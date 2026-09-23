# Copyright (c) 2026, Property Management and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class HeldTenantItem(Document):
	def before_insert(self):
		if not self.held_on:
			self.held_on = frappe.utils.now_datetime()
