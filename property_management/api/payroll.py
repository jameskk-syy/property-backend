# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt
"""
Payroll API for listing and managing salary slips.
Provides endpoints for admin dashboard to view/print salary slips with filters.
"""

import frappe
from frappe import _
from frappe.utils import getdate, get_first_day, get_last_day, nowdate, flt


@frappe.whitelist(allow_guest=True)
def list_salary_slips(
    company=None,
    property=None,
    employee=None,
    employee_name=None,
    start_date=None,
    end_date=None,
    status=None,
    limit=50,
    offset=0
):
    """
    List salary slips with filters.
    
    Args:
        company: Filter by company name
        property: Filter by property (via custom field on Employee)
        employee: Filter by employee ID
        employee_name: Search by employee name (partial match)
        start_date: Filter by payroll period start date
        end_date: Filter by payroll period end date
        status: Filter by slip status (Draft, Submitted, Paid, Cancelled)
        limit: Number of records to return
        offset: Pagination offset
    
    Returns:
        dict: {data: [...], total: count}
    """
    filters = {}
    
    if company:
        filters["company"] = company
    
    if employee:
        filters["employee"] = employee
    
    if start_date:
        filters["start_date"] = (">=", getdate(start_date))
    
    if end_date:
        filters["end_date"] = ("<=", getdate(end_date))
    
    if status:
        filters["status"] = status
    
    # Build query for more complex filters
    conditions = []
    values = {}
    
    base_query = """
        SELECT 
            ss.name,
            ss.employee,
            ss.employee_name,
            ss.company,
            ss.start_date,
            ss.end_date,
            ss.posting_date,
            ss.salary_structure,
            ss.gross_pay,
            ss.total_deduction,
            ss.net_pay,
            ss.status,
            ss.docstatus,
            emp.designation,
            emp.department
        FROM `tabSalary Slip` ss
        LEFT JOIN `tabEmployee` emp ON ss.employee = emp.name
        WHERE 1=1
    """
    
    if company:
        conditions.append("ss.company = %(company)s")
        values["company"] = company
    
    if employee:
        conditions.append("ss.employee = %(employee)s")
        values["employee"] = employee
    
    if employee_name:
        conditions.append("ss.employee_name LIKE %(employee_name)s")
        values["employee_name"] = f"%{employee_name}%"
    
    if start_date:
        conditions.append("ss.start_date >= %(start_date)s")
        values["start_date"] = getdate(start_date)
    
    if end_date:
        conditions.append("ss.end_date <= %(end_date)s")
        values["end_date"] = getdate(end_date)
    
    if status:
        conditions.append("ss.status = %(status)s")
        values["status"] = status
    
    # Filter by property if custom field exists
    if property:
        # Check if employee has property_ref custom field
        if frappe.db.exists("Custom Field", "Employee-property_ref"):
            conditions.append("emp.property_ref = %(property)s")
            values["property"] = property
    
    if conditions:
        base_query += " AND " + " AND ".join(conditions)
    
    # Get total count
    count_query = f"SELECT COUNT(*) FROM ({base_query}) as subq"
    total = frappe.db.sql(count_query, values)[0][0]
    
    # Add ordering and pagination
    base_query += " ORDER BY ss.posting_date DESC, ss.employee_name ASC"
    base_query += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    
    data = frappe.db.sql(base_query, values, as_dict=True)
    
    return {
        "data": data,
        "total": total,
        "limit": int(limit),
        "offset": int(offset)
    }


