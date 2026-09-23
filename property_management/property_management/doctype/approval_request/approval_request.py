# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ApprovalRequest(Document):
	def validate(self):
		if not self.reference_doctype or not self.reference_name:
			frappe.throw("Reference DocType and Reference Name are required.")
