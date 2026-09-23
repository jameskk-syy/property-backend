# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpenseVendor(Document):
	def validate(self):
		if not self.vendor_name:
			frappe.throw("Vendor Name is required.")