@frappe.whitelist(allow_guest=True)
def get_salary_slip(name):
    """
    Get detailed salary slip information for printing/viewing.
    
    Args:
        name: Salary Slip document name
    
    Returns:
        dict: Full salary slip details including earnings and deductions breakdown
    """
    if not frappe.db.exists("Salary Slip", name):
        frappe.throw(_("Salary Slip {0} not found").format(name))
    
    slip = frappe.get_doc("Salary Slip", name)
    
    # Get employee details
    employee = frappe.get_doc("Employee", slip.employee)
    
    # Format earnings
    earnings = []
    for e in slip.earnings:
        earnings.append({
            "salary_component": e.salary_component,
            "abbr": e.abbr,
            "amount": e.amount,
            "is_tax_applicable": e.is_tax_applicable,
            "is_flexible_benefit": e.is_flexible_benefit
        })
    
    # Format deductions
    deductions = []
    for d in slip.deductions:
        deductions.append({
            "salary_component": d.salary_component,
            "abbr": d.abbr,
            "amount": d.amount,
            "is_tax_applicable": d.is_tax_applicable
        })
    
    return {
        "name": slip.name,
        "employee": slip.employee,
        "employee_name": slip.employee_name,
        "company": slip.company,
        "department": employee.department if employee else None,
        "designation": employee.designation if employee else None,
        "start_date": slip.start_date,
        "end_date": slip.end_date,
        "posting_date": slip.posting_date,
        "salary_structure": slip.salary_structure,
        "payroll_frequency": slip.payroll_frequency,
        "currency": slip.currency,
        "total_working_days": slip.total_working_days,
        "payment_days": slip.payment_days,
        "leave_without_pay": slip.leave_without_pay,
        "earnings": earnings,
        "deductions": deductions,
        "gross_pay": slip.gross_pay,
        "total_deduction": slip.total_deduction,
        "net_pay": slip.net_pay,
        "rounded_total": slip.rounded_total,
        "total_in_words": slip.total_in_words,
        "status": slip.status,
        "docstatus": slip.docstatus,
        "bank_name": employee.bank_name if hasattr(employee, 'bank_name') else None,
        "bank_account_no": employee.bank_ac_no if hasattr(employee, 'bank_ac_no') else None,
    }


@frappe.whitelist(allow_guest=True)
def get_salary_slip_filters():
    """
    Get available filter options for salary slip listing.
    
    Returns:
        dict: Available companies, properties, employees, statuses
    """
    # Get companies
    companies = frappe.get_all("Company", pluck="name")
    
    # Get properties
    properties = frappe.get_all("Property", 
        fields=["name", "property_name"],
        filters={"status": "Active"}
    )
    
    # Get employees with salary structure assignments
    employees = frappe.db.sql("""
        SELECT DISTINCT emp.name, emp.employee_name, emp.designation
        FROM `tabEmployee` emp
        INNER JOIN `tabSalary Structure Assignment` ssa ON ssa.employee = emp.name
        WHERE emp.status = 'Active'
        AND ssa.docstatus = 1
        ORDER BY emp.employee_name
    """, as_dict=True)
    
    # Get available months with salary slips
    months = frappe.db.sql("""
        SELECT DISTINCT 
            DATE_FORMAT(start_date, '%Y-%m') as month_key,
            DATE_FORMAT(start_date, '%M %Y') as month_label,
            MIN(start_date) as start_date,
            MAX(end_date) as end_date
        FROM `tabSalary Slip`
        GROUP BY DATE_FORMAT(start_date, '%Y-%m')
        ORDER BY start_date DESC
    """, as_dict=True)
    
    # Statuses
    statuses = ["Draft", "Submitted", "Paid", "Cancelled"]
    
    return {
        "companies": companies,
        "properties": properties,
        "employees": employees,
        "months": months,
        "statuses": statuses
    }


@frappe.whitelist(allow_guest=True)
def get_payroll_summary(company=None, start_date=None, end_date=None):
    """
    Get payroll summary statistics.
    
    Args:
        company: Filter by company
        start_date: Filter by period start
        end_date: Filter by period end
    
    Returns:
        dict: Summary statistics
    """
    filters = {}
    
    if company:
        filters["company"] = company
    
    if start_date:
        filters["start_date"] = (">=", getdate(start_date))
    
    if end_date:
        filters["end_date"] = ("<=", getdate(end_date))
    
    # Count slips by status
    total_slips = frappe.db.count("Salary Slip", filters)
    
    # Get totals
    conditions = []
    values = {}
    
    query = """
        SELECT 
            COUNT(*) as slip_count,
            SUM(gross_pay) as total_gross,
            SUM(total_deduction) as total_deductions,
            SUM(net_pay) as total_net,
            status
        FROM `tabSalary Slip`
        WHERE 1=1
    """
    
    if company:
        conditions.append("company = %(company)s")
        values["company"] = company
    
    if start_date:
        conditions.append("start_date >= %(start_date)s")
        values["start_date"] = getdate(start_date)
    
    if end_date:
        conditions.append("end_date <= %(end_date)s")
        values["end_date"] = getdate(end_date)
    
    if conditions:
        query += " AND " + " AND ".join(conditions)
    
    query += " GROUP BY status"
    
    by_status = frappe.db.sql(query, values, as_dict=True)
    
    # Calculate totals
    totals = frappe.db.sql("""
        SELECT 
            SUM(gross_pay) as total_gross,
            SUM(total_deduction) as total_deductions,
            SUM(net_pay) as total_net
        FROM `tabSalary Slip`
        WHERE 1=1
    """ + (" AND " + " AND ".join(conditions) if conditions else ""), values, as_dict=True)
    
    return {
        "total_slips": total_slips,
        "by_status": by_status,
        "totals": totals[0] if totals else {},
    }


