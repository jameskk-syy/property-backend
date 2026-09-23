# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt


class PropertyJournalEntry(Document):
	def validate(self):
		if not self.property or not self.posting_date:
			frappe.throw("Property and Posting Date are required.")

		if not self.get("lines") or len(self.lines) < 2:
			frappe.throw("A valid Property Journal Entry must contain at least 2 line items (Debit and Credit).")

		total_debit = sum(flt(line.debit) for line in self.lines)
		total_credit = sum(flt(line.credit) for line in self.lines)

		self.total_debit = total_debit
		self.total_credit = total_credit

		if abs(total_debit - total_credit) > 0.01:
			frappe.throw(f"Unbalanced Property Journal Entry: Total Debit ({total_debit:,.2f}) must equal Total Credit ({total_credit:,.2f}).")

	def post(self, user=None):
		"""Posts the journal entry into the property accounting ledger."""
		self.validate()
		self.status = "Posted"
		self.posted_by = user or frappe.session.user
		self.posted_at = now_datetime()
		self.save(ignore_permissions=True)
