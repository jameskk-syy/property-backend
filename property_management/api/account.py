# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Shared "my account" API — the logged-in user's own profile, role-agnostic.

Used by the profile/settings page for every role (admin, landlord, caretaker,
tenant). Reads real User details and mirrors edits back to the User and any linked
Property Tenant / Caretaker / Landlord record.
"""

import frappe


def _linked_record(user):
	"""Return ('Doctype', name) of the record linked to this user, if any."""
	for doctype in ("Property Tenant", "Caretaker", "Landlord"):
		if not frappe.db.has_column(doctype, "user"):
			continue
		name = frappe.db.get_value(doctype, {"user": user}, "name")
		if name:
			return doctype, name
	return None, None


def _org_display():
	"""Human-readable organization/company name for the profile header."""
	org = frappe.get_all("Organization", fields=["organization_name", "name"], limit=1)
	if org:
		return org[0].organization_name or org[0].name
	company = frappe.get_all("Company", fields=["company_name", "name"], limit=1)
	if company:
		return company[0].company_name or company[0].name
	return ""


@frappe.whitelist()
def get_my_account():
	"""The logged-in user's real profile details."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Please sign in.", frappe.PermissionError)

	u = frappe.db.get_value(
		"User", user,
		["name", "full_name", "first_name", "last_name", "email", "phone", "mobile_no", "user_image"],
		as_dict=True,
	) or {}

	phone = u.get("phone") or u.get("mobile_no") or ""
	national_id = ""

	doctype, name = _linked_record(user)
	if doctype == "Property Tenant" and name:
		t = frappe.db.get_value("Property Tenant", name, ["phone", "national_id"], as_dict=True) or {}
		phone = phone or t.get("phone") or ""
		national_id = t.get("national_id") or ""
	elif doctype and name and frappe.db.has_column(doctype, "phone"):
		phone = phone or (frappe.db.get_value(doctype, name, "phone") or "")

	return {
		"id": u.get("name") or user,
		"name": u.get("full_name") or f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() or user,
		"email": u.get("email") or user,
		"phone": phone,
		"national_id": national_id,
		"avatar": u.get("user_image") or "",
		"organization": _org_display(),
		"linked_doctype": doctype,
	}


@frappe.whitelist()
def update_my_account(full_name=None, phone=None):
	"""
	Update the logged-in user's editable profile fields (name + phone). Mirrors the
	phone to the linked Tenant/Caretaker/Landlord record so both stay in sync.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Please sign in.", frappe.PermissionError)

	udoc = frappe.get_doc("User", user)
	if full_name:
		parts = full_name.strip().split(" ", 1)
		udoc.first_name = parts[0]
		udoc.last_name = parts[1] if len(parts) > 1 else ""
	if phone is not None:
		udoc.phone = phone
		udoc.mobile_no = phone
	udoc.save(ignore_permissions=True)

	# Mirror to the linked domain record.
	doctype, name = _linked_record(user)
	if doctype and name:
		updates = {}
		if phone is not None and frappe.db.has_column(doctype, "phone"):
			updates["phone"] = phone
		if full_name:
			name_field = {"Property Tenant": "tenant_name", "Caretaker": "caretaker_name",
						  "Landlord": "landlord_name"}.get(doctype)
			if name_field and frappe.db.has_column(doctype, name_field):
				updates[name_field] = full_name
		if updates:
			frappe.db.set_value(doctype, name, updates)

	frappe.db.commit()
	return get_my_account()
