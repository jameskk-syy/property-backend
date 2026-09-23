# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ProspectiveTenant(Document):
	def validate(self):
		if not self.phone or not self.full_name:
			frappe.throw("Full Name and Phone Number are required.")
