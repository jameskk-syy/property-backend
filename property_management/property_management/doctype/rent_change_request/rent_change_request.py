# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Rent Change Request.

A caretaker proposes a new rent (often a discount) for a tenant's lease. It stays
in 'Pending Approval' until an admin / Super Admin / Director approves it via the
maker-checker Approval Request. On approval, the new rent is written to the lease.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class RentChangeRequest(Document):
	def validate(self):
		if not self.lease:
			frappe.throw("A lease is required for a rent change request.")

		lease = frappe.get_doc("Lease Agreement", self.lease)
		# Backfill context from the lease so the request is self-describing.
		self.property = self.property or lease.property
		self.unit = self.unit or lease.unit
		self.tenant = self.tenant or lease.tenant
		self.organization = self.organization or lease.get("organization")
		if self.current_rent in (None, ""):
			self.current_rent = flt(lease.rent_amount)

		if self.new_rent in (None, "") or flt(self.new_rent) < 0:
			frappe.throw("A valid new rent (>= 0) is required.")

		# Derive the discount for reporting when the new rent is lower.
		self.discount_amount = max(0.0, flt(self.current_rent) - flt(self.new_rent))

	def approve(self, user=None, comment=None):
		"""Apply the new rent to the lease and mark the request approved."""
		if not user:
			user = frappe.session.user

		# Maker-checker: the requester cannot approve their own change.
		if user != "Administrator" and user == (self.requested_by or self.owner):
			frappe.throw("Maker-Checker Rule Violation: Creator cannot approve their own rent change.")

		lease = frappe.get_doc("Lease Agreement", self.lease)
		lease.rent_amount = flt(self.new_rent)
		lease.save(ignore_permissions=True)

		self.status = "Approved"
		self.approved_by = user
		self.approved_at = now_datetime()
		if comment:
			self.decision_comment = comment
		self.save(ignore_permissions=True)

	def reject(self, user=None, comment=None):
		"""Reject the request without touching the lease rent."""
		if not user:
			user = frappe.session.user
		self.status = "Rejected"
		self.approved_by = user
		self.approved_at = now_datetime()
		if comment:
			self.decision_comment = comment
		self.save(ignore_permissions=True)
