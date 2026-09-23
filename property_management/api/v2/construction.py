# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Construction module (admin).

Endpoints for the admin Construction menu:
  - Projects  : list / create Construction Projects (+ budget-vs-actual)
  - Purchases : list / create material purchase records, and Director-only
                approve / reject (maker-checker preserved)
  - Suppliers : list / create Expense Vendors (which bridge to ERPNext Suppliers
                automatically the first time they're used on a purchase)

Approval of a construction purchase is restricted to the Director role. On
approval, the doctype posts a native ERPNext Purchase Invoice tagged to the
project (see integration/expenses.post_construction_purchase), which is what
feeds the project's real actual-spend rollup.

Base URL: /api/method/property_management.api.v2.construction.<function>
"""

import frappe
from frappe.utils import flt, now_datetime, nowdate

from property_management.api.v2 import envelope
from property_management.api.utils import resolve_organization


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _require_director():
	"""Raise unless the current user may approve construction purchases."""
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	# Administrator and Director may approve; everyone else is refused.
	if user == "Administrator" or "Director" in roles:
		return user
	frappe.throw("Only a Director can approve or reject construction purchases.", frappe.PermissionError)


def _vendor_name(vendor):
	"""Resolve an Expense Vendor docname to its display name (falls back to the id)."""
	if not vendor:
		return ""
	return frappe.db.get_value("Expense Vendor", vendor, "vendor_name") or vendor


# Roles allowed to view across all organizations.
_UNRESTRICTED_ROLES = {"Administrator", "System Manager", "Director", "Office", "Office User", "Organization Admin"}


def _scope_org(explicit=None):
	"""
	The organization to filter a construction list by (projects/purchases/vendors
	are tied to an Organization/Company).

	- An explicit organization is used as given (after verifying it exists).
	- The sentinel "all" means "no org filter" — but only for unrestricted execs
	  (Administrator / Director / etc.); a scoped user still gets their own org.
	- Otherwise, resolve the caller's organization.
	"""
	user = frappe.session.user
	is_unrestricted = user == "Administrator" or bool(set(frappe.get_roles(user)) & _UNRESTRICTED_ROLES)

	if explicit == "all":
		return None if is_unrestricted else resolve_organization()
	if explicit and frappe.db.exists("Organization", explicit):
		return explicit
	return resolve_organization()


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def list_projects(organization=None):
	"""
	Construction projects with budget, actual spend and variance, scoped to the
	caller's organization (projects are tied to an Organization/Company). An
	explicit `organization` overrides; unrestricted execs may pass "all".
	"""
	filters = {}
	org = _scope_org(organization)
	if org:
		filters["organization"] = org
	rows = frappe.get_all(
		"Construction Project", filters=filters,
		fields=["name", "project_name", "property", "status", "start_date",
				"expected_end_date", "total_budget", "total_actual_spend", "budget_variance"],
		order_by="creation desc",
	)
	from property_management.integration import construction_budget
	funded_spent = {r.name: construction_budget.budget_summary(r.name) for r in rows}
	out = []
	for r in rows:
		prop_name = frappe.db.get_value("Property", r.property, "property_name") if r.property else ""
		out.append({
			"id": r.name,
			"name": r.project_name or r.name,
			"property": prop_name or r.property or "",
			"status": r.status or "Planning",
			"startDate": str(r.start_date) if r.start_date else "",
			"endDate": str(r.expected_end_date) if r.expected_end_date else "",
			"budget": flt(r.total_budget),
			"spent": funded_spent.get(r.name, {}).get("spent", flt(r.total_actual_spend)),
			"funded": funded_spent.get(r.name, {}).get("funded", 0.0),
			"available": funded_spent.get(r.name, {}).get("available", 0.0),
			"variance": flt(r.total_budget) - funded_spent.get(r.name, {}).get("spent", flt(r.total_actual_spend)),
		})
	return out


@frappe.whitelist()
@envelope
def create_project(project_name, property=None, total_budget=0, start_date=None,
				   expected_end_date=None, status="Planning", organization=None):
	"""
	Create a Construction Project (a new build), tied to the organization; property
	is optional. Bridges to a native ERPNext Project and provisions the ring-fenced
	construction budget account on insert.
	"""
	org = organization or resolve_organization()
	if property and not org:
		org = frappe.db.get_value("Property", property, "organization")
	doc = frappe.get_doc({
		"doctype": "Construction Project",
		"project_name": project_name,
		"organization": org,
		"property": property or None,
		"total_budget": flt(total_budget),
		"start_date": start_date or None,
		"expected_end_date": expected_end_date or None,
		"status": status or "Planning",
	})
	doc.insert(ignore_permissions=True)

	# Provision the budget wallet up front so it can be funded immediately.
	from property_management.integration import construction_budget
	try:
		construction_budget.ensure_budget_account(doc.name)
	except Exception:
		frappe.log_error(title="budget account provisioning failed", message=frappe.get_traceback())

	return {"name": doc.name}


# Fixed category options shared with the frontend dropdown. "Labour / Wages" is
# how worker wages are recorded (vendor = the worker or labour crew).
PURCHASE_CATEGORIES = [
	"Materials", "Labour / Wages", "Equipment", "Transport",
	"Professional Fees", "Permits & Fees", "Other",
]


@frappe.whitelist()
@envelope
def categories():
	"""The allowed purchase categories for the frontend dropdown."""
	return PURCHASE_CATEGORIES


@frappe.whitelist()
@envelope
def funding_sources(organization=None):
	"""Bank/Cash accounts the project budget can be funded FROM."""
	org = _scope_org(organization)
	company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
	if not company:
		from property_management.integration.settings import company_for_organization
		company = company_for_organization(org) if org else None
	if not company:
		return []
	rows = frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "account_type": ["in", ["Bank", "Cash"]]},
		fields=["name", "account_name", "account_type"],
		order_by="account_type asc, account_name asc",
	)
	# Never offer a construction budget wallet as a funding SOURCE.
	rows = [r for r in rows if not (r.account_name or "").startswith("Construction Budget")]
	return [{"id": r.name, "name": r.account_name or r.name, "type": r.account_type} for r in rows]


@frappe.whitelist()
@envelope
def fund_project(project, source_account, amount, posting_date=None, remark=None):
	"""Transfer cash into a project's budget wallet from a Bank/Cash account."""
	if not project or not frappe.db.exists("Construction Project", project):
		frappe.throw("A valid Construction Project is required.")
	from property_management.integration import construction_budget
	je = construction_budget.fund_project(project, source_account, amount,
										  posting_date=posting_date, remark=remark)
	summary = construction_budget.budget_summary(project)
	return {"journal_entry": je, **summary}


@frappe.whitelist()
@envelope
def budget_summary(project):
	"""Funded / spent / available snapshot for a project."""
	from property_management.integration import construction_budget
	return construction_budget.budget_summary(project)


# --------------------------------------------------------------------------
# Purchases (material records)
# --------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def list_purchases(project=None, status=None, organization=None):
	"""Material purchase records, newest first, with vendor + project names resolved."""
	filters = {}
	if project:
		filters["project"] = project
	if status:
		filters["status"] = status
	org = _scope_org(organization)
	if org:
		filters["organization"] = org
	rows = frappe.get_all(
		"Construction Purchase", filters=filters,
		fields=["name", "organization", "property", "project", "vendor", "category",
				"item_description", "amount", "receipt_image", "status",
				"approved_by", "approved_at", "creation"],
		order_by="creation desc",
	)
	out = []
	for r in rows:
		project_name = frappe.db.get_value("Construction Project", r.project, "project_name") if r.project else ""
		prop_name = frappe.db.get_value("Property", r.property, "property_name") if r.property else ""
		# The linked PI is a custom field; read defensively.
		pi = frappe.db.get_value("Construction Purchase", r.name, "purchase_invoice") if frappe.get_meta("Construction Purchase").has_field("purchase_invoice") else None
		out.append({
			"id": r.name,
			"project": r.project,
			"projectName": project_name or r.project or "",
			"property": prop_name or r.property or "",
			"vendor": r.vendor,
			"vendorName": _vendor_name(r.vendor),
			"category": r.category or "",
			"description": r.item_description or "",
			"amount": flt(r.amount),
			"receiptImage": r.receipt_image or None,
			"status": r.status or "Draft",
			"approvedBy": r.approved_by or None,
			"approvedAt": str(r.approved_at) if r.approved_at else None,
			"purchaseInvoice": pi or None,
			"date": str(r.creation)[:10] if r.creation else "",
		})
	return out


@frappe.whitelist()
@envelope
def create_purchase(project, amount, item_description, vendor=None, category=None,
					property=None, receipt_image=None, organization=None):
	"""
	Record a material purchase against a project. Starts in 'Pending Approval'
	so a Director can review it (maker-checker: the creator cannot approve it).
	"""
	if not project or not frappe.db.exists("Construction Project", project):
		frappe.throw("A valid Construction Project is required.")
	if flt(amount) <= 0:
		frappe.throw("Amount must be greater than zero.")

	org = organization or frappe.db.get_value("Construction Project", project, "organization")
	prop = property or frappe.db.get_value("Construction Project", project, "property")

	doc = frappe.get_doc({
		"doctype": "Construction Purchase",
		"organization": org,
		"property": prop or None,
		"project": project,
		"vendor": vendor or None,
		"category": category or None,
		"item_description": item_description,
		"amount": flt(amount),
		"receipt_image": receipt_image or None,
		"status": "Pending Approval",
	})
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
@envelope
def approve_purchase(name, comment=None):
	"""
	Director-only approval. Enforces maker-checker (creator != approver), then
	runs the doctype's approve(): updates budget actuals and posts a native
	ERPNext Purchase Invoice tagged to the project.
	"""
	user = _require_director()
	if not frappe.db.exists("Construction Purchase", name):
		frappe.throw("Purchase not found.")
	cp = frappe.get_doc("Construction Purchase", name)
	if cp.status == "Approved":
		frappe.throw("This purchase is already approved.")
	if user != "Administrator" and cp.owner == user:
		frappe.throw("Maker-Checker: you cannot approve a purchase you created.", frappe.PermissionError)

	cp.approve(user=user, comment=comment)
	cp.reload()
	pi = frappe.db.get_value("Construction Purchase", cp.name, "purchase_invoice") if frappe.get_meta("Construction Purchase").has_field("purchase_invoice") else None
	return {"name": cp.name, "status": cp.status, "purchaseInvoice": pi or None}


@frappe.whitelist()
@envelope
def reject_purchase(name, comment=None):
	"""Director-only rejection."""
	user = _require_director()
	if not frappe.db.exists("Construction Purchase", name):
		frappe.throw("Purchase not found.")
	cp = frappe.get_doc("Construction Purchase", name)
	cp.reject(user=user, comment=comment)
	return {"name": cp.name, "status": "Rejected"}


# --------------------------------------------------------------------------
# Suppliers (Expense Vendors)
# --------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def list_vendors(organization=None):
	"""Expense Vendors for the vendor picker / suppliers page."""
	filters = {}
	org = _scope_org(organization)
	if org:
		filters["organization"] = org
	rows = frappe.get_all(
		"Expense Vendor", filters=filters,
		fields=["name", "vendor_name", "category", "phone_number", "notes"],
		order_by="vendor_name asc",
	)
	# Count how many construction purchases each vendor has (light activity signal).
	out = []
	for v in rows:
		purchases = frappe.db.count("Construction Purchase", {"vendor": v.name})
		out.append({
			"id": v.name,
			"name": v.vendor_name or v.name,
			"category": v.category or "General",
			"phone": v.phone_number or "",
			"notes": v.notes or "",
			"purchases": purchases,
		})
	return out


@frappe.whitelist()
@envelope
def create_vendor(vendor_name, category="General", phone_number=None, notes=None, organization=None):
	"""Onboard a supplier/vendor. Reused across expenses + construction purchases."""
	if not vendor_name:
		frappe.throw("Vendor name is required.")
	org = organization or resolve_organization()
	if frappe.db.exists("Expense Vendor", vendor_name):
		doc = frappe.get_doc("Expense Vendor", vendor_name)
		doc.category = category or doc.category
		doc.phone_number = phone_number or doc.phone_number
		if notes:
			doc.notes = notes
		doc.save(ignore_permissions=True)
		return {"name": doc.name, "created": False}
	doc = frappe.get_doc({
		"doctype": "Expense Vendor",
		"vendor_name": vendor_name,
		"organization": org,
		"category": category or "General",
		"phone_number": phone_number or None,
		"notes": notes or None,
	})
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "created": True}
