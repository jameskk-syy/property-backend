# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MeterReading(Document):
	def validate(self):
		if not self.captured_by:
			self.captured_by = frappe.session.user

		# Respect a manually supplied previous_reading (first reading or a
		# correction). Only auto-detect the last reading when none was provided.
		manual = self.get("previous_reading")
		if manual in (None, ""):
			last_reading = frappe.db.get_value(
				"Meter Reading",
				{
					"unit": self.unit,
					"utility_type": self.utility_type,
					"name": ["!=", self.name or ""]
				},
				"current_reading",
				order_by="reading_date desc, creation desc"
			) or 0.0
			self.previous_reading = float(last_reading)
		else:
			self.previous_reading = float(manual)
		if float(self.current_reading) < self.previous_reading:
			frappe.throw(
				f"Current reading ({self.current_reading}) cannot be less than previous reading ({self.previous_reading})."
			)

		self.consumption = float(self.current_reading) - self.previous_reading
		self.billing_amount = self.consumption * float(self.rate_per_unit or 0.0)
