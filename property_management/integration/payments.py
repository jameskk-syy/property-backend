# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 3 - Payments bridge.

Tenant rent/utility payments become native ERPNext Payment Entries (type Receive)
allocated against the property's Sales Invoice. This posts to the GL and, unlike the
legacy Property Payment, native Payment Entry cancellation reverses the GL cleanly.
"""

import contextlib

import frappe
from frappe.utils import flt, nowdate

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


def _mode_of_payment(payment_method: str | None) -> str | None:
	"""Map the property payment_method to an ERPNext Mode of Payment, creating if needed."""
	label = (payment_method or "Cash").strip()
	# Normalise a couple of common ones.
	if label.lower() in ("mpesa", "m-pesa"):
		label = "M-Pesa"
	if not frappe.db.exists("Mode of Payment", label):
		frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": label, "type": "Cash"}).insert(
			ignore_permissions=True
		)
	return label


def _paid_to_account(company: str) -> str:
	"""Default bank/cash account money is received into."""
	return (
		setup.get_default_account(company, "Bank")
		or setup.get_default_account(company, "Cash")
	)


def create_payment_entry(
	property_name: str,
	amount: float,
	sales_invoice: str | None = None,
	tenant: str | None = None,
	payment_method: str | None = "M-Pesa",
	reference_no: str | None = None,
	posting_date: str | None = None,
	submit: bool = True,
) -> str:
	"""
	Create (and optionally submit) a Payment Entry (Receive) for a tenant payment.
	Allocates against `sales_invoice` when provided. Returns the Payment Entry name.
	"""
	prop = frappe.get_doc("Property", property_name)
	company = require_company(prop.organization)
	setup.bootstrap_global_masters()

	cost_center = prop.get("cost_center") or setup.provision_property_cost_center(property_name)
	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	paid_to = _paid_to_account(company)
	receivable = setup.get_default_account(company, "Receivable")

	# Resolve the customer from the sales invoice or the tenant.
	customer = None
	if sales_invoice:
		customer = frappe.db.get_value("Sales Invoice", sales_invoice, "customer")
	if not customer and tenant:
		from property_management.integration.invoicing import ensure_customer
		customer = ensure_customer(tenant, company)
	if not customer:
		frappe.throw("Cannot resolve customer for payment (need sales_invoice or tenant).")

	pe = frappe.get_doc({
		"doctype": "Payment Entry",
		"payment_type": "Receive",
		"company": company,
		"posting_date": posting_date or nowdate(),
		"mode_of_payment": _mode_of_payment(payment_method),
		"party_type": "Customer",
		"party": customer,
		"paid_from": receivable,
		"paid_to": paid_to,
		"paid_amount": flt(amount),
		"received_amount": flt(amount),
		"paid_from_account_currency": company_currency,
		"paid_to_account_currency": company_currency,
		"source_exchange_rate": 1.0,
		"target_exchange_rate": 1.0,
		"reference_no": reference_no or "",
		"reference_date": posting_date or nowdate(),
		"cost_center": cost_center,
		"property_ref": property_name,
	})

	# Allocate against the invoice when supplied.
	if sales_invoice:
		outstanding = flt(frappe.db.get_value("Sales Invoice", sales_invoice, "outstanding_amount"))
		allocated = min(flt(amount), outstanding) if outstanding else flt(amount)
		pe.append("references", {
			"reference_doctype": "Sales Invoice",
			"reference_name": sales_invoice,
			"allocated_amount": allocated,
		})

	pe.flags.ignore_mandatory = True
	pe.insert(ignore_permissions=True)
	if submit:
		pe.submit()

	frappe.db.commit()
	return pe.name


def post_onboarding_payment(property_name, rent_amount, deposit_amount, reference_no=None,
							posting_date=None):
	"""
	Post the onboarding rent+deposit payment as a Journal Entry:
	  Debit  Bank/Cash          (rent + deposit)      -- money received
	  Credit Rent Income        (rent)                -- revenue
	  Credit Tenant Deposits    (deposit)             -- refundable liability

	Deposit is intentionally NOT income; it's a liability we owe back. Returns the
	Journal Entry name.
	"""
	prop = frappe.get_doc("Property", property_name)
	company = require_company(prop.organization)
	setup.bootstrap_global_masters()
	cost_center = prop.get("cost_center") or setup.provision_property_cost_center(property_name)

	bank = _paid_to_account(company)
	rent_income = setup.get_income_account(company, "Rent")
	deposit_liab = setup.ensure_deposit_liability_account(company)

	rent_amount = flt(rent_amount)
	deposit_amount = flt(deposit_amount)
	total = rent_amount + deposit_amount
	if total <= 0:
		frappe.throw("Onboarding payment amount must be greater than zero.")

	accounts = [{
		"account": bank, "debit_in_account_currency": total, "credit_in_account_currency": 0,
		"cost_center": cost_center,
	}]
	if rent_amount > 0 and rent_income:
		accounts.append({
			"account": rent_income, "debit_in_account_currency": 0,
			"credit_in_account_currency": rent_amount, "cost_center": cost_center,
		})
	if deposit_amount > 0 and deposit_liab:
		accounts.append({
			"account": deposit_liab, "debit_in_account_currency": 0,
			"credit_in_account_currency": deposit_amount, "cost_center": cost_center,
		})

	je = frappe.get_doc({
		"doctype": "Journal Entry",
		"voucher_type": "Journal Entry",
		"company": company,
		"posting_date": posting_date or nowdate(),
		"cheque_no": reference_no or "",
		"cheque_date": posting_date or nowdate(),
		"user_remark": f"Onboarding rent+deposit for {property_name} (ref {reference_no or '-'})",
		"accounts": accounts,
	})
	je.flags.ignore_mandatory = True
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	return je.name


@frappe.whitelist()
def create_payment_for_property_payment(property_payment: str, submit: bool = True) -> str:
	"""Bridge an existing Property Payment doc into a Payment Entry."""
	pp = frappe.get_doc("Property Payment", property_payment)

	sales_invoice = None
	if pp.invoice:
		sales_invoice = frappe.db.get_value("Property Invoice", pp.invoice, "sales_invoice")

	with _as_system_user():
		pe_name = create_payment_entry(
			property_name=pp.property,
			amount=pp.amount_paid,
			sales_invoice=sales_invoice,
			tenant=pp.tenant,
			payment_method=pp.payment_method,
			reference_no=pp.transaction_reference,
			posting_date=pp.payment_date,
			submit=submit,
		)
	return pe_name
