import frappe


def run():
    from property_management.api.directory import onboard_tenant

    tenant = frappe.get_doc("Property Tenant", "James Maina John")
    unit_name = "PROP-F3EE24-A-1"
    unit = frappe.get_doc("Property Unit", unit_name)
    prop = unit.property

    # onboard_tenant creates a NEW tenant; James already exists. So instead we
    # create the lease directly for the existing tenant and mark the unit occupied,
    # mirroring what onboard_tenant does for its unit step.
    lease = frappe.get_doc({
        "doctype": "Lease Agreement",
        "organization": tenant.organization,
        "property": prop,
        "unit": unit_name,
        "tenant": tenant.name,
        "status": "Active",
        "start_date": frappe.utils.nowdate(),
        "end_date": frappe.utils.add_months(frappe.utils.nowdate(), 12),
        "rent_amount": unit.base_rent or 10,
        "deposit_amount": unit.security_deposit or 0,
    })
    lease.flags.ignore_mandatory = True
    lease.insert(ignore_permissions=True)

    frappe.db.set_value("Property Unit", unit_name, "status", "Occupied")
    frappe.db.commit()

    print("Lease created:", lease.name, "| rent:", lease.rent_amount, "| deposit:", lease.deposit_amount)
    print("Unit status now:", frappe.db.get_value("Property Unit", unit_name, "status"))
