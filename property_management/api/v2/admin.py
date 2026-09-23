# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""API v2 - Admin/provisioning endpoints (System Manager only)."""

import frappe

from property_management.api.v2 import envelope


def _require_system_manager():
	if "System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw("Only System Manager can perform provisioning.", frappe.PermissionError)


@frappe.whitelist()
@envelope
def provision_organization(organization):
	"""Provision an ERPNext Company for an Organization."""
	_require_system_manager()
	from property_management.integration.erpnext_setup import provision_company
	return {"company": provision_company(organization)}


@frappe.whitelist()
@envelope
def provision_property(property):
	"""Provision a Cost Center for a Property."""
	_require_system_manager()
	from property_management.integration.erpnext_setup import provision_property_cost_center
	return {"cost_center": provision_property_cost_center(property)}


@frappe.whitelist()
@envelope
def provision_all():
	"""Provision Companies for all Organizations and Cost Centers for all Properties."""
	_require_system_manager()
	from property_management.integration.erpnext_setup import provision_all as _all
	return _all()


@frappe.whitelist()
@envelope
def setup_payroll(company):
	"""Create Salary Components + Structures for a company."""
	_require_system_manager()
	from property_management.integration.payroll_setup import setup_salary_components
	return setup_salary_components(company)


# --- Expense accounts & categories -------------------------------------------

_EXPENSE_ADMIN_ROLES = {"Administrator", "System Manager", "Director", "Organization Admin"}


def _require_expense_admin():
	roles = set(frappe.get_roles(frappe.session.user))
	if frappe.session.user != "Administrator" and not (roles & _EXPENSE_ADMIN_ROLES):
		frappe.throw("Not permitted to manage expense categories.", frappe.PermissionError)


def _resolve_company(company=None, organization=None, property=None):
	if company:
		return company
	if property and not organization:
		organization = frappe.db.get_value("Property", property, "organization")
	if organization:
		from property_management.integration.settings import company_for_organization
		return company_for_organization(organization)
	# Fall back to the first provisioned company.
	return frappe.db.get_value("Company", {}, "name")


@frappe.whitelist()
@envelope
def list_expense_accounts(company=None, organization=None, property=None):
	"""
	List the indirect (operating) expense account heads for a company, sourced
	from the native ERPNext chart of accounts (the expense-account module). These
	are the accounts that category-driven property expenses post against.
	"""
	company = _resolve_company(company, organization, property)
	if not company:
		frappe.throw("No company found; provision an Organization first.")

	accounts = frappe.get_all(
		"Account",
		filters={"company": company, "root_type": "Expense", "is_group": 0},
		fields=["name", "account_name", "account_number", "parent_account"],
		order_by="lft asc",
	)
	# Flag which ones sit under the Indirect Expenses group.
	from property_management.integration.erpnext_setup import _indirect_expense_group
	indirect_parent = _indirect_expense_group(company)
	for a in accounts:
		a["is_indirect"] = bool(indirect_parent and a.get("parent_account") == indirect_parent)
	return {"company": company, "accounts": accounts}


@frappe.whitelist()
@envelope
def create_expense_category(category_name, account_name=None, description=None, provision=True):
	"""
	Create a new (indirect) Expense Category from the admin UI. Optionally provisions
	the matching expense account under 'Indirect Expenses' in every provisioned
	Company, so the category is immediately postable across all tenant orgs.
	"""
	_require_expense_admin()

	if frappe.db.exists("Expense Category", category_name):
		frappe.throw(f"Expense Category '{category_name}' already exists.")

	cat = frappe.get_doc({
		"doctype": "Expense Category",
		"category_name": category_name,
		"account_name": account_name or category_name,
		"description": description,
		"is_active": 1,
	})
	cat.insert(ignore_permissions=True)

	provisioned = []
	if provision in (True, "true", "1", 1):
		from property_management.integration.erpnext_setup import get_expense_account_for_category
		for company in frappe.get_all("Company", pluck="name"):
			try:
				acc = get_expense_account_for_category(company, cat.name)
				if acc:
					provisioned.append(acc)
			except Exception:
				frappe.log_error(title="create_expense_category provisioning", message=frappe.get_traceback())

	frappe.db.commit()
	return {"category": cat.name, "provisioned_accounts": provisioned}
