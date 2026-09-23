# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PropertyPayment(Document):
	def validate(self):
		if self.property:
			prop = frappe.get_doc("Property", self.property)
			self.collection_account = prop.collection_account
			self.receivables_account = prop.receivables_account

		if self.invoice:
			inv = frappe.get_doc("Property Invoice", self.invoice)
			self.tenant = inv.tenant
			self.property = inv.property

	def on_submit(self):
		self.update_invoice_payment(add=True)
		if not self.bridge_to_payment_entry():
			# Legacy custom-ledger posting only when integration is disabled.
			self.post_payment_journal_entry()

	def on_cancel(self):
		self.update_invoice_payment(add=False)
		# Reverse the native Payment Entry (native cancel reverses the GL).
		if self.get("payment_entry") and frappe.db.exists("Payment Entry", self.get("payment_entry")):
			pe = frappe.get_doc("Payment Entry", self.get("payment_entry"))
			if pe.docstatus == 1:
				pe.cancel()

	def bridge_to_payment_entry(self):
		"""
		Create a native ERPNext Payment Entry for this payment when integration is
		enabled for the organization. Returns True if a Payment Entry was created
		(or already exists), False to fall back to legacy posting.
		"""
		from property_management.integration.settings import is_enabled

		if self.get("payment_entry"):
			return True
		if not self.property or not self.amount_paid or float(self.amount_paid) <= 0:
			return False
		if not is_enabled(self.organization):
			return False

		from property_management.integration.payments import create_payment_for_property_payment

		pe_name = create_payment_for_property_payment(self.name, submit=True)
		self.db_set("payment_entry", pe_name, update_modified=False)
		return True

	def update_invoice_payment(self, add=True):
		if not self.invoice:
			return

		inv = frappe.get_doc("Property Invoice", self.invoice)
		if add:
			paid = float(inv.paid_amount or 0.0) + float(self.amount_paid)
		else:
			paid = float(inv.paid_amount or 0.0) - float(self.amount_paid)

		outstanding = max(0.0, float(inv.total_amount) - paid)
		if float(inv.total_amount) > 0 and outstanding == 0:
			status = "Paid"
		elif 0 < paid < float(inv.total_amount):
			status = "Partially Paid"
		else:
			status = "Unpaid"

		# Update only the payment-tracking fields to avoid re-validating unrelated
		# mandatory fields on legacy/imported invoices.
		inv.db_set("paid_amount", paid, update_modified=False)
		inv.db_set("outstanding_amount", outstanding, update_modified=False)
		inv.db_set("status", status, update_modified=False)

	def post_payment_journal_entry(self):
		from property_management.api.accounting import post_journal_entry

		if not self.property or not self.amount_paid or float(self.amount_paid) <= 0:
			return

		coll_acc = self.collection_account or frappe.db.get_value(
			"Ledger Account", {"property": self.property, "account_type": "Asset", "is_group": 0}, "name"
		)
		rec_acc = self.receivables_account or frappe.db.get_value(
			"Ledger Account", {"property": self.property, "account_type": "Asset", "is_group": 0}, "name"
		)

		if not coll_acc or not rec_acc:
			return

		lines = [
			{"account": coll_acc, "debit": float(self.amount_paid), "credit": 0.0},
			{"account": rec_acc, "debit": 0.0, "credit": float(self.amount_paid)}
		]

		post_journal_entry(
			property_id=self.property,
			lines=lines,
			memo=f"Rent Payment Post: {self.payment_reference or self.name}",
			ref_doctype="Property Payment",
			ref_name=self.name,
			organization=self.organization
		)
