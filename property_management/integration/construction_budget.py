# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Construction budget funding & spend control.

Each Construction Project has a ring-fenced ASSET account, "Construction Budget
- <project>", that behaves like a project wallet:

  - FUND it by transferring from a Bank/Cash account:
        Dr  Construction Budget (asset)      <amount>
        Cr  Bank / Cash (asset)              <amount>
    (a balanced Journal Entry) — this is the AVAILABLE cash for the project.

  - SPEND draws down against it. When a material purchase is approved, its
    Purchase Invoice credits this budget account (instead of generic payables),
    so the budget-account balance = money still available.

Approval is capped against actually-funded cash (funded - spent >= amount), so a
project can never spend money that hasn't been transferred in.
"""

import contextlib

import frappe
from frappe.utils import flt, nowdate

from property_management.integration import erpnext_setup as setup
from property_management.integration.settings import require_company


@contextlib.contextmanager
def _as_system_user():
	original = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(original)


def _company(construction_project):
	cp = frappe.get_doc("Construction Project", construction_project)
	return require_company(cp.organization), cp


def ensure_budget_account(construction_project: str) -> str:
	"""
	Create (or return) the ring-fenced ASSET account for a project's construction
	budget, under the company's Current Assets group. Idempotent; linked back on
	the Construction Project via `budget_account`.
	"""
	company, cp = _company(construction_project)
	if cp.get("budget_account") and frappe.db.exists("Account", cp.get("budget_account")):
		return cp.get("budget_account")

	abbr = frappe.get_cached_value("Company", company, "abbr")
	label = f"Construction Budget - {cp.project_name}"
	acc_name = f"{label} - {abbr}"
	if frappe.db.exists("Account", acc_name):
		if cp.get("budget_account") != acc_name:
			cp.db_set("budget_account", acc_name, update_modified=False)
		return acc_name

	# Parent = Current Assets (fall back to the Asset root group).
	parent = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Asset",
		 "account_name": ["in", ["Current Assets", "Bank Accounts", "Cash In Hand"]]},
		"name",
	) or frappe.db.get_value(
		"Account", {"company": company, "is_group": 1, "root_type": "Asset"},
		"name", order_by="lft asc",
	)

	with _as_system_user():
		acc = frappe.get_doc({
			"doctype": "Account",
			"account_name": label,
			"parent_account": parent,
			"company": company,
			"root_type": "Asset",
			# Plain asset control account (NOT typed Bank/Cash) so it never appears
			# as a funding SOURCE and can't self-fund; no party required on GL lines.
			"account_type": "",
			"is_group": 0,
		})
		acc.flags.ignore_mandatory = True
		acc.insert(ignore_permissions=True)

	cp.db_set("budget_account", acc.name, update_modified=False)
	frappe.db.commit()
	return acc.name


def fund_project(construction_project: str, source_account: str, amount: float,
				 posting_date: str = None, remark: str = None) -> str:
	"""
	Transfer cash into the project's budget account from a Bank/Cash account.
	Posts a balanced Journal Entry (Dr budget, Cr source). Returns the JE name.
	"""
	amount = flt(amount)
	if amount <= 0:
		frappe.throw("Funding amount must be greater than zero.")

	company, cp = _company(construction_project)
	budget_acc = ensure_budget_account(construction_project)
	if not source_account or not frappe.db.exists("Account", source_account):
		frappe.throw("A valid source (Bank/Cash) account is required.")

	from property_management.api.v2.accounting import _post_journal_entry
	lines = [
		{"account": budget_acc, "debit": amount, "credit": 0.0},
		{"account": source_account, "debit": 0.0, "credit": amount},
	]
	with _as_system_user():
		je = _post_journal_entry(
			company, lines, posting_date=posting_date or nowdate(),
			remark=remark or f"Fund construction project: {cp.project_name}",
		)
	return je


def funded_amount(construction_project: str) -> float:
	"""Total transferred INTO the budget account (sum of debits)."""
	budget_acc = frappe.db.get_value("Construction Project", construction_project, "budget_account")
	if not budget_acc:
		return 0.0
	rows = frappe.get_all(
		"GL Entry",
		filters={"account": budget_acc, "is_cancelled": 0},
		fields=["sum(debit) as d", "sum(credit) as c"],
	)
	if not rows:
		return 0.0
	# Debits = money in; credits = money drawn down. Funded = gross debits.
	return flt(rows[0].d)


def spent_amount(construction_project: str) -> float:
	"""Total spent = sum of APPROVED construction purchases for the project."""
	rows = frappe.get_all(
		"Construction Purchase",
		filters={"project": construction_project, "status": "Approved"},
		fields=["sum(amount) as s"],
	)
	return flt(rows[0].s) if rows and rows[0].s else 0.0


def available_amount(construction_project: str) -> float:
	"""Cash still available to spend = funded - spent (never negative)."""
	return max(0.0, funded_amount(construction_project) - spent_amount(construction_project))


def assert_within_budget(construction_project: str, amount: float):
	"""Raise unless the project has enough FUNDED cash left for `amount`."""
	amount = flt(amount)
	available = available_amount(construction_project)
	if amount > available + 0.001:
		frappe.throw(
			f"This purchase (KSh {amount:,.0f}) exceeds the funded budget still "
			f"available (KSh {available:,.0f}). Transfer more funds into the "
			f"project budget first.",
			frappe.ValidationError,
		)


def pay_purchase_invoice_from_budget(construction_project: str, purchase_invoice: str) -> str | None:
	"""
	Pay a submitted Purchase Invoice IN FULL from the project's budget wallet by
	recording a native Payment Entry (paid FROM the Construction Budget account).

	This is the correct settlement: it reconciles against the PI so its
	outstanding drops to 0 and its status becomes 'Paid', AND the cash leaves the
	project wallet (available = funded - spent). Returns the Payment Entry name.
	"""
	if not purchase_invoice or not frappe.db.exists("Purchase Invoice", purchase_invoice):
		return None
	company, cp = _company(construction_project)
	budget_acc = cp.get("budget_account")
	if not budget_acc:
		return None

	# Delegate to the shared PI payer, drawing from the project's budget wallet.
	from property_management.integration.expenses import pay_purchase_invoice
	return pay_purchase_invoice(purchase_invoice, paid_from=budget_acc,
								reference_no=f"CONSTRUCTION-{cp.name}")


def budget_summary(construction_project: str) -> dict:
	"""Funded / spent / available snapshot for a project."""
	funded = funded_amount(construction_project)
	spent = spent_amount(construction_project)
	return {
		"budget_account": frappe.db.get_value("Construction Project", construction_project, "budget_account"),
		"funded": funded,
		"spent": spent,
		"available": max(0.0, funded - spent),
	}
