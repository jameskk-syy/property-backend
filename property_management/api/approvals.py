# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

UNRESTRICTED_ROLES = {"Administrator", "System Manager", "Director", "Office", "Office User", "Organization Admin"}


@frappe.whitelist()
def get_pending_approvals(user=None):
	"""Returns centralized pending approval requests across all categories."""
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
	return requests


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
