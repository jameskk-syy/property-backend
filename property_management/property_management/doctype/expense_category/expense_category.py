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
	Seed a baseline set of expense categories (idempotent). Mirrors the legacy
	hardcoded Select options so existing data keeps working after the migration
	to a Link field.
	"""
	defaults = [
		("Plumbing", "Plumbing Expenses"),
		("Electrical", "Electrical Expenses"),
		("General", "General Expenses"),
		("Painting", "Painting Expenses"),
		("Carpentry", "Carpentry Expenses"),
		("Utility", "Utility Expenses"),
		("Maintenance", "Maintenance Expenses"),
		("Other", "Other Expenses"),
	]
	for category_name, account_name in defaults:
		if not frappe.db.exists("Expense Category", category_name):
			frappe.get_doc({
				"doctype": "Expense Category",
				"category_name": category_name,
				"account_name": account_name,
				"is_active": 1,
			}).insert(ignore_permissions=True)
	frappe.db.commit()
