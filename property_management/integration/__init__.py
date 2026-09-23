# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Integration layer that bridges property-domain DocTypes to native ERPNext / HRMS.

Instead of the legacy custom ledger (Ledger Account / Journal Entry), financial
events delegate to native ERPNext documents:

    Organization         -> Company
    Property             -> Cost Center (+ "Property" accounting dimension)
    Construction Project -> ERPNext Project
    Tenant               -> Customer
    Vendor               -> Supplier
    Employee             -> HRMS Employee
    Property Invoice     -> Sales Invoice
    Property Payment     -> Payment Entry
    Property Expense     -> Purchase Invoice
    Payroll Run          -> Payroll Entry -> Salary Slip

All modules are gated by `integration.settings.is_enabled()` so the app keeps
working during incremental cutover.
"""
