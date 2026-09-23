# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Feature gating for the ERPNext/HRMS integration layer.

The integration is considered enabled for an Organization once its ERPNext
Company has been provisioned. This lets us migrate organization-by-organization
without breaking those not yet cut over.
"""

import frappe

# Global kill-switch stored in site config; defaults to enabled.
_SITE_FLAG = "erpnext_integration_enabled"

# Standard statutory / control constants (Kenya, KES).
DEFAULT_CURRENCY = "KES"
COUNTRY = "Kenya"


def is_globally_enabled() -> bool:
	"""Site-wide switch. Set `bench set-config erpnext_integration_enabled 0` to disable."""
	val = frappe.conf.get(_SITE_FLAG)
	# Absent -> enabled by default.
	return True if val is None else bool(int(val))


def company_for_organization(organization: str) -> str | None:
	"""Return the provisioned ERPNext Company linked to an Organization, if any."""
	if not organization:
		return None
	# Link is stored on the Organization doc (custom field added by provisioning).
	company = frappe.db.get_value("Organization", organization, "erpnext_company")
	if company and frappe.db.exists("Company", company):
		return company
	# Fallback: match by company name == organization name.
	if frappe.db.exists("Company", organization):
		return organization
	return None


def is_enabled(organization: str | None = None) -> bool:
	"""
	True when integration should handle a financial event.

	If an organization is given, it must also have a provisioned Company.
	"""
	if not is_globally_enabled():
		return False
	if organization is None:
		return True
	return company_for_organization(organization) is not None


def require_company(organization: str) -> str:
	"""Return the Company for an Organization or raise a clear error."""
	company = company_for_organization(organization)
	if not company:
		frappe.throw(
			f"Organization '{organization}' has no provisioned ERPNext Company. "
			f"Run property_management.integration.erpnext_setup.provision_company first."
		)
	return company
