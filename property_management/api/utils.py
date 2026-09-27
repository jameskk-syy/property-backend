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


def paginate(doctype, filters=None, fields=None, order_by='creation desc', page=1, page_size=8, search=None, search_fields=None):
	"""
	Generic pagination helper for list APIs.
	
	Args:
		doctype: Frappe DocType name
		filters: dict of filters
		fields: list of fields to return
		order_by: sort order (default: creation desc)
		page: page number (1-indexed)
		page_size: records per page (default: 8, max: 100)
		search: optional search term
		search_fields: list of fields to search in
	
	Returns:
		dict with data and pagination metadata
	"""
	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	offset = (page - 1) * page_size
	
	# Build filters
	filters = filters or {}
	
	# Add search filter if provided
	or_filters = None
	if search and search_fields:
		search_term = f'%{search}%'
		or_filters = []
		for field in search_fields:
			or_filters.append([doctype, field, 'like', search_term])
	
	# Get total count (with or without search)
	if or_filters:
		# Count with OR search conditions
		total = len(frappe.get_all(
			doctype,
			filters=filters,
			or_filters=or_filters,
			pluck='name'
		))
		
		# Get data with search
		data = frappe.get_all(
			doctype,
			filters=filters,
			or_filters=or_filters,
			fields=fields or ['name'],
			order_by=order_by,
			limit_start=offset,
			limit_page_length=page_size
		)
	else:
		# Standard count and fetch
		total = frappe.db.count(doctype, filters)
		
		data = frappe.get_all(
			doctype,
			filters=filters,
			fields=fields or ['name'],
			order_by=order_by,
			limit_start=offset,
			limit_page_length=page_size
		)
	
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': data,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}