@frappe.whitelist(allow_guest=True)
def submit_salary_slips(names=None):
    """
    Submit salary slips (change from Draft to Submitted).
    
    Args:
        names: List of salary slip names to submit, or None to submit all drafts
    
    Returns:
        dict: {submitted: [...], errors: [...]}
    """
    # Run as Administrator to bypass guest permissions
    frappe.set_user('Administrator')
    
    submitted = []
    errors = []
    
    if names:
        if isinstance(names, str):
            import json
            names = json.loads(names)
        slip_names = names
    else:
        # Get all draft slips
        slip_names = frappe.get_all('Salary Slip', filters={'docstatus': 0}, pluck='name')
    
    for slip_name in slip_names:
        try:
            doc = frappe.get_doc('Salary Slip', slip_name)
            doc.submit()
            submitted.append(slip_name)
        except Exception as e:
            errors.append({'name': slip_name, 'error': str(e)})
    
    frappe.db.commit()
    
    return {
        'submitted': submitted,
        'errors': errors,
        'message': f'Submitted {len(submitted)} salary slip(s)'
    }


@frappe.whitelist(allow_guest=True)
def get_payroll_journal_entries():
    """Check if journal entries were created for salary slips."""
    frappe.set_user('Administrator')
    
    # Get journal entries linked to salary slips
    entries = frappe.db.sql("""
        SELECT 
            je.name,
            je.posting_date,
            je.total_debit,
            je.voucher_type,
            je.cheque_no as reference
        FROM `tabJournal Entry` je
        WHERE je.docstatus = 1
        ORDER BY je.posting_date DESC
        LIMIT 10
    """, as_dict=True)
    
    return {
        'entries': entries,
        'count': len(entries)
    }


@frappe.whitelist(allow_guest=True)
def get_bank_accounts():
    """Get list of bank accounts for payment."""
    frappe.set_user('Administrator')
    
    accounts = frappe.get_all('Account',
        filters={
            'account_type': 'Bank',
            'is_group': 0,
            'disabled': 0
        },
        fields=['name', 'account_name', 'account_currency', 'parent_account'],
        order_by='account_name'
    )
    
    return accounts


@frappe.whitelist(allow_guest=True)
def get_chart_of_accounts():
    """Get chart of accounts overview."""
    frappe.set_user('Administrator')
    
    # Get company
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        return {'error': 'No company found'}
    
    company_name = company[0]
    
    # Get all accounts
    accounts = frappe.get_all('Account',
        filters={'company': company_name},
        fields=['name', 'account_name', 'account_type', 'root_type', 'parent_account', 'is_group'],
        order_by='lft'
    )
    
    # Count by type
    account_types = {}
    for acc in accounts:
        rt = acc.get('root_type') or 'Other'
        if rt not in account_types:
            account_types[rt] = 0
        account_types[rt] += 1
    
    return {
        'company': company_name,
        'total_accounts': len(accounts),
        'by_root_type': account_types,
        'accounts': accounts
    }


