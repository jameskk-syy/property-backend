# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PayrollItem(Document):
	def validate(self):
		if not self.employee or not self.gross_salary:
			frappe.throw("Employee and Gross Salary are required.")

		# Recalculate total deductions and net salary
		total_ded = sum(float(d.amount or 0.0) for d in self.get("deductions", []))
		self.total_deductions = total_ded
		self.net_salary = max(0.0, float(self.gross_salary) - total_ded)
