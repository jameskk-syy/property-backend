# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Frontend-facing API v2.

Thin, authenticated endpoints that wrap the ERPNext/HRMS integration layer and
return a consistent envelope:

    { "status": "success" | "error", "data": <payload>, "message": <str> }

Base URL pattern:
    /api/method/property_management.api.v2.<module>.<function>
"""

import functools

import frappe


def envelope(fn):
	"""Wrap a function so it always returns {status, data, message} and never leaks tracebacks."""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		try:
			data = fn(*args, **kwargs)
			return {"status": "success", "data": data, "message": ""}
		except frappe.PermissionError as e:
			frappe.local.response["http_status_code"] = 403
			return {"status": "error", "data": None, "message": str(e) or "Not permitted"}
		except frappe.ValidationError as e:
			frappe.local.response["http_status_code"] = 400
			return {"status": "error", "data": None, "message": str(e)}
		except Exception as e:
			frappe.log_error(title="api.v2 error", message=frappe.get_traceback())
			frappe.local.response["http_status_code"] = 500
			return {"status": "error", "data": None, "message": str(e)}

	return wrapper
