# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Install / migrate setup: seed the app's access modules and grant them to the
admin-side roles. Runs on both `after_install` (fresh site) and `after_migrate`
(reinstall / update). All operations are idempotent.

The access model (see api/roles.py) is driven by:
  - Module Def        : the named access modules shown in the admin sidebar.
  - Module Permission : per-role can_view/create/edit/approve on each module.

Administrator and unrestricted roles already bypass these (roles.py grants them
full access), but seeding real Module Permission rows means the Access
Management UI shows the correct matrix out of the box after a fresh install.
"""

import frappe

# The admin-side access module catalog: sections -> [{key, label}].
# This is the single source of truth mirrored by the frontend (data/modules.js).
ACCESS_MODULE_CATALOG = [
	("Leasing", [
		("properties", "Properties & Units"),
		("billing", "Tenants & Billing"),
		("vacancy", "Vacancy Management"),
		("tenantOnboarding", "Tenant Onboarding"),
		("propertyOnboarding", "Property Onboarding"),
	]),
	("Finance", [
		("financialReports", "Financial Reports"),
		("accounting", "Accounting"),
		("paymentReconciliation", "Payment Reconciliation"),
		("arrears", "Arrears Tracking"),
		("salaries", "Salary Management"),
		("expenses", "Expense Management"),
	]),
	("People", [
		("landlords", "Landlords"),
		("caretakers", "Caretakers"),
		("accessManagement", "Access Management"),
	]),
	("Operations", [
		("documents", "Documents"),
		("construction", "Construction Projects"),
		("whatsapp", "WhatsApp Communication"),
	]),
	("System", [
		("notifications", "Notifications"),
		("auditLog", "Audit Log"),
		("settings", "Organization Settings"),
	]),
]

# Flat list of the granular module keys.
ACCESS_MODULE_KEYS = [key for _section, mods in ACCESS_MODULE_CATALOG for key, _label in mods]

# The top-level sections (kept for the sidebar-level access on User records).
ACCESS_SECTIONS = [section for section, _mods in ACCESS_MODULE_CATALOG]

# Legacy alias (section-level module names).
ACCESS_MODULES = ACCESS_SECTIONS

# Roles that should get full access to every access module on install.
ADMIN_ROLES = ["Administrator", "System Manager", "Organization Admin", "Director"]


def _ensure_module_def(module_name):
	"""Create a custom Module Def for an access module if missing."""
	if frappe.db.exists("Module Def", module_name):
		return module_name
	frappe.get_doc({
		"doctype": "Module Def",
		"module_name": module_name,
		"app_name": "property_management",
		"custom": 1,
	}).insert(ignore_permissions=True)
	return module_name


def _ensure_module_permission(role, module):
	"""Grant a role full access to a module (idempotent)."""
	existing = frappe.db.get_value(
		"Module Permission", {"role": role, "module": module, "organization": ["is", "not set"]}, "name"
	) or frappe.db.get_value("Module Permission", {"role": role, "module": module}, "name")
	if existing:
		doc = frappe.get_doc("Module Permission", existing)
	else:
		doc = frappe.get_doc({
			"doctype": "Module Permission",
			"role": role,
			"module": module,
			"organization": None,
		})
	doc.can_view = 1
	doc.can_create = 1
	doc.can_edit = 1
	doc.can_approve = 1
	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True
	doc.flags.ignore_mandatory = True
	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)


def _ensure_roles():
	"""Ensure the app's roles exist (desk-scoped where relevant)."""
	desk = {"Organization Admin": 1, "Director": 1, "Office User": 1, "Caretaker": 1,
			"Landlord": 0, "Tenant": 0}
	for role, desk_access in desk.items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc({
				"doctype": "Role",
				"role_name": role,
				"desk_access": desk_access,
			}).insert(ignore_permissions=True)


# The default parent Organization. Its ERPNext Company is named the same, so
# the Company created for it is "Dadis Estates Limited". Everything in the app
# is wired to an Organization, so at least one must exist for creates to work.
DEFAULT_ORGANIZATION = "Dadis Estates Limited"


def ensure_default_organization():
	"""Create the default parent Organization if none exists yet. Idempotent."""
	if frappe.db.exists("Organization", DEFAULT_ORGANIZATION):
		return DEFAULT_ORGANIZATION
	# If some other Organization already exists, don't force-create ours.
	if frappe.db.count("Organization"):
		return frappe.db.get_value("Organization", {}, "name")
	frappe.get_doc({
		"doctype": "Organization",
		"organization_name": DEFAULT_ORGANIZATION,
		"status": "Active",
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return DEFAULT_ORGANIZATION


def ensure_default_company(organization):
	"""
	Provision the ERPNext Company (with KES/Kenya Standard chart of accounts) for
	the organization, plus the global masters financial documents need. Runs only
	when ERPNext is installed. Idempotent; safe to skip if it fails on a partial
	install (it will be retried on migrate).
	"""
	if not frappe.db.exists("DocType", "Company"):
		return None
	try:
		from property_management.integration import erpnext_setup as setup
		setup.bootstrap_global_masters()
		company = setup.provision_company(organization)
		# Dedicated Rent/Utility income accounts + refundable-deposit liability.
		setup.ensure_income_accounts(company)
		setup.ensure_deposit_liability_account(company)
		return company
	except Exception:
		frappe.log_error(title="ensure_default_company failed", message=frappe.get_traceback())
		return None


def seed_access_modules():
	"""
	Seed the granular access-module catalog (Module Def per key) and grant every
	module to the admin-side roles. Provision the default Organization + ERPNext
	Company (chart of accounts). Idempotent; safe on every install and migrate.
	"""
	org = ensure_default_organization()
	ensure_default_company(org)
	_ensure_roles()

	# Create a Module Def for each granular module key.
	for key in ACCESS_MODULE_KEYS:
		_ensure_module_def(key)

	# Admin roles get full access to every granular module.
	for role in ADMIN_ROLES:
		if not frappe.db.exists("Role", role):
			continue
		for key in ACCESS_MODULE_KEYS:
			_ensure_module_permission(role, key)

	# Seed default expense categories (mirror the legacy options) so the Raise
	# Expense dropdown is populated out of the box.
	try:
		from property_management.property_management.doctype.expense_category.expense_category import ensure_default_categories
		ensure_default_categories()
	except Exception:
		frappe.log_error(title="expense category seeding failed", message=frappe.get_traceback())

	# Caretaker default grant (Leasing + Operations sections), matching the
	# app's role definition, so its matrix is enforced not just displayed.
	caretaker_keys = [
		k for section, mods in ACCESS_MODULE_CATALOG if section in ("Leasing", "Operations")
		for k, _l in mods
	]
	if frappe.db.exists("Role", "Caretaker"):
		for key in caretaker_keys:
			_ensure_module_permission("Caretaker", key)

	frappe.db.commit()


def after_install():
	"""Hook: fresh site install."""
	seed_access_modules()


def after_migrate():
	"""Hook: migrate / reinstall. Keep the custom-fields hook working too."""
	seed_access_modules()