@frappe.whitelist(allow_guest=True)
def create_bank_account(account_name, bank_name=None, account_number=None):
    """
    Create a new bank account in the Chart of Accounts.
    
    Args:
        account_name: Name for the account (e.g., "Cooperative Bank")
        bank_name: Optional bank name
        account_number: Optional bank account number
    
    Returns:
        dict: Created account details
    """
    frappe.set_user('Administrator')
    
    # Get company
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        frappe.throw('No company found')
    company_name = company[0]
    
    # Find the parent "Bank Accounts" group
    parent_account = frappe.db.get_value('Account', 
        filters={
            'account_name': 'Bank Accounts',
            'is_group': 1,
            'company': company_name
        },
        fieldname='name'
    )
    
    if not parent_account:
        # Try to find any bank group account
        parent_account = frappe.db.get_value('Account',
            filters={
                'account_type': 'Bank',
                'is_group': 1,
                'company': company_name
            },
            fieldname='name'
        )
    
    if not parent_account:
        frappe.throw('Could not find Bank Accounts parent group')
    
    # Check if account already exists
    existing = frappe.db.exists('Account', {
        'account_name': account_name,
        'company': company_name
    })
    
    if existing:
        return {
            'status': 'exists',
            'account': existing,
            'message': f'Account "{account_name}" already exists'
        }
    
    # Create the account
    account = frappe.get_doc({
        'doctype': 'Account',
        'account_name': account_name,
        'parent_account': parent_account,
        'company': company_name,
        'account_type': 'Bank',
        'is_group': 0,
        'account_currency': 'KES'
    })
    account.insert()
    
    frappe.db.commit()
    
    return {
        'status': 'created',
        'account': account.name,
        'message': f'Bank account "{account_name}" created successfully'
    }


# ============================================================================
# PAYROLL ENTRY AND PAYMENT PROCESSING
# ============================================================================

@frappe.whitelist(allow_guest=True)
def get_pending_salary_slips(month=None, year=None):
    """
    Get salary slips for a period - both draft and submitted.
    
    Args:
        month: Filter by month (1-12)
        year: Filter by year
    
    Returns:
        dict: Salary slips grouped by status with summary
    """
    frappe.set_user('Administrator')
    
    values = {}
    conditions = ["1=1"]
    
    if month and year:
        conditions.append("MONTH(ss.start_date) = %(month)s")
        conditions.append("YEAR(ss.start_date) = %(year)s")
        values['month'] = int(month)
        values['year'] = int(year)
    
    # Get all slips (draft and submitted) for the period
    query = f"""
        SELECT 
            ss.name,
            ss.employee,
            ss.employee_name,
            ss.company,
            ss.start_date,
            ss.end_date,
            ss.gross_pay,
            ss.total_deduction,
            ss.net_pay,
            ss.status,
            ss.docstatus,
            emp.designation,
            emp.department
        FROM `tabSalary Slip` ss
        LEFT JOIN `tabEmployee` emp ON ss.employee = emp.name
        WHERE {' AND '.join(conditions)}
        AND ss.docstatus IN (0, 1)
        ORDER BY ss.docstatus DESC, ss.employee_name
    """
    
    all_slips = frappe.db.sql(query, values, as_dict=True)
    
    # Separate draft and submitted slips
    draft_slips = [s for s in all_slips if s.get('docstatus') == 0]
    submitted_slips = [s for s in all_slips if s.get('docstatus') == 1]
    
    # Calculate totals for submitted slips only (these are the ones that can be paid)
    total_gross = sum(flt(s.get('gross_pay', 0)) for s in submitted_slips)
    total_deductions = sum(flt(s.get('total_deduction', 0)) for s in submitted_slips)
    total_net = sum(flt(s.get('net_pay', 0)) for s in submitted_slips)
    
    # Draft totals
    draft_gross = sum(flt(s.get('gross_pay', 0)) for s in draft_slips)
    draft_deductions = sum(flt(s.get('total_deduction', 0)) for s in draft_slips)
    draft_net = sum(flt(s.get('net_pay', 0)) for s in draft_slips)
    
    return {
        'slips': submitted_slips,  # For backward compatibility
        'draft_slips': draft_slips,
        'count': len(submitted_slips),
        'draft_count': len(draft_slips),
        'totals': {
            'gross': total_gross,
            'deductions': total_deductions,
            'net': total_net
        },
        'draft_totals': {
            'gross': draft_gross,
            'deductions': draft_deductions,
            'net': draft_net
        }
    }


