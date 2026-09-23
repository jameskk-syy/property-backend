# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Reports, sourced from native ERPNext GL / documents.

profit_and_loss and trial_balance read GL Entry filtered by company + property
cost center, so figures come from the real ledger (not recomputed custom tables).
"""

import frappe
from frappe.utils import flt, nowdate

from property_management.api.v2 import envelope


def _company_and_cost_center(company=None, property=None):
	cost_center = None
	if property:
		cost_center = frappe.db.get_value("Property", property, "cost_center")
		if not company:
			org = frappe.db.get_value("Property", property, "organization")
			company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
	# Whole-company view (no property): fall back to the resolved organization's
	# company, then the site's single company, so reports work without an explicit
	# company argument.
	if not company:
		try:
			from property_management.api.utils import resolve_organization
			org = resolve_organization()
			company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
		except Exception:
			company = None
	if not company:
		companies = frappe.get_all("Company", pluck="name", limit=1)
		company = companies[0] if companies else None
	return company, cost_center


@frappe.whitelist()
@envelope
def profit_and_loss(company=None, property=None, from_date=None, to_date=None):
	"""
	P&L from GL Entry: income vs expense, optionally scoped to a property cost center.
	"""
	company, cost_center = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")

	filters = {"company": company, "is_cancelled": 0}
	if cost_center:
		filters["cost_center"] = cost_center
	if from_date and to_date:
		filters["posting_date"] = ["between", [from_date, to_date]]

	rows = frappe.get_all(
		"GL Entry", filters=filters,
		fields=["account", "debit", "credit"],
	)

	income = expense = 0.0
	income_by = {}
	expense_by = {}
	for r in rows:
		root = frappe.get_cached_value("Account", r.account, "root_type")
		if root == "Income":
			amt = flt(r.credit) - flt(r.debit)
			income += amt
			income_by[r.account] = income_by.get(r.account, 0.0) + amt
		elif root == "Expense":
			amt = flt(r.debit) - flt(r.credit)
			expense += amt
			expense_by[r.account] = expense_by.get(r.account, 0.0) + amt

	return {
		"company": company,
		"property": property,
		"cost_center": cost_center,
		"from_date": from_date,
		"to_date": to_date,
		"total_income": income,
		"total_expense": expense,
		"net_profit": income - expense,
		"income_breakdown": income_by,
		"expense_breakdown": expense_by,
	}


@frappe.whitelist()
@envelope
def trial_balance(company=None, property=None, from_date=None, to_date=None):
	"""Debit/credit balance per account from GL Entry."""
	company, cost_center = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")

	filters = {"company": company, "is_cancelled": 0}
	if cost_center:
		filters["cost_center"] = cost_center
	if from_date and to_date:
		filters["posting_date"] = ["between", [from_date, to_date]]

	rows = frappe.get_all("GL Entry", filters=filters, fields=["account", "debit", "credit"])
	summary = {}
	for r in rows:
		s = summary.setdefault(r.account, {"account": r.account, "debit": 0.0, "credit": 0.0})
		s["debit"] += flt(r.debit)
		s["credit"] += flt(r.credit)
	# Enrich with account name + type for display.
	for acc, s in summary.items():
		meta = frappe.db.get_value("Account", acc, ["account_name", "root_type", "account_type"], as_dict=True) or {}
		s["account_name"] = meta.get("account_name") or acc
		s["account_type"] = meta.get("root_type") or ""
		s["balance"] = s["debit"] - s["credit"]
	return sorted(summary.values(), key=lambda x: x["account"])


@frappe.whitelist()
@envelope
def balance_sheet(company=None, property=None, from_date=None, to_date=None):
	"""
	Balance Sheet from GL: Assets, Liabilities and Equity balances (optionally
	scoped to a property cost center). Assets are debit-positive; Liabilities and
	Equity are credit-positive. Returns grouped accounts + section totals and the
	accounting check (assets == liabilities + equity + net profit).
	"""
	company, cost_center = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")

	filters = {"company": company, "is_cancelled": 0}
	if cost_center:
		filters["cost_center"] = cost_center
	if from_date and to_date:
		filters["posting_date"] = ["between", [str(from_date), str(to_date)]]

	rows = frappe.get_all("GL Entry", filters=filters, fields=["account", "debit", "credit"])

	# Aggregate per account, then classify by root_type.
	per_acc = {}
	for r in rows:
		per_acc.setdefault(r.account, {"debit": 0.0, "credit": 0.0})
		per_acc[r.account]["debit"] += flt(r.debit)
		per_acc[r.account]["credit"] += flt(r.credit)

	sections = {"Asset": [], "Liability": [], "Equity": []}
	totals = {"Asset": 0.0, "Liability": 0.0, "Equity": 0.0, "Income": 0.0, "Expense": 0.0}
	for acc, v in per_acc.items():
		meta = frappe.db.get_value("Account", acc, ["account_name", "root_type"], as_dict=True) or {}
		rt = meta.get("root_type")
		# Debit-positive for Assets and Expenses; credit-positive for Liabilities,
		# Equity and Income. (Expense is debit-positive so net profit = Income -
		# Expense comes out with the correct sign.)
		if rt in ("Asset", "Expense"):
			bal = v["debit"] - v["credit"]
		else:
			bal = v["credit"] - v["debit"]
		if rt in totals:
			totals[rt] += bal
		if rt in sections and abs(bal) > 0.0001:
			sections[rt].append({"account": acc, "account_name": meta.get("account_name") or acc, "balance": bal})

	# Net profit (Income - Expense) rolls into equity on the balance sheet.
	net_profit = totals["Income"] - totals["Expense"]
	equity_total = totals["Equity"] + net_profit

	for k in sections:
		sections[k].sort(key=lambda x: x["account"])

	return {
		"company": company,
		"property": property,
		"cost_center": cost_center,
		"from_date": from_date,
		"to_date": to_date,
		"assets": sections["Asset"],
		"liabilities": sections["Liability"],
		"equity": sections["Equity"],
		"total_assets": totals["Asset"],
		"total_liabilities": totals["Liability"],
		"total_equity": equity_total,
		"net_profit": net_profit,
		"balanced": abs(totals["Asset"] - (totals["Liability"] + equity_total)) < 0.01,
	}


@frappe.whitelist()
@envelope
def construction_cost(construction_project):
	"""True cost of a construction project (materials + labour + other), from ERPNext."""
	from property_management.integration.construction import construction_cost as _cc
	return _cc(construction_project)


@frappe.whitelist()
@envelope
def landlord_remittance(company=None, property=None, from_date=None, to_date=None, commission_rate=10.0):
	"""
	Net landlord remittance = collected rent - management commission - approved expenses,
	computed from native Payment Entries (collections) and Purchase Invoices (expenses).
	"""
	company, cost_center = _company_and_cost_center(company, property)
	if not company:
		frappe.throw("company or property is required")

	pe_filters = {"company": company, "payment_type": "Receive", "docstatus": 1}
	if property:
		pe_filters["property_ref"] = property
	if from_date and to_date:
		pe_filters["posting_date"] = ["between", [from_date, to_date]]
	collections = sum(
		flt(p.paid_amount) for p in frappe.get_all("Payment Entry", filters=pe_filters, fields=["paid_amount"])
	)

	pi_filters = {"company": company, "docstatus": 1}
	if property:
		pi_filters["property_ref"] = property
	if from_date and to_date:
		pi_filters["posting_date"] = ["between", [from_date, to_date]]
	expenses = sum(
		flt(p.grand_total) for p in frappe.get_all("Purchase Invoice", filters=pi_filters, fields=["grand_total"])
	)

	commission = collections * (flt(commission_rate) / 100.0)
	net = max(0.0, collections - commission - expenses)
	return {
		"company": company,
		"property": property,
		"gross_collected": collections,
		"commission_rate": commission_rate,
		"management_commission": commission,
		"expenses": expenses,
		"net_remittance": net,
	}
