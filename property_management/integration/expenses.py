# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 4 - Expenses & vendors bridge.

Approved property expenses and construction purchases post as native ERPNext
Purchase Invoices, tagged with the property's Cost Center (and Project for
construction). Vendors map to Suppliers.

Maker-checker is enforced upstream in the doctype .approve() methods (a creator
cannot approve their own request). This module only runs *after* that check passes.
"""

import contextlib

import frappe
from frappe.utils import flt, nowdate

from property_management.integration import erpnext_setup as setup
from property_management.integration.settings import require_company


@contextlib.contextmanager
def _as_system_user():
	"""
	Temporarily run as Administrator so native ERPNext postings (which require
	account read permissions) succeed. The domain-level maker-checker check has
	already been enforced by the caller before reaching here.
	"""
	original = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(original)


def ensure_supplier(vendor_name: str, company: str) -> str:
	"""Return an ERPNext Supplier for a vendor name, creating it if needed."""
	if not vendor_name:
		vendor_name = "General Vendor"

	if frappe.db.exists("Supplier", vendor_name):
		return vendor_name

	setup.bootstrap_global_masters()
	supplier_group = frappe.db.get_value("Supplier Group", "Vendors", "name") or frappe.db.get_value(
		"Supplier Group", {"is_group": 0}, "name"
	)
	frappe.get_doc({
		"doctype": "Supplier",
		"supplier_name": vendor_name,
		"supplier_group": supplier_group,
		"supplier_type": "Company",
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return vendor_name


def create_purchase_invoice(
	property_name: str,
	supplier_name: str,
	amount: float,
	description: str,
	expense_account: str | None = None,
	project: str | None = None,
	property_expense_ref: str | None = None,
	posting_date: str | None = None,
	submit: bool = True,
) -> str:
	"""
	Create (and optionally submit) a Purchase Invoice for an expense/purchase.
	Uses an expense-account line (no stock item). Returns the PI name.
	"""
	# num2words (via *_in_words) needs a valid locale; unset in some non-request
	# contexts. Default to English so PI submit never fails on language.
	if not getattr(frappe.local, "lang", None):
		frappe.local.lang = "en"

	prop = frappe.get_doc("Property", property_name) if property_name else None
	organization = prop.organization if prop else None
	company = require_company(organization) if organization else _fallback_company()
	setup.bootstrap_global_masters()

	cost_center = None
	if prop:
		cost_center = prop.get("cost_center") or setup.provision_property_cost_center(property_name)

	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	supplier = ensure_supplier(supplier_name, company)
	expense_account = expense_account or setup.get_default_account(company, "Expense Account")
	payable = setup.get_default_account(company, "Payable")

	pi = frappe.get_doc({
		"doctype": "Purchase Invoice",
		"company": company,
		"supplier": supplier,
		"currency": company_currency,
		"conversion_rate": 1.0,
		"posting_date": posting_date or nowdate(),
		"credit_to": payable,
		"cost_center": cost_center,
		"project": project,
		"property_ref": property_name,
		"property_expense_ref": property_expense_ref or "",
		"update_stock": 0,
		"items": [{
			"item_name": (description or "Expense")[:140],
			"description": description or "Expense",
			"qty": 1,
			"rate": flt(amount),
			"expense_account": expense_account,
			"cost_center": cost_center,
			"project": project,
		}],
	})
	pi.flags.ignore_mandatory = True
	pi.insert(ignore_permissions=True)
	if submit:
		pi.submit()

	frappe.db.commit()
	return pi.name


def _fallback_company() -> str:
	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		frappe.throw("No ERPNext Company exists; provision one first.")
	return company


def pay_purchase_invoice(purchase_invoice: str, paid_from: str = None, reference_no: str = None) -> str | None:
	"""
	Pay a submitted Purchase Invoice IN FULL via a native Payment Entry, drawing
	from `paid_from` (defaults to the company Cash account). Reconciles against the
	PI so its outstanding drops to 0 and status becomes 'Paid'. Returns the PE name.
	"""
	if not purchase_invoice or not frappe.db.exists("Purchase Invoice", purchase_invoice):
		return None
	pi = frappe.get_doc("Purchase Invoice", purchase_invoice)
	if pi.docstatus != 1 or flt(pi.outstanding_amount) <= 0:
		return None

	company = pi.company
	if not paid_from:
		paid_from = setup.get_default_account(company, "Cash") or setup.get_default_account(company, "Bank")
	if not paid_from:
		return None

	# Ensure a language is set: Payment Entry -> set_total_in_words -> num2words
	# needs a valid locale, which can be unset in non-request contexts.
	if not getattr(frappe.local, "lang", None):
		frappe.local.lang = "en"

	with _as_system_user():
		if not getattr(frappe.local, "lang", None):
			frappe.local.lang = "en"
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
		pe = get_payment_entry("Purchase Invoice", pi.name)
		pe.paid_from = paid_from
		if reference_no:
			pe.reference_no = reference_no
			pe.reference_date = nowdate()
		pe.flags.ignore_permissions = True
		pe.flags.ignore_mandatory = True
		pe.insert(ignore_permissions=True)
		pe.submit()
		frappe.db.commit()
		return pe.name


@frappe.whitelist()
def post_property_expense(property_expense: str, submit: bool = True) -> str:
	"""Bridge an approved Property Expense into a Purchase Invoice.

	The expense's category drives which expense account is debited: it is resolved
	(and created if needed) within the property's Organization Company, so the
	category behaves as a per-tenant accounting head.
	"""
	exp = frappe.get_doc("Property Expense", property_expense)
	with _as_system_user():
		# Resolve the category's expense account inside the property's company.
		expense_account = None
		if exp.property and exp.get("expense_category"):
			prop = frappe.get_doc("Property", exp.property)
			if prop.organization:
				company = require_company(prop.organization)
				expense_account = setup.get_expense_account_for_category(company, exp.expense_category)

		return create_purchase_invoice(
			property_name=exp.property,
			supplier_name=exp.vendor_name or exp.get("vendor_name_manual") or "General Vendor",
			amount=exp.amount,
			description=exp.work_description or f"Expense {exp.name}",
			expense_account=expense_account,
			property_expense_ref=exp.name,
			posting_date=getattr(exp, "approved_at", None) or nowdate(),
			submit=submit,
		)


@frappe.whitelist()
def post_construction_purchase(construction_purchase: str, submit: bool = True) -> str:
	"""Bridge an approved Construction Purchase into a Purchase Invoice tagged to the Project."""
	cp = frappe.get_doc("Construction Purchase", construction_purchase)

	project = None
	if cp.project:
		project = frappe.db.get_value("Construction Project", cp.project, "erpnext_project")

	# Resolve the real vendor name from the Expense Vendor link (the doctype has a
	# `vendor` link field, not `vendor_name`), so the PI supplier is the actual
	# vendor rather than a generic fallback.
	vendor_display = None
	if cp.get("vendor"):
		vendor_display = frappe.db.get_value("Expense Vendor", cp.vendor, "vendor_name") or cp.vendor

	with _as_system_user():
		return create_purchase_invoice(
			property_name=cp.property,
			supplier_name=vendor_display or "General Vendor",
			amount=cp.amount,
			description=getattr(cp, "item_description", None) or f"Construction Purchase {cp.name}",
			project=project,
			posting_date=getattr(cp, "approved_at", None) or nowdate(),
			submit=submit,
		)
