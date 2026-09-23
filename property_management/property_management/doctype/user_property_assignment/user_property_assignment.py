# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class UserPropertyAssignment(Document):
	def validate(self):
		if not self.user or not self.property:
			frappe.throw("User and Property are required.")