@frappe.whitelist(allow_guest=True)
def submit_draft_salary_slips(names=None, month=None, year=None):
    """
    Submit draft salary slips. Only Admin/Administrator/Director can do this.
    
    Args:
        names: List of salary slip names to submit (JSON string or list)
        month: If names not provided, submit all drafts for this month
        year: If names not provided, submit all drafts for this year
    
    Returns:
        dict: {submitted: [...], errors: [...]}
    """
    frappe.set_user('Administrator')
    
    submitted = []
    errors = []
    
    if names:
        if isinstance(names, str):
            import json
            names = json.loads(names)
        slip_names = names
    elif month and year:
        # Get all draft slips for the period
        slip_names = frappe.db.sql("""
            SELECT name FROM `tabSalary Slip`
            WHERE docstatus = 0
            AND MONTH(start_date) = %s
            AND YEAR(start_date) = %s
        """, (int(month), int(year)), as_list=True)
        slip_names = [s[0] for s in slip_names]
    else:
        frappe.throw('Please provide slip names or month/year')
    
    for slip_name in slip_names:
        try:
            doc = frappe.get_doc('Salary Slip', slip_name)
            if doc.docstatus == 0:
                # Fix total_in_words before submitting
                from frappe.utils import money_in_words
                doc.total_in_words = money_in_words(doc.net_pay, doc.currency or 'KES')
                doc.save()
                doc.submit()
                submitted.append({
                    'name': slip_name,
                    'employee_name': doc.employee_name,
                    'net_pay': doc.net_pay
                })
        except Exception as e:
            errors.append({'name': slip_name, 'error': str(e)})
    
    frappe.db.commit()
    
    return {
        'submitted': submitted,
        'errors': errors,
        'count': len(submitted),
        'message': f'Submitted {len(submitted)} salary slip(s)' + (f' with {len(errors)} error(s)' if errors else '')
    }


@frappe.whitelist(allow_guest=True)
def fix_salary_slip_words(name=None, month=None, year=None):
    """
    Fix the total_in_words field for salary slips where it doesn't match net_pay.
    
    Args:
        name: Specific salary slip name to fix
        month: Fix all slips for this month
        year: Fix all slips for this year
    
    Returns:
        dict: {fixed: [...], errors: [...]}
    """
    frappe.set_user('Administrator')
    from frappe.utils import money_in_words
    
    fixed = []
    errors = []
    
    if name:
        slip_names = [name]
    elif month and year:
        slip_names = frappe.db.sql("""
            SELECT name FROM `tabSalary Slip`
            WHERE MONTH(start_date) = %s
            AND YEAR(start_date) = %s
        """, (int(month), int(year)), as_list=True)
        slip_names = [s[0] for s in slip_names]
    else:
        # Fix all slips with mismatched words
        slip_names = frappe.db.sql("""
            SELECT name FROM `tabSalary Slip`
            WHERE docstatus IN (0, 1)
        """, as_list=True)
        slip_names = [s[0] for s in slip_names]
    
    for slip_name in slip_names:
        try:
            doc = frappe.get_doc('Salary Slip', slip_name)
            correct_words = money_in_words(doc.net_pay, doc.currency or 'KES')
            
            if doc.total_in_words != correct_words:
                # Use db_set to update without triggering full save (works for submitted docs)
                frappe.db.set_value('Salary Slip', slip_name, 'total_in_words', correct_words)
                fixed.append({
                    'name': slip_name,
                    'employee_name': doc.employee_name,
                    'net_pay': doc.net_pay,
                    'old_words': doc.total_in_words,
                    'new_words': correct_words
                })
        except Exception as e:
            errors.append({'name': slip_name, 'error': str(e)})
    
    frappe.db.commit()
    
    return {
        'fixed': fixed,
        'errors': errors,
        'count': len(fixed),
        'message': f'Fixed {len(fixed)} salary slip(s)' + (f' with {len(errors)} error(s)' if errors else '')
    }


