# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpenseCategory(Document):
	def validate(self):
		# The account label defaults to the category name so posting always has a
		# canonical account to resolve/create within each company's chart of accounts.
		if not self.account_name:
			self.account_name = self.category_name


def ensure_default_categories():
	"""
	Seed the organisation's expense categories (idempotent).

	Only "Water & Sewer" and "Repairs & Maintenance" are visible to caretakers
	when they raise an expense; everything else is admin-only. Admins always see
	the full list.
	"""
	# (category_name, account_name, caretaker_visible)
	defaults = [
		("Electricity", "Electricity Expenses", 0),
		("Water & Sewer", "Water & Sewer Expenses", 1),
		("Repairs & Maintenance - Cost of Materials and Labour", "Repairs & Maintenance Expenses", 1),
		("Office Expenses - Stationery, Transport, Airtime etc", "Office Expenses", 0),
		("Land Rent & Rates", "Land Rent & Rates Expenses", 0),
		("Costs for financial transactions - M-Pesa, Bank", "Financial Transaction Costs", 0),
		("Administrative costs: Stationery", "Administrative Stationery Expenses", 0),
		("Dadis Academy", "Dadis Academy Expenses", 0),
		("Oseko Advocates", "Oseko Advocates Expenses", 0),
	]
	for category_name, account_name, caretaker_visible in defaults:
		if not frappe.db.exists("Expense Category", category_name):
			frappe.get_doc({
				"doctype": "Expense Category",
				"category_name": category_name,
				"account_name": account_name,
				"caretaker_visible": caretaker_visible,
				"is_active": 1,
			}).insert(ignore_permissions=True)
	frappe.db.commit()
