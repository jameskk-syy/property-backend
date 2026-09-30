# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

UNRESTRICTED_ROLES = {"Administrator", "System Manager", "Director", "Office", "Office User", "Organization Admin"}


def _request_property(req):
	"""Resolve the Property a request relates to, via its referenced source doc.

	Most approvable docs (e.g. Property Expense) carry a `property` link. Returns
	the property docname or None when the reference has no property dimension.
	"""
	ref_dt = req.get("reference_doctype")
	ref_name = req.get("reference_name")
	if not ref_dt or not ref_name:
		return None
	if not frappe.get_meta(ref_dt).has_field("property"):
		return None
	if not frappe.db.exists(ref_dt, ref_name):
		return None
	return frappe.db.get_value(ref_dt, ref_name, "property")


@frappe.whitelist()
def get_pending_approvals(user=None, property=None):
	"""Returns centralized pending approval requests across all categories.

	property: optional Property filter. A request is included when its referenced
	source document resolves to that property (e.g. Property Expense.property).
	Each returned row is annotated with `property` / `property_name` for display.
	"""
	if not user:
		user = frappe.session.user

	user_roles = set(frappe.get_roles(user))

	filters = {"status": "Pending"}

	if user != "Administrator" and not bool(user_roles & UNRESTRICTED_ROLES):
		filters["approver_role"] = ["in", list(user_roles)]

	requests = frappe.get_all(
		"Approval Request",
		filters=filters,
		fields=[
			"name", "request_type", "reference_doctype", "reference_name",
			"requested_by", "organization", "comment", "creation"
		]
	)

	# Annotate each request with its resolved property, and filter if requested.
	prop_name_cache = {}
	out = []
	for req in requests:
		prop = _request_property(req)
		if property and prop != property:
			continue
		req["property"] = prop or ""
		if prop:
			if prop not in prop_name_cache:
				prop_name_cache[prop] = frappe.db.get_value("Property", prop, "property_name") or prop
			req["property_name"] = prop_name_cache[prop]
		else:
			req["property_name"] = ""
		out.append(req)

	return out


@frappe.whitelist()
def approve_request(approval_id, comment=None, user=None):
	"""Approve maker-checker request, updating source doc and writing audit log."""
	if not user:
		user = frappe.session.user

	req = frappe.get_doc("Approval Request", approval_id)

	if req.requested_by and req.requested_by == user and user != "Administrator":
		frappe.throw("Maker-Checker Rule Violation: Creator cannot approve their own request.")

	ref_dt = req.reference_doctype
	ref_name = req.reference_name

	if frappe.db.exists(ref_dt, ref_name):
		target_doc = frappe.get_doc(ref_dt, ref_name)
		if hasattr(target_doc, "approve"):
			target_doc.approve(user=user, comment=comment)
		else:
			target_doc.status = "Approved"
			target_doc.save(ignore_permissions=True)

	req.status = "Approved"
	req.approved_by = user
	req.approved_at = now_datetime()
	if comment:
		req.comment = comment
	req.save(ignore_permissions=True)

	return {"status": "Success", "approval_id": req.name, "doc_status": "Approved"}


@frappe.whitelist()
def reject_request(approval_id, comment=None, user=None):
	"""Reject maker-checker request, updating source doc and writing audit log."""
	if not user:
		user = frappe.session.user

	req = frappe.get_doc("Approval Request", approval_id)

	ref_dt = req.reference_doctype
	ref_name = req.reference_name

	if frappe.db.exists(ref_dt, ref_name):
		target_doc = frappe.get_doc(ref_dt, ref_name)
		if hasattr(target_doc, "reject"):
			target_doc.reject(user=user, comment=comment)
		else:
			target_doc.status = "Rejected"
			target_doc.save(ignore_permissions=True)

	req.status = "Rejected"
	req.approved_by = user
	req.approved_at = now_datetime()
	if comment:
		req.comment = comment
	req.save(ignore_permissions=True)

	return {"status": "Success", "approval_id": req.name, "doc_status": "Rejected"}
