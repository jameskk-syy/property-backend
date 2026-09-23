# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Accounting: Journal Entries + opening balances, backed by native
ERPNext Journal Entry / Account. Everything is scoped to the organization's
Company (and optionally a property cost center) for multi-tenant isolation.
"""

import frappe
from frappe.utils import flt, nowdate, getdate

from property_management.api.v2 import envelope


def _company_and_cost_center(company=None, property=None):
	cost_center = None
	if property:
		cost_center = frappe.db.get_value("Property", property, "cost_center")
		if not company:
			org = frappe.db.get_value("Property", property, "organization")
			company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
	if not company:
		from property_management.api.utils import resolve_organization
		org = resolve_organization()
		company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
	return company, cost_center


@frappe.whitelist()
@envelope
def list_accounts(company=None, property=None, root_type=None):
	"""
	List non-group accounts for a company (for JE / opening-balance pickers).
	Optionally filter by root_type (Asset/Liability/Income/Expense/Equity).
	"""
	company, _cc = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")
	filters = {"company": company, "is_group": 0}
	if root_type:
		filters["root_type"] = root_type
	return frappe.get_all(
		"Account", filters=filters,
		fields=["name", "account_name", "root_type", "account_type"],
		order_by="lft asc",
	)


@frappe.whitelist()
@envelope
def list_journal_entries(company=None, property=None, from_date=None, to_date=None, limit=50):
	"""List submitted/draft Journal Entries for a company, with their line accounts."""
	company, _cc = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")

	filters = {"company": company, "docstatus": ["<", 2]}
	if from_date and to_date:
		filters["posting_date"] = ["between", [str(from_date), str(to_date)]]

	entries = frappe.get_all(
		"Journal Entry", filters=filters,
		fields=["name", "posting_date", "total_debit", "total_credit", "user_remark",
				"is_opening", "docstatus", "voucher_type"],
		order_by="posting_date desc, creation desc", limit=int(limit),
	)
	for e in entries:
		e["accounts"] = frappe.get_all(
			"Journal Entry Account", filters={"parent": e.name},
			fields=["account", "debit_in_account_currency as debit", "credit_in_account_currency as credit"],
		)
		e["status"] = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(e.docstatus, "")
	return entries


def _post_journal_entry(company, lines, posting_date=None, remark=None, is_opening=0, cost_center=None):
	"""
	Core JE poster. lines = [{account, debit, credit}]. Validates balance, then
	creates + submits a native Journal Entry. Returns the JE name.
	"""
	total_debit = sum(flt(l.get("debit")) for l in lines)
	total_credit = sum(flt(l.get("credit")) for l in lines)
	if round(total_debit, 2) != round(total_credit, 2):
		frappe.throw(f"Journal Entry is not balanced: debit {total_debit} != credit {total_credit}")
	if total_debit <= 0:
		frappe.throw("Journal Entry has no amounts.")

	accounts = []
	for l in lines:
		row = {
			"account": l["account"],
			"debit_in_account_currency": flt(l.get("debit")),
			"credit_in_account_currency": flt(l.get("credit")),
		}
		cc = l.get("cost_center") or cost_center
		if cc:
			row["cost_center"] = cc
		accounts.append(row)

	je = frappe.get_doc({
		"doctype": "Journal Entry",
		"voucher_type": "Opening Entry" if is_opening else "Journal Entry",
		"company": company,
		"posting_date": posting_date or nowdate(),
		"is_opening": "Yes" if is_opening else "No",
		"user_remark": remark or "",
		"accounts": accounts,
	})
	je.flags.ignore_mandatory = True
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	return je.name


@frappe.whitelist()
@envelope
def create_journal_entry(lines, company=None, property=None, posting_date=None, remark=None):
	"""
	Create + submit a balanced Journal Entry. `lines` is a JSON list of
	{account, debit, credit, cost_center?}.
	"""
	if isinstance(lines, str):
		lines = frappe.parse_json(lines)
	company, cost_center = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")
	name = _post_journal_entry(company, lines, posting_date=posting_date, remark=remark,
							   is_opening=0, cost_center=cost_center)
	return {"journal_entry": name}


def _temporary_opening_account(company):
	"""
	Return (creating if needed) an EQUITY 'Opening Balance Equity' account for the
	company, used as the balancing side of opening balances.

	Important: ERPNext ships a stock 'Temporary Opening' account under ASSETS
	(account_type=Temporary). Using that as the opening offset makes opening
	balances cancel out of Assets instead of appearing as Equity, which breaks the
	Balance Sheet. So we deliberately DO NOT reuse a non-Equity account — we use a
	dedicated Equity account instead.
	"""
	abbr = frappe.get_cached_value("Company", company, "abbr")

	# Reuse an existing Equity-classified opening account if one already exists.
	for acct_name in ("Opening Balance Equity", "Temporary Opening"):
		existing = frappe.db.get_value(
			"Account",
			{"company": company, "account_name": acct_name, "is_group": 0, "root_type": "Equity"},
			"name",
		)
		if existing:
			return existing

	# Otherwise create a proper Equity account under the Equity root group.
	name = f"Opening Balance Equity - {abbr}"
	if frappe.db.exists("Account", name):
		return name
	parent = frappe.db.get_value(
		"Account", {"company": company, "is_group": 1, "root_type": "Equity"}, "name", order_by="lft asc"
	) or frappe.db.get_value("Account", {"company": company, "is_group": 1}, "name", order_by="lft asc")
	frappe.get_doc({
		"doctype": "Account", "account_name": "Opening Balance Equity", "parent_account": parent,
		"company": company, "root_type": "Equity", "is_group": 0,
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return name


@frappe.whitelist()
@envelope
def create_opening_balance(balances, company=None, property=None, posting_date=None):
	"""
	Set opening balances via an Opening Journal Entry. `balances` is a JSON list of
	{account, debit?, credit?} — the amount you want as the account's opening
	balance on the correct side. The balancing side is posted to 'Temporary
	Opening'. Dated at the fiscal-year start unless posting_date is given.
	"""
	if isinstance(balances, str):
		balances = frappe.parse_json(balances)
	company, cost_center = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")

	lines = []
	total_debit = total_credit = 0.0
	for b in (balances or []):
		debit = flt(b.get("debit"))
		credit = flt(b.get("credit"))
		if debit == 0 and credit == 0:
			continue
		lines.append({"account": b["account"], "debit": debit, "credit": credit, "cost_center": b.get("cost_center") or cost_center})
		total_debit += debit
		total_credit += credit

	if not lines:
		frappe.throw("Provide at least one opening balance amount.")

	# Balance against Temporary Opening.
	temp = _temporary_opening_account(company)
	diff = round(total_debit - total_credit, 2)
	if diff > 0:
		lines.append({"account": temp, "debit": 0.0, "credit": diff})
	elif diff < 0:
		lines.append({"account": temp, "debit": abs(diff), "credit": 0.0})

	if not posting_date:
		# Fiscal year start (Jan 1 of current year on a Kenya Jan-Dec FY).
		posting_date = f"{getdate(nowdate()).year}-01-01"

	name = _post_journal_entry(company, lines, posting_date=posting_date,
							   remark="Opening Balance", is_opening=1, cost_center=cost_center)
	return {"journal_entry": name, "temporary_opening": temp, "posting_date": posting_date}
