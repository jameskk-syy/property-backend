# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class TenantComplaint(Document):
	def validate(self):
		if not self.subject or not self.description:
			frappe.throw("Subject and description are required.")
		# Keep a denormalized tenant display name for easy listing/search.
		if self.tenant and not self.tenant_name:
			self.tenant_name = frappe.db.get_value("Property Tenant", self.tenant, "tenant_name") or self.tenant

	def respond(self, response=None, status=None, user=None):
		"""Admin/caretaker responds and/or updates the status of a complaint."""
		if not user:
			user = frappe.session.user
		if response:
			self.admin_response = response
			self.responded_by = user
			self.responded_at = now_datetime()
		if status:
			self.status = status
		self.save(ignore_permissions=True)
