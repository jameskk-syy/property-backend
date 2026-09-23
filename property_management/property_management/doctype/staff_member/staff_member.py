# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class StaffMember(Document):
	def validate(self):
		if not self.staff_name or not self.gross_salary or float(self.gross_salary) <= 0:
			frappe.throw("Staff Name and Gross Salary (> 0) are required.")
