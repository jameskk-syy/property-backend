# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ModulePermission(Document):
	def validate(self):
		if not self.role or not self.module:
			frappe.throw("Role and Module are required.")
