# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""Shared helpers for the property_management API layer."""

import frappe


def resolve_organization(user=None, explicit=None):
	"""
	Resolve the Organization to use for a create/write operation.

	Resolution order:
	  1. an explicit organization passed by the caller (if it exists),
	  2. the organization on any User Property Assignment for the user,
	  3. the single Organization in the system (common single-tenant case),
	  4. the first Active Organization,
	  5. the first Organization of any status.

	Returns the Organization name, or None if none exist.
	"""
	if explicit and frappe.db.exists("Organization", explicit):
		return explicit

	user = user or frappe.session.user

	assigned_org = frappe.db.get_value(
		"User Property Assignment", {"user": user}, "organization"
	)
	if assigned_org:
		return assigned_org

	orgs = frappe.get_all("Organization", pluck="name", limit=2)
	if len(orgs) == 1:
		return orgs[0]

	active = frappe.get_all(
		"Organization", filters={"status": "Active"}, pluck="name", limit=1
	)
	if active:
		return active[0]

	if orgs:
		return orgs[0]

	# No organization exists yet — provision the default parent Organization so
	# the app is usable out of the box instead of failing every create.
	try:
		from property_management.setup import ensure_default_organization
		return ensure_default_organization()
	except Exception:
		frappe.log_error(title="default org provisioning failed", message=frappe.get_traceback())
		return None
