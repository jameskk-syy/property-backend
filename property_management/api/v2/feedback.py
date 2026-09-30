# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Feedback (formerly Complaints).

Admin/caretaker endpoints to list feedback filtered by property, tenant, or caretaker,
and to respond / update status. Row-level scoping (organization + assigned properties)
is enforced by permission.get_permission_query_conditions via get_list.

Caretakers can:
- Raise feedback themselves
- See their own feedback + feedback raised by tenants on their properties

Base URL: /api/method/property_management.api.v2.feedback.<function>
"""

import frappe
from frappe.utils import now_datetime

from property_management.api.v2 import envelope


@frappe.whitelist()
@envelope
def list_feedback(property=None, tenant=None, caretaker=None, raised_by=None, status=None, category=None, search=None, page=1, page_size=8):
	"""
	Feedback for the admin/caretaker with pagination, filterable by property, tenant, caretaker, or raised_by.
	Uses get_list so multi-tenant + property-scoped permissions apply automatically.
	"""
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	
	filters = {}
	if property:
		filters["property"] = property
	if tenant:
		filters["tenant"] = tenant
	if caretaker:
		filters["caretaker"] = caretaker
	if raised_by:
		filters["raised_by_type"] = raised_by
	if status:
		filters["status"] = status
	if category:
		filters["category"] = category

	rows = frappe.get_list(
		"Tenant Complaint", filters=filters,
		fields=["name", "raised_by_type", "tenant", "tenant_name", "mobile_number",
				"caretaker", "caretaker_name", "property", "unit", "category",
				"priority", "status", "feedback", "admin_response",
				"responded_by", "responded_at", "creation"],
		order_by="creation desc", ignore_permissions=False,
	)

	out = []
	for r in rows:
		prop_name = frappe.db.get_value("Property", r.property, "property_name") if r.property else ""
		unit_no = frappe.db.get_value("Property Unit", r.unit, "unit_number") if r.unit else ""
		if search:
			s = str(search).lower()
			hay = f"{r.tenant_name or r.tenant} {r.feedback} {prop_name} {r.caretaker_name or ''}".lower()
			if s not in hay:
				continue
		out.append({
			"id": r.name,
			"raisedBy": r.raised_by_type or "Tenant",
			"tenant": r.tenant,
			"tenantName": r.tenant_name or r.tenant or "",
			"mobileNumber": r.mobile_number or "",
			"caretaker": r.caretaker,
			"caretakerName": r.caretaker_name or "",
			"property": r.property,
			"propertyName": prop_name or r.property or "",
			"unit": unit_no or r.unit or "",
			"category": r.category or "General",
			"priority": r.priority or "Medium",
			"status": r.status or "Open",
			"feedback": r.feedback or "",
			"response": r.admin_response or "",
			"respondedBy": r.responded_by or None,
			"respondedAt": str(r.responded_at)[:19] if r.responded_at else None,
			"date": str(r.creation)[:10] if r.creation else "",
		})
	
	total = len(out)
	offset = (page - 1) * page_size
	paginated = out[offset:offset + page_size]
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': paginated,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


@frappe.whitelist()
@envelope
def feedback_stats(property=None, tenant=None, caretaker=None, raised_by=None):
	"""Counts by status for the feedback dashboard cards."""
	filters = {}
	if property:
		filters["property"] = property
	if tenant:
		filters["tenant"] = tenant
	if caretaker:
		filters["caretaker"] = caretaker
	if raised_by:
		filters["raised_by_type"] = raised_by
	
	rows = frappe.get_list("Tenant Complaint", filters=filters, fields=["status"], limit=0)
	stats = {"total": len(rows), "open": 0, "in_progress": 0, "resolved": 0, "closed": 0}
	key = {"Open": "open", "In Progress": "in_progress", "Resolved": "resolved", "Closed": "closed"}
	for r in rows:
		k = key.get(r.status)
		if k:
			stats[k] += 1
	return stats


@frappe.whitelist()
@envelope
def respond_feedback(name, response=None, status=None):
	"""Admin/caretaker: add a response and/or change the status of feedback."""
	if not frappe.db.exists("Tenant Complaint", name):
		frappe.throw("Feedback not found.")
	doc = frappe.get_doc("Tenant Complaint", name)
	doc.respond(response=response, status=status)
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
@envelope
def raise_feedback(feedback, property=None, unit=None, tenant=None, category="General", priority="Medium", photo=None):
	"""
	Caretaker raises feedback. Auto-fills caretaker info from logged-in user.
	If tenant is provided, auto-fills tenant info.
	"""
	user = frappe.session.user
	if not feedback or not feedback.strip():
		frappe.throw("Please provide your feedback.")
	
	# Find caretaker linked to this user
	caretaker = frappe.db.get_value("Property Caretaker", {"user": user}, "name")
	caretaker_name = ""
	if caretaker:
		caretaker_name = frappe.db.get_value("Property Caretaker", caretaker, "caretaker_name") or ""
	
	# Get tenant info if provided
	tenant_name = ""
	mobile_number = ""
	if tenant:
		tenant_doc = frappe.db.get_value("Property Tenant", tenant, ["tenant_name", "phone"], as_dict=True)
		if tenant_doc:
			tenant_name = tenant_doc.tenant_name or ""
			mobile_number = tenant_doc.phone or ""
	
	doc = frappe.get_doc({
		"doctype": "Tenant Complaint",
		"raised_by_type": "Caretaker",
		"raised_by_user": user,
		"caretaker": caretaker,
		"caretaker_name": caretaker_name,
		"tenant": tenant or None,
		"tenant_name": tenant_name,
		"mobile_number": mobile_number,
		"property": property or None,
		"unit": unit or None,
		"category": category or "General",
		"priority": priority or "Medium",
		"feedback": feedback.strip(),
		"photo": photo or None,
		"status": "Open",
	})
	doc.insert(ignore_permissions=True)
	return {"feedback": doc.name, "status": doc.status}


@frappe.whitelist()
@envelope
def my_feedback():
	"""
	Feedback visible to the logged-in caretaker:
	- Feedback they raised themselves
	- Feedback raised by tenants on their assigned properties
	"""
	user = frappe.session.user
	
	# Find caretaker linked to this user
	caretaker = frappe.db.get_value("Property Caretaker", {"user": user}, "name")
	
	if not caretaker:
		return []
	
	# Get properties assigned to this caretaker
	assigned_properties = frappe.get_all(
		"Property",
		filters={"caretaker": caretaker},
		pluck="name"
	)
	
	# Build OR filters: raised by me OR on my properties
	or_filters = []
	or_filters.append(["caretaker", "=", caretaker])
	if assigned_properties:
		or_filters.append(["property", "in", assigned_properties])
	
	rows = frappe.get_all(
		"Tenant Complaint",
		or_filters=or_filters,
		fields=["name", "raised_by_type", "tenant", "tenant_name", "mobile_number",
				"caretaker", "caretaker_name", "property", "unit", "category",
				"priority", "status", "feedback", "admin_response",
				"responded_by", "responded_at", "creation"],
		order_by="creation desc",
	)
	
	out = []
	for r in rows:
		prop_name = frappe.db.get_value("Property", r.property, "property_name") if r.property else ""
		unit_no = frappe.db.get_value("Property Unit", r.unit, "unit_number") if r.unit else ""
		out.append({
			"id": r.name,
			"raisedBy": r.raised_by_type or "Tenant",
			"tenant": r.tenant,
			"tenantName": r.tenant_name or r.tenant or "",
			"mobileNumber": r.mobile_number or "",
			"caretaker": r.caretaker,
			"caretakerName": r.caretaker_name or "",
			"property": r.property,
			"propertyName": prop_name or r.property or "",
			"unit": unit_no or r.unit or "",
			"category": r.category or "General",
			"priority": r.priority or "Medium",
			"status": r.status or "Open",
			"feedback": r.feedback or "",
			"response": r.admin_response or "",
			"respondedBy": r.responded_by or None,
			"respondedAt": str(r.responded_at)[:19] if r.responded_at else None,
			"date": str(r.creation)[:10] if r.creation else "",
		})
	return out


@frappe.whitelist()
@envelope
def tenant_options(property=None):
	"""Lightweight [{id,name}] list of tenants (for the admin feedback filter)."""
	filters = {}
	if property:
		names = frappe.get_all("Tenant Complaint", filters={"property": property}, pluck="tenant")
		if not names:
			return []
		filters["name"] = ["in", list(set(n for n in names if n))]
	rows = frappe.get_all("Property Tenant", filters=filters,
						  fields=["name", "tenant_name"], order_by="tenant_name asc")
	return [{"id": r.name, "name": r.tenant_name or r.name} for r in rows]


@frappe.whitelist()
@envelope
def caretaker_options(property=None):
	"""Lightweight [{id,name}] list of caretakers (for the admin feedback filter)."""
	filters = {}
	if property:
		names = frappe.get_all("Tenant Complaint", filters={"property": property}, pluck="caretaker")
		if not names:
			return []
		filters["name"] = ["in", list(set(n for n in names if n))]
	rows = frappe.get_all("Property Caretaker", filters=filters,
						  fields=["name", "caretaker_name"], order_by="caretaker_name asc")
	return [{"id": r.name, "name": r.caretaker_name or r.name} for r in rows]


# ----- BACKWARD COMPATIBILITY (old API names) -----

@frappe.whitelist()
@envelope
def list_complaints(property=None, tenant=None, status=None, category=None, search=None, page=1, page_size=8):
	"""Backward compatible alias for list_feedback."""
	return list_feedback(property=property, tenant=tenant, status=status, category=category, search=search, page=page, page_size=page_size)


@frappe.whitelist()
@envelope
def complaint_stats(property=None, tenant=None):
	"""Backward compatible alias for feedback_stats."""
	return feedback_stats(property=property, tenant=tenant)


@frappe.whitelist()
@envelope
def respond_complaint(name, response=None, status=None):
	"""Backward compatible alias for respond_feedback."""
	return respond_feedback(name=name, response=response, status=status)
