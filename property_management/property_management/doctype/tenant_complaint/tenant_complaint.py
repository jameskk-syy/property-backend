# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class TenantComplaint(Document):
	"""
	Tenant Complaint (Feedback) - raised by tenants or caretakers.
	
	Fields:
	- raised_by_type: "Tenant" or "Caretaker"
	- raised_by_user: Link to User who raised it
	- tenant / tenant_name / mobile_number: tenant info (auto-filled)
	- caretaker / caretaker_name: caretaker info (if raised by caretaker)
	- property / unit: auto-filled from tenant's lease or caretaker assignment
	- feedback: the feedback text (replaces subject + description)
	- category, priority, status: as before
	"""

	def validate(self):
		if not self.feedback:
			frappe.throw("Feedback is required.")
		
		# Denormalize tenant name for easy listing/search
		if self.tenant and not self.tenant_name:
			self.tenant_name = frappe.db.get_value("Property Tenant", self.tenant, "tenant_name") or self.tenant
		
		# Denormalize mobile number from tenant
		if self.tenant and not self.mobile_number:
			self.mobile_number = frappe.db.get_value("Property Tenant", self.tenant, "phone") or ""
		
		# Denormalize caretaker name
		if self.caretaker and not self.caretaker_name:
			self.caretaker_name = frappe.db.get_value("Property Caretaker", self.caretaker, "caretaker_name") or self.caretaker

	def respond(self, response=None, status=None, user=None):
		"""Admin/caretaker responds and/or updates the status of feedback."""
		if not user:
			user = frappe.session.user
		if response:
			self.admin_response = response
			self.responded_by = user
			self.responded_at = now_datetime()
		if status:
			self.status = status
		self.save(ignore_permissions=True)
