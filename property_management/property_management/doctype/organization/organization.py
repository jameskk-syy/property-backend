# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Organization(Document):
	def validate(self):
		if not self.organization_name:
			frappe.throw("Organization name is required.")
