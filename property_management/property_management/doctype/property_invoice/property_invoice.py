# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class PropertyInvoice(Document):
	def validate(self):
		self.set_property_accounts()
		self.calculate_totals()
		self.update_invoice_status()

	def set_property_accounts(self):
		if self.property:
			prop = frappe.get_doc("Property", self.property)
			self.receivables_account = prop.receivables_account
			if self.invoice_type in ["Water", "Electricity"]:
				self.income_account = prop.utility_income_account
			else:
				self.income_account = prop.rent_income_account

	def calculate_totals(self):
		total = 0.0
		for item in self.get("items", []):
			item.amount = float(item.quantity or 1) * float(item.rate or 0)
			total += item.amount

		self.total_amount = total
		self.paid_amount = float(self.paid_amount or 0.0)
		self.outstanding_amount = max(0.0, self.total_amount - self.paid_amount)

	def update_invoice_status(self):
		if self.total_amount > 0 and self.outstanding_amount == 0:
			self.status = "Paid"
		elif 0 < self.paid_amount < self.total_amount:
			self.status = "Partially Paid"
		elif self.due_date and getdate(self.due_date) < getdate(nowdate()) and self.outstanding_amount > 0:
			self.status = "Overdue"

	def on_update(self):
		self.bridge_to_sales_invoice()

	def bridge_to_sales_invoice(self):
		"""
		Create a native ERPNext Sales Invoice for this Property Invoice once the
		integration is enabled for the organization. No-op if already bridged or
		if the organization has no provisioned Company yet.
		"""
		from property_management.integration.settings import is_enabled

		if self.get("sales_invoice"):
			return
		if not self.property or not self.total_amount or float(self.total_amount) <= 0:
			return
		if not is_enabled(self.organization):
			return

		from property_management.integration.invoicing import create_invoice_for_property_invoice

		si_name = create_invoice_for_property_invoice(self.name, submit=True)
		# Store link without re-triggering on_update.
		self.db_set("sales_invoice", si_name, update_modified=False)
