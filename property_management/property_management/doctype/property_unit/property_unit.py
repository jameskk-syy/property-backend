# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PropertyUnit(Document):
	def validate(self):
		if self.base_rent <= 0:
			frappe.throw("Base rent must be greater than zero.")
