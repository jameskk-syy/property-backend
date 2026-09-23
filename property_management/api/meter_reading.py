# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import nowdate


@frappe.whitelist()
def capture_meter_reading(unit_id, utility_type, current_reading, rate_per_unit=150,
						  reading_date=None, photo_url=None, previous_reading=None):
	"""
	API for Caretakers to submit meter readings and auto-calculate utility consumption.

	The endpoint itself is the authorization boundary (whitelisted for caretakers),
	so the document is inserted with ignore_permissions. An optional previous_reading
	may be supplied to override the auto-detected last reading (e.g. first reading
	or a correction).
	"""
	if not unit_id or not utility_type or current_reading is None:
		frappe.throw(_("Unit, utility type, and current reading are required."))

	unit = frappe.get_doc("Property Unit", unit_id)

	values = {
		"doctype": "Meter Reading",
		"organization": unit.organization,
		"property": unit.property,
		"unit": unit.name,
		"utility_type": utility_type,
		"reading_date": reading_date or nowdate(),
		"current_reading": float(current_reading),
		"rate_per_unit": float(rate_per_unit),
		"reading_photo": photo_url,
		"captured_by": frappe.session.user,
	}
	if previous_reading not in (None, ""):
		values["previous_reading"] = float(previous_reading)

	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)

	return {
		"status": "Success",
		"meter_reading_id": doc.name,
		"previous_reading": doc.previous_reading,
		"current_reading": doc.current_reading,
		"consumption": doc.consumption,
		"billed_amount": doc.billing_amount
	}


@frappe.whitelist()
def list_meter_readings(property=None, unit=None, captured_by=None, limit=100):
	"""
	List meter readings (server-side, so caretakers aren't blocked by the
	Meter Reading doctype's role permissions the way /resource is).
	"""
	filters = {}
	if property:
		filters["property"] = property
	if unit:
		filters["unit"] = unit
	if captured_by:
		filters["captured_by"] = captured_by
	return frappe.get_all(
		"Meter Reading", filters=filters,
		fields=["name", "property", "unit", "utility_type", "reading_date",
				"previous_reading", "current_reading", "consumption",
				"rate_per_unit", "billing_amount", "captured_by"],
		order_by="reading_date desc, creation desc", limit=int(limit),
	)
