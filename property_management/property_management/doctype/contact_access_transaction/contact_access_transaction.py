# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ContactAccessTransaction(Document):
	def validate(self):
		if not self.prospective_tenant or not self.property:
			frappe.throw("Prospective Tenant and Property are required.")
