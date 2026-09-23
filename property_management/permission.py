# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe

UNRESTRICTED_ROLES = {"System Manager", "Director", "Office", "Office User", "Organization Admin"}


def get_permission_query_conditions(user=None):
	"""
	Enforces Multi-Tenancy (organization) and Property-Scoped User Assignments at the database query level.
	Executive roles (Administrator, System Manager, Director, Office, Office User, Organization Admin) get org-wide access.
	Operational roles (e.g. Caretaker, Landlord) are scoped to their assigned properties.
	"""
	if not user:
		user = frappe.session.user

	if user == "Administrator":
		return ""

	user_roles = set(frappe.get_roles(user))

	user_org = frappe.db.get_value("User", user, "organization")
	doctype = frappe.flags.current_doctype or ""
	conditions = []

	if user_org and "System Manager" not in user_roles:
		conditions.append(f"`tab{doctype}`.`organization` = {frappe.db.escape(user_org)}")

	# Check if user has unrestricted executive role
	if bool(user_roles & UNRESTRICTED_ROLES):
		return " AND ".join(conditions) if conditions else ""

	# For operational roles, check Property assignment
	assigned_props = frappe.get_all(
		"User Property Assignment",
		filters={"user": user},
		pluck="property"
	)

	if assigned_props:
		escaped_props = ", ".join([frappe.db.escape(p) for p in assigned_props])
		if doctype == "Property":
			conditions.append(f"`tabProperty`.`name` IN ({escaped_props})")
		else:
			# If DocType contains a 'property' column
			meta = frappe.get_meta(doctype) if doctype else None
			if meta and meta.has_field("property"):
				conditions.append(f"`tab{doctype}`.`property` IN ({escaped_props})")

	return " AND ".join(conditions) if conditions else ""

def set_default_organization(doc, method=None):
    """
    Auto-populates mandatory organization field if missing during document creation.
    """
    if hasattr(doc, "organization") and not doc.organization:
        org = None
        if hasattr(doc, "property") and doc.property:
            org = frappe.db.get_value("Property", doc.property, "organization")
        if not org:
            org = frappe.defaults.get_user_default("organization")
        if not org and hasattr(frappe.session, "user") and frappe.session.user != "Guest":
            org = frappe.db.get_value("User Property Assignment", {"user": frappe.session.user}, "organization")
        if not org:
            # Fall back to the single/first real Organization if one exists.
            # Never inject a hardcoded name (that fails link validation on a
            # fresh site that has no Organization yet).
            org = frappe.db.get_value("Organization", {}, "name")
        if org:
            doc.organization = org
