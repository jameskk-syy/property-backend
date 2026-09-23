# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class PayrollRun(Document):
	def validate(self):
		if not self.payroll_period:
			frappe.throw("Payroll Period (e.g., '2026-09') is required.")

	def generate_payslips(self):
		"""
		Fetches active employees for the property/organization, computes statutory deductions
		(PAYE, SHA, NSSF, Housing Levy), creates Payroll Item records, and aggregates totals.
		"""
		filters = {"status": "Active"}
		if self.organization:
			filters["organization"] = self.organization
		if self.property:
			filters["property"] = self.property

		employees = frappe.get_all("Employee", filters=filters, fields=["name", "employee_name", "gross_salary", "mpesa_phone"])
		if not employees:
			frappe.throw("No active employees found matching the specified property/organization.")

		# Clear previous items if re-generating
		existing_items = frappe.get_all("Payroll Item", filters={"payroll_run": self.name}, pluck="name")
		for item_name in existing_items:
			frappe.delete_doc("Payroll Item", item_name, ignore_permissions=True)

		total_gross = 0.0
		total_deductions = 0.0
		total_net = 0.0

		for emp in employees:
			gross = flt(emp.gross_salary)
			deductions_list = self.calculate_statutory_deductions(gross)

			total_item_ded = sum(d["amount"] for d in deductions_list)
			net = max(0.0, gross - total_item_ded)

			item_doc = frappe.get_doc({
				"doctype": "Payroll Item",
				"payroll_run": self.name,
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"gross_salary": gross,
				"total_deductions": total_item_ded,
				"net_salary": net,
				"payment_status": "Pending",
				"deductions": deductions_list
			})
			item_doc.insert(ignore_permissions=True)

			total_gross += gross
			total_deductions += total_item_ded
			total_net += net

		self.total_gross = total_gross
		self.total_deductions = total_deductions
		self.total_net = total_net
		self.save(ignore_permissions=True)
		return {"status": "Success", "payslips_generated": len(employees), "total_net": total_net}

	@staticmethod
	def calculate_statutory_deductions(gross):
		"""
		Computes Kenya statutory deductions:
		- NSSF: 6% capped at KSh 2,160
		- SHA (Social Health Insurance): 2.75% of gross
		- Housing Levy: 1.5% of gross
		- PAYE: Taxable pay = (Gross - NSSF), Progressive brackets - Personal Relief (2,400)
		"""
		deductions = []

		# 1. NSSF (6% up to 2,160)
		nssf_amount = min(2160.0, flt(gross * 0.06))
		deductions.append({"deduction_type": "NSSF", "amount": round(nssf_amount, 2)})

		# 2. SHA (2.75%)
		sha_amount = flt(gross * 0.0275)
		deductions.append({"deduction_type": "SHA", "amount": round(sha_amount, 2)})

		# 3. Housing Levy (1.5%)
		housing_levy = flt(gross * 0.015)
		deductions.append({"deduction_type": "Housing Levy", "amount": round(housing_levy, 2)})

		# 4. PAYE Tax
		taxable_pay = max(0.0, gross - nssf_amount)
		tax = 0.0

		if taxable_pay <= 24000:
			tax = taxable_pay * 0.10
		elif taxable_pay <= 32333:
			tax = (24000 * 0.10) + ((taxable_pay - 24000) * 0.25)
		elif taxable_pay <= 500000:
			tax = (24000 * 0.10) + (8333 * 0.25) + ((taxable_pay - 32333) * 0.30)
		elif taxable_pay <= 800000:
			tax = (24000 * 0.10) + (8333 * 0.25) + (467667 * 0.30) + ((taxable_pay - 500000) * 0.325)
		else:
			tax = (24000 * 0.10) + (8333 * 0.25) + (467667 * 0.30) + (300000 * 0.325) + ((taxable_pay - 800000) * 0.35)

		personal_relief = 2400.0
		net_paye = max(0.0, tax - personal_relief)
		deductions.append({"deduction_type": "PAYE", "amount": round(net_paye, 2)})

		return deductions
