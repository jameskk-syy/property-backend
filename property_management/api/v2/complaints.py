# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Tenant Complaints (admin view).

Admin/caretaker endpoints to list complaints filtered by property and/or tenant,
and to respond / update a complaint's status. Row-level scoping (organization +
assigned properties) is enforced by permission.get_permission_query_conditions
via get_list, so a caretaker only sees complaints on their properties.

Base URL: /api/method/property_management.api.v2.complaints.<function>
"""

import frappe
from frappe.utils import now_datetime

from property_management.api.v2 import envelope


@frappe.whitelist()
@envelope
def list_complaints(property=None, tenant=None, status=None, category=None, search=None, limit=200):
	"""
	Complaints for the admin, filterable by property and/or tenant. Uses get_list
	so multi-tenant + property-scoped permissions apply automatically.
	"""
	filters = {}
	if property:
		filters["property"] = property
	if tenant:
		filters["tenant"] = tenant
	if status:
		filters["status"] = status
	if category:
		filters["category"] = category

	rows = frappe.get_list(
		"Tenant Complaint", filters=filters,
		fields=["name", "tenant", "tenant_name", "property", "unit", "category",
				"priority", "status", "subject", "description", "admin_response",
				"responded_by", "responded_at", "creation"],
		order_by="creation desc", limit=int(limit), ignore_permissions=False,
	)

	out = []
	for r in rows:
		prop_name = frappe.db.get_value("Property", r.property, "property_name") if r.property else ""
		unit_no = frappe.db.get_value("Property Unit", r.unit, "unit_number") if r.unit else ""
		if search:
			s = str(search).lower()
			hay = f"{r.tenant_name or r.tenant} {r.subject} {prop_name}".lower()
			if s not in hay:
				continue
		out.append({
			"id": r.name,
			"tenant": r.tenant,
			"tenantName": r.tenant_name or r.tenant,
			"property": r.property,
			"propertyName": prop_name or r.property or "",
			"unit": unit_no or r.unit or "",
			"category": r.category or "General",
			"priority": r.priority or "Medium",
			"status": r.status or "Open",
			"subject": r.subject,
			"description": r.description,
			"response": r.admin_response or "",
			"respondedBy": r.responded_by or None,
			"respondedAt": str(r.responded_at)[:19] if r.responded_at else None,
			"date": str(r.creation)[:10] if r.creation else "",
		})
	return out


@frappe.whitelist()
@envelope
def complaint_stats(property=None, tenant=None):
	"""Counts by status for the complaint dashboard cards."""
	filters = {}
	if property:
		filters["property"] = property
	if tenant:
		filters["tenant"] = tenant
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
def respond_complaint(name, response=None, status=None):
	"""Admin/caretaker: add a response and/or change the status of a complaint."""
	if not frappe.db.exists("Tenant Complaint", name):
		frappe.throw("Complaint not found.")
	doc = frappe.get_doc("Tenant Complaint", name)
	doc.respond(response=response, status=status)
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
@envelope
def tenant_options(property=None):
	"""Lightweight [{id,name}] list of tenants (for the admin complaint filter)."""
	filters = {}
	if property:
		# Tenants who have a complaint on this property (keeps the list relevant).
		names = frappe.get_all("Tenant Complaint", filters={"property": property}, pluck="tenant")
		if not names:
			return []
		filters["name"] = ["in", list(set(names))]
	rows = frappe.get_all("Property Tenant", filters=filters,
						  fields=["name", "tenant_name"], order_by="tenant_name asc")
	return [{"id": r.name, "name": r.tenant_name or r.name} for r in rows]
