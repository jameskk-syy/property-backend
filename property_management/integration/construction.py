# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 6 - Construction costing.

Maps a Construction Project to a native ERPNext Project (tagged Company + Property).
All construction costs attach to the ERPNext Project:
  - Material/input purchases  -> Purchase Invoice (project set) [via expenses bridge]
  - Worker wages              -> Salary Slip / Journal Entry (project set)
  - Other expenses            -> Purchase Invoice / Journal Entry (project set)

construction_cost() rolls these up into budget-vs-actual + total cost per project.
"""

import contextlib

import frappe
from frappe.utils import flt

from property_management.integration import erpnext_setup as setup
from property_management.integration.settings import require_company, is_enabled


@contextlib.contextmanager
def _as_system_user():
	original = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(original)


def ensure_erpnext_project(construction_project: str) -> str:
	"""Create (or return) an ERPNext Project for a Construction Project. Idempotent."""
	cp = frappe.get_doc("Construction Project", construction_project)
	if cp.get("erpnext_project") and frappe.db.exists("Project", cp.get("erpnext_project")):
		return cp.get("erpnext_project")

	company = require_company(cp.organization)
	cost_center = None
	if cp.property:
		prop = frappe.get_doc("Property", cp.property)
		cost_center = prop.get("cost_center") or setup.provision_property_cost_center(cp.property)

	with _as_system_user():
		project = frappe.get_doc({
			"doctype": "Project",
			"project_name": cp.project_name,
			"company": company,
			"cost_center": cost_center,
			"property_ref": cp.property,
			"construction_project_ref": cp.name,
			"expected_start_date": cp.get("start_date"),
			"expected_end_date": cp.get("expected_end_date"),
			"estimated_costing": flt(cp.get("total_budget")),
		})
		project.flags.ignore_mandatory = True
		project.insert(ignore_permissions=True)

	cp.db_set("erpnext_project", project.name, update_modified=False)
	frappe.db.commit()
	return project.name


def record_worker_wage(construction_project: str, employee: str, amount: float, description: str = None, posting_date: str = None) -> str:
	"""
	Record ad-hoc casual construction labour as a Purchase Invoice tagged to the
	Project (expense account = Salary Expense, supplier = the worker/labour vendor).

	NOTE: We use a Purchase Invoice rather than a native Journal Entry because the
	app's custom 'Journal Entry' doctype shares the name with ERPNext's and has an
	integer autoname, which collides with ERPNext's series. For full statutory
	payroll, use payroll.run_payroll and set the Salary Slip's project.
	"""
	from frappe.utils import nowdate
	project = ensure_erpnext_project(construction_project)
	cp = frappe.get_doc("Construction Project", construction_project)
	company = require_company(cp.organization)

	from property_management.integration import payroll_setup
	from property_management.integration.expenses import create_purchase_invoice
	expense_acc = payroll_setup._salary_expense_account(company)

	with _as_system_user():
		pi_name = create_purchase_invoice(
			property_name=cp.property,
			supplier_name=f"Casual Labour - {employee}",
			amount=flt(amount),
			description=description or f"Construction wage: {employee}",
			expense_account=expense_acc,
			project=project,
			posting_date=posting_date or nowdate(),
			submit=True,
		)
	frappe.db.commit()
	return pi_name


@frappe.whitelist()
def construction_cost(construction_project: str) -> dict:
	"""
	Roll up the true cost of a construction project from native ERPNext documents
	tagged to its Project: Purchase Invoices (materials/expenses), Journal Entries
	(wages/other), and Salary Slips linked to the project.

	Returns budget vs actual + a breakdown by source.
	"""
	cp = frappe.get_doc("Construction Project", construction_project)
	project = cp.get("erpnext_project")
	if not project:
		# Not yet bridged: fall back to the custom budget-line actuals.
		return {
			"construction_project": cp.name,
			"erpnext_project": None,
			"total_budget": flt(cp.get("total_budget")),
			"total_actual": flt(cp.get("total_actual_spend")),
			"variance": flt(cp.get("total_budget")) - flt(cp.get("total_actual_spend")),
			"breakdown": {"purchase_invoices": 0.0, "wages_journal": 0.0},
			"source": "custom_budget_lines",
		}

	# Materials/other expenses via Purchase Invoices tagged to the project.
	pi_total = 0.0
	pi_items = frappe.get_all(
		"Purchase Invoice Item",
		filters={"project": project, "docstatus": 1},
		fields=["amount"],
	)
	pi_total = sum(flt(r.amount) for r in pi_items)

	# Wages / other costs via Journal Entry lines tagged to the project (debits only).
	je_lines = frappe.get_all(
		"Journal Entry Account",
		filters={"project": project, "docstatus": 1},
		fields=["debit_in_account_currency as debit"],
	)
	je_total = sum(flt(r.debit) for r in je_lines)

	# Salary Slips linked to the project (if payroll tagged the project).
	ss_total = 0.0
	if frappe.get_meta("Salary Slip").has_field("project"):
		slips = frappe.get_all(
			"Salary Slip", filters={"project": project, "docstatus": 1}, fields=["gross_pay"]
		)
		ss_total = sum(flt(s.gross_pay) for s in slips)

	total_actual = pi_total + je_total + ss_total
	budget = flt(cp.get("total_budget"))

	return {
		"construction_project": cp.name,
		"erpnext_project": project,
		"project_name": cp.project_name,
		"property": cp.property,
		"total_budget": budget,
		"total_actual": total_actual,
		"variance": budget - total_actual,
		"breakdown": {
			"materials_and_expenses (Purchase Invoices)": pi_total,
			"wages_and_other (Journal Entries)": je_total,
			"payroll (Salary Slips)": ss_total,
		},
		"source": "erpnext_project",
	}
