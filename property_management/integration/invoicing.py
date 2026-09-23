# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 2 - Invoicing bridge.

Creates native ERPNext Sales Invoices from property-domain invoice data. Rent vs
utility revenue is routed to the correct income account and every invoice is tagged
with the property's Cost Center + property_ref so reports slice by property.

Unlike the legacy custom Property Invoice (which never posted to the ledger), a
submitted Sales Invoice posts to the GL automatically.
"""

import contextlib

import frappe
from frappe.utils import flt, nowdate, add_days

from property_management.integration import erpnext_setup as setup
from property_management.integration.settings import require_company


@contextlib.contextmanager
def _as_system_user():
	"""Run native ERPNext postings as Administrator (account-read permissions)."""
	original = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(original)


def ensure_customer(tenant: str, company: str) -> str:
	"""Return an ERPNext Customer for a Property Tenant, creating it if needed."""
	if not tenant:
		frappe.throw("Tenant is required to create an invoice.")

	# Reuse a Customer named after the tenant.
	tenant_name = frappe.db.get_value("Property Tenant", tenant, "tenant_name") or tenant
	customer_name = tenant_name

	if frappe.db.exists("Customer", customer_name):
		return customer_name

	frappe.get_doc({
		"doctype": "Customer",
		"customer_name": customer_name,
		"customer_type": "Individual",
		"customer_group": _default_customer_group(),
		"territory": _default_territory(),
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return customer_name


def _default_customer_group() -> str:
	return (
		frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		or "All Customer Groups"
	)


def _default_territory() -> str:
	return frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"


def create_sales_invoice(
	property_name: str,
	tenant: str,
	items: list[dict],
	invoice_type: str = "Rent",
	posting_date: str | None = None,
	due_date: str | None = None,
	property_invoice_ref: str | None = None,
	submit: bool = True,
) -> str:
	"""
	Build (and optionally submit) a Sales Invoice.

	items: list of {"item_name", "description"?, "quantity", "rate"}.
	Returns the Sales Invoice name.
	"""
	prop = frappe.get_doc("Property", property_name)
	company = require_company(prop.organization)

	# Ensure global masters (fiscal year, groups, uom) and accounting scaffolding exist.
	setup.bootstrap_global_masters()
	cost_center = prop.get("cost_center") or setup.provision_property_cost_center(property_name)
	income_account = setup.get_income_account(company, invoice_type)
	receivable = setup.get_default_account(company, "Receivable")
	customer = ensure_customer(tenant, company)

	posting_date = posting_date or nowdate()
	due_date = due_date or add_days(posting_date, 30)

	si_items = []
	for it in (items or []):
		si_items.append({
			"item_name": it.get("item_name") or invoice_type,
			"description": it.get("description") or it.get("item_name") or invoice_type,
			"qty": flt(it.get("quantity") or 1),
			"rate": flt(it.get("rate") or 0),
			"income_account": income_account,
			"cost_center": cost_center,
		})

	company_currency = frappe.get_cached_value("Company", company, "default_currency")

	si = frappe.get_doc({
		"doctype": "Sales Invoice",
		"company": company,
		"customer": customer,
		"currency": company_currency,
		"conversion_rate": 1.0,
		"posting_date": posting_date,
		"due_date": due_date,
		"debit_to": receivable,
		"cost_center": cost_center,
		"property_ref": property_name,
		"property_invoice_ref": property_invoice_ref or "",
		"update_stock": 0,
		"items": si_items,
	})
	# Allow items without a linked Item master (services like rent/utility).
	si.flags.ignore_mandatory = True
	si.insert(ignore_permissions=True)

	if submit:
		si.submit()

	frappe.db.commit()
	return si.name


@frappe.whitelist()
def create_invoice_for_property_invoice(property_invoice: str, submit: bool = True) -> str:
	"""
	Bridge an existing Property Invoice doc into a Sales Invoice. Stores the SI name
	back on the Property Invoice (custom link handled by caller/field).
	"""
	pi = frappe.get_doc("Property Invoice", property_invoice)

	items = [
		{
			"item_name": row.item_name,
			"description": row.description,
			"quantity": row.quantity,
			"rate": row.rate,
		}
		for row in pi.get("items", [])
	]

	with _as_system_user():
		si_name = create_sales_invoice(
			property_name=pi.property,
			tenant=pi.tenant,
			items=items,
			invoice_type=pi.invoice_type,
			posting_date=pi.posting_date,
			due_date=pi.due_date,
			property_invoice_ref=pi.name,
			submit=submit,
		)
	return si_name