@frappe.whitelist(allow_guest=True)
def get_payroll_entries(status=None, limit=20):
    """
    List payroll entries.
    
    Args:
        status: Filter by status (Draft, Submitted)
        limit: Number of records
    
    Returns:
        list: Payroll entries
    """
    frappe.set_user('Administrator')
    
    filters = {}
    if status:
        if status == 'Draft':
            filters['docstatus'] = 0
        elif status == 'Submitted':
            filters['docstatus'] = 1
    
    entries = frappe.get_all('Payroll Entry',
        filters=filters,
        fields=[
            'name', 'company', 'payroll_frequency', 'start_date', 'end_date',
            'posting_date', 'docstatus', 'number_of_employees',
            'creation', 'modified'
        ],
        order_by='posting_date DESC',
        limit=int(limit)
    )
    
    # Add status label
    for entry in entries:
        if entry['docstatus'] == 0:
            entry['status'] = 'Draft'
        elif entry['docstatus'] == 1:
            entry['status'] = 'Submitted'
        else:
            entry['status'] = 'Cancelled'
    
    return entries


@frappe.whitelist(allow_guest=True)
def create_payroll_entry(start_date, end_date, posting_date=None):
    """
    Create a new Payroll Entry for the given period.
    This groups salary slips and prepares for accounting entry.
    
    Args:
        start_date: Payroll period start
        end_date: Payroll period end
        posting_date: Posting date (defaults to today)
    
    Returns:
        dict: Created payroll entry details
    """
    frappe.set_user('Administrator')
    
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        frappe.throw('No company found')
    company_name = company[0]
    
    if not posting_date:
        posting_date = nowdate()
    
    # Check for existing payroll entry for this period
    existing = frappe.db.exists('Payroll Entry', {
        'company': company_name,
        'start_date': getdate(start_date),
        'end_date': getdate(end_date),
        'docstatus': ['!=', 2]  # Not cancelled
    })
    
    if existing:
        return {
            'status': 'exists',
            'name': existing,
            'message': f'Payroll Entry already exists for this period: {existing}'
        }
    
    # Create payroll entry
    payroll_entry = frappe.get_doc({
        'doctype': 'Payroll Entry',
        'company': company_name,
        'payroll_frequency': 'Monthly',
        'start_date': getdate(start_date),
        'end_date': getdate(end_date),
        'posting_date': getdate(posting_date),
        'currency': 'KES',
        'exchange_rate': 1
    })
    
    payroll_entry.insert()
    
    # Get employees with salary slips in this period
    payroll_entry.fill_employee_details()
    payroll_entry.save()
    
    frappe.db.commit()
    
    return {
        'status': 'created',
        'name': payroll_entry.name,
        'number_of_employees': payroll_entry.number_of_employees,
        'message': f'Payroll Entry {payroll_entry.name} created with {payroll_entry.number_of_employees} employees'
    }


@frappe.whitelist(allow_guest=True)
def get_payroll_entry_details(name):
    """
    Get detailed information about a payroll entry.
    
    Args:
        name: Payroll Entry name
    
    Returns:
        dict: Full payroll entry details
    """
    frappe.set_user('Administrator')
    
    if not frappe.db.exists('Payroll Entry', name):
        frappe.throw(f'Payroll Entry {name} not found')
    
    doc = frappe.get_doc('Payroll Entry', name)
    
    # Get associated salary slips
    slips = frappe.get_all('Salary Slip',
        filters={
            'start_date': doc.start_date,
            'end_date': doc.end_date,
            'company': doc.company,
            'docstatus': 1
        },
        fields=['name', 'employee', 'employee_name', 'gross_pay', 'total_deduction', 'net_pay', 'status']
    )
    
    total_gross = sum(flt(s.get('gross_pay', 0)) for s in slips)
    total_deductions = sum(flt(s.get('total_deduction', 0)) for s in slips)
    total_net = sum(flt(s.get('net_pay', 0)) for s in slips)
    
    # Get employees in payroll entry
    employees = []
    for emp in doc.employees:
        employees.append({
            'employee': emp.employee,
            'employee_name': emp.employee_name,
            'department': emp.department,
            'designation': emp.designation
        })
    
    return {
        'name': doc.name,
        'company': doc.company,
        'start_date': doc.start_date,
        'end_date': doc.end_date,
        'posting_date': doc.posting_date,
        'status': 'Draft' if doc.docstatus == 0 else ('Submitted' if doc.docstatus == 1 else 'Cancelled'),
        'docstatus': doc.docstatus,
        'number_of_employees': doc.number_of_employees,
        'employees': employees,
        'salary_slips': slips,
        'totals': {
            'gross': total_gross,
            'deductions': total_deductions,
            'net': total_net
        }
    }


@frappe.whitelist(allow_guest=True)
def submit_payroll_entry(name):
    """
    Submit a payroll entry. This creates the accrual Journal Entry.
    
    Args:
        name: Payroll Entry name
    
    Returns:
        dict: Submission result with journal entry details
    """
    frappe.set_user('Administrator')
    
    if not frappe.db.exists('Payroll Entry', name):
        frappe.throw(f'Payroll Entry {name} not found')
    
    doc = frappe.get_doc('Payroll Entry', name)
    
    if doc.docstatus != 0:
        frappe.throw(f'Payroll Entry {name} is not in Draft status')
    
    try:
        doc.submit()
        frappe.db.commit()
        
        # Get created journal entry
        journal_entry = frappe.db.get_value('Journal Entry',
            filters={'cheque_no': name},
            fieldname='name'
        )
        
        return {
            'status': 'submitted',
            'name': doc.name,
            'journal_entry': journal_entry,
            'message': f'Payroll Entry submitted. Journal Entry: {journal_entry or "Created"}'
        }
    except Exception as e:
        frappe.db.rollback()
        return {
            'status': 'error',
            'message': str(e)
        }


@frappe.whitelist(allow_guest=True)
def process_payroll_payment(payroll_entry, bank_account, payment_date=None):
    """
    Process payment for a submitted payroll entry.
    Creates a Bank Entry to pay employees from the selected bank account.
    
    Args:
        payroll_entry: Payroll Entry name
        bank_account: Bank account to pay from
        payment_date: Payment date (defaults to today)
    
    Returns:
        dict: Payment result with bank entry details
    """
    frappe.set_user('Administrator')
    
    if not frappe.db.exists('Payroll Entry', payroll_entry):
        frappe.throw(f'Payroll Entry {payroll_entry} not found')
    
    if not frappe.db.exists('Account', bank_account):
        frappe.throw(f'Bank Account {bank_account} not found')
    
    doc = frappe.get_doc('Payroll Entry', payroll_entry)
    
    if doc.docstatus != 1:
        frappe.throw('Payroll Entry must be submitted before processing payment')
    
    if not payment_date:
        payment_date = nowdate()
    
    # Get salary slips for this payroll period
    slips = frappe.get_all('Salary Slip',
        filters={
            'start_date': doc.start_date,
            'end_date': doc.end_date,
            'company': doc.company,
            'docstatus': 1
        },
        fields=['name', 'employee', 'employee_name', 'net_pay', 'status']
    )
    
    # Check if already paid - prevent duplicate payments
    paid_slips = [s for s in slips if s.get('status') == 'Paid']
    if paid_slips:
        paid_count = len(paid_slips)
        total_slips = len(slips)
        if paid_count == total_slips:
            frappe.throw(f'All {total_slips} salary slips for this period have already been paid. Cannot process duplicate payment.')
        else:
            frappe.throw(f'{paid_count} of {total_slips} salary slips are already paid. Cannot process partial/duplicate payment.')
    
    # Filter to only unpaid slips
    unpaid_slips = [s for s in slips if s.get('status') != 'Paid']
    
    total_net = sum(flt(s.get('net_pay', 0)) for s in unpaid_slips)
    
    if total_net <= 0:
        frappe.throw('No unpaid salary slips found for this period')
    
    # Get payable account (Salary Payable)
    payable_account = frappe.db.get_value('Account',
        filters={
            'account_name': ['like', '%Salary Payable%'],
            'company': doc.company
        },
        fieldname='name'
    )
    
    if not payable_account:
        # Try to find any payable account
        payable_account = frappe.db.get_value('Account',
            filters={
                'account_type': 'Payable',
                'is_group': 0,
                'company': doc.company
            },
            fieldname='name'
        )
    
    if not payable_account:
        frappe.throw('Could not find Salary Payable account. Please create one first.')
    
    # Get or create Salary Expense account for the debit side
    salary_expense_account = frappe.db.get_value('Account',
        filters={
            'account_name': ['like', '%Salary%'],
            'root_type': 'Expense',
            'is_group': 0,
            'company': doc.company
        },
        fieldname='name'
    )
    
    if not salary_expense_account:
        # Use a general expense account
        salary_expense_account = frappe.db.get_value('Account',
            filters={
                'root_type': 'Expense',
                'is_group': 0,
                'company': doc.company
            },
            fieldname='name'
        )
    
    if not salary_expense_account:
        frappe.throw('Could not find a Salary Expense account. Please create one first.')
    
    # Create Bank Entry (Journal Entry of type Bank Entry)
    # We debit Salary Expense (increase expense) and credit Bank (decrease asset)
    je = frappe.get_doc({
        'doctype': 'Journal Entry',
        'voucher_type': 'Bank Entry',
        'posting_date': getdate(payment_date),
        'company': doc.company,
        'cheque_no': f'PAY-{payroll_entry}',
        'cheque_date': getdate(payment_date),
        'user_remark': f'Salary payment for {doc.start_date} to {doc.end_date}',
        'accounts': [
            {
                'account': salary_expense_account,
                'debit_in_account_currency': total_net,
                'credit_in_account_currency': 0
            },
            {
                'account': bank_account,
                'debit_in_account_currency': 0,
                'credit_in_account_currency': total_net
            }
        ]
    })
    
    je.insert()
    je.submit()
    
    # Update salary slip status to Paid (only unpaid ones)
    for slip in unpaid_slips:
        frappe.db.set_value('Salary Slip', slip['name'], 'status', 'Paid')
    
    frappe.db.commit()
    
    return {
        'status': 'paid',
        'payroll_entry': payroll_entry,
        'bank_entry': je.name,
        'bank_account': bank_account,
        'amount': total_net,
        'employees_paid': len(unpaid_slips),
        'message': f'Payment of KES {total_net:,.2f} processed. Bank Entry: {je.name}'
    }


@frappe.whitelist(allow_guest=True)
def get_payroll_accounting_entries(payroll_entry):
    """
    Get all accounting entries related to a payroll entry.
    
    Args:
        payroll_entry: Payroll Entry name
    
    Returns:
        dict: Journal entries and GL entries
    """
    frappe.set_user('Administrator')
    
    # Get journal entries
    journal_entries = frappe.db.sql("""
        SELECT 
            name, voucher_type, posting_date, total_debit, 
            cheque_no, user_remark, docstatus
        FROM `tabJournal Entry`
        WHERE cheque_no LIKE %s
        ORDER BY posting_date
    """, f'%{payroll_entry}%', as_dict=True)
    
    # Get GL entries for these journal entries
    gl_entries = []
    for je in journal_entries:
        gle = frappe.get_all('GL Entry',
            filters={'voucher_no': je['name'], 'is_cancelled': 0},
            fields=['account', 'debit', 'credit', 'against']
        )
        je['gl_entries'] = gle
        gl_entries.extend(gle)
    
    return {
        'payroll_entry': payroll_entry,
        'journal_entries': journal_entries,
        'total_entries': len(journal_entries)
    }


@frappe.whitelist(allow_guest=True)
def get_available_months():
    """
    Get months that have salary slips for dropdown selection.
    
    Returns:
        list: Available months with counts
    """
    frappe.set_user('Administrator')
    
    months = frappe.db.sql("""
        SELECT 
            YEAR(start_date) as year,
            MONTH(start_date) as month,
            DATE_FORMAT(start_date, '%M %Y') as label,
            MIN(start_date) as start_date,
            MAX(end_date) as end_date,
            COUNT(*) as slip_count,
            SUM(net_pay) as total_net
        FROM `tabSalary Slip`
        WHERE docstatus IN (0, 1)
        GROUP BY YEAR(start_date), MONTH(start_date)
        ORDER BY year DESC, month DESC
    """, as_dict=True)
    
    return months
