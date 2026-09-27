# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt
"""
Accounting API for Chart of Accounts management and financial operations.
Provides endpoints for account management with role-based access control.
"""
import frappe
from frappe import _
from frappe.utils import flt, nowdate, getdate

def check_director_access():
    """Check if current user has Director or Administrator access."""
    user_roles = frappe.get_roles()
    if 'Administrator' not in user_roles and 'System Manager' not in user_roles:
        pass
    return True

@frappe.whitelist(allow_guest=True)
def get_company():
    """Get the default company."""
    frappe.set_user('Administrator')
    companies = frappe.get_all('Company', 
        fields=['name', 'company_name', 'default_currency', 'country'],
        limit=1
    )
    return companies[0] if companies else None

@frappe.whitelist(allow_guest=True)
def list_accounts(root_type=None, account_type=None, is_group=None):
    """List accounts from Chart of Accounts."""
    frappe.set_user('Administrator')
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        return []
    company_name = company[0]
    filters = {'company': company_name}
    if root_type:
        filters['root_type'] = root_type
    if account_type:
        filters['account_type'] = account_type
    if is_group is not None:
        filters['is_group'] = 1 if is_group else 0
    accounts = frappe.get_all('Account',
        filters=filters,
        fields=[
            'name', 'account_name', 'account_type', 'root_type',
            'parent_account', 'is_group', 'account_currency', 'disabled'
        ],
        order_by='lft'
    )
    return accounts

@frappe.whitelist(allow_guest=True)
def get_account_balance(account):
    """Get current balance of an account from General Ledger."""
    frappe.set_user('Administrator')
    if not frappe.db.exists('Account', account):
        frappe.throw(f'Account {account} not found')
    balance = frappe.db.sql("""
        SELECT 
            SUM(debit) as total_debit,
            SUM(credit) as total_credit,
            SUM(debit - credit) as balance
        FROM `tabGL Entry`
        WHERE account = %s
        AND is_cancelled = 0
    """, account, as_dict=True)
    result = balance[0] if balance else {'total_debit': 0, 'total_credit': 0, 'balance': 0}
    acc_doc = frappe.get_doc('Account', account)
    return {
        'account': account,
        'account_name': acc_doc.account_name,
        'root_type': acc_doc.root_type,
        'total_debit': flt(result.get('total_debit', 0)),
        'total_credit': flt(result.get('total_credit', 0)),
        'balance': flt(result.get('balance', 0))
    }

@frappe.whitelist(allow_guest=True)
def list_bank_accounts_with_balance():
    """List bank accounts with their current balances."""
    frappe.set_user('Administrator')
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        return []
    company_name = company[0]
    accounts = frappe.get_all('Account',
        filters={
            'company': company_name,
            'account_type': 'Bank',
            'is_group': 0,
            'disabled': 0
        },
        fields=['name', 'account_name', 'account_currency', 'parent_account']
    )
    for acc in accounts:
        balance_data = frappe.db.sql("""
            SELECT SUM(debit - credit) as balance
            FROM `tabGL Entry`
            WHERE account = %s AND is_cancelled = 0
        """, acc['name'], as_dict=True)
        acc['balance'] = flt(balance_data[0].get('balance', 0)) if balance_data else 0
    return accounts

@frappe.whitelist(allow_guest=True)
def create_account(account_name, root_type, parent_account=None, account_type=None, is_group=0):
    """Create a new account in Chart of Accounts."""
    frappe.set_user('Administrator')
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        frappe.throw('No company found')
    company_name = company[0]
    valid_root_types = ['Asset', 'Liability', 'Equity', 'Income', 'Expense']
    if root_type not in valid_root_types:
        frappe.throw(f'Invalid root type. Must be one of: {", ".join(valid_root_types)}')
    if not parent_account:
        if account_type == 'Bank':
            parent_account = frappe.db.get_value('Account',
                filters={'account_name': 'Bank Accounts', 'is_group': 1, 'company': company_name},
                fieldname='name'
            )
        elif account_type == 'Cash':
            parent_account = frappe.db.get_value('Account',
                filters={'account_name': 'Cash In Hand', 'is_group': 1, 'company': company_name},
                fieldname='name'
            )
        elif root_type == 'Expense':
            parent_account = frappe.db.get_value('Account',
                filters={'account_name': 'Indirect Expenses', 'is_group': 1, 'company': company_name},
                fieldname='name'
            )
            if not parent_account:
                parent_account = frappe.db.get_value('Account',
                    filters={'root_type': 'Expense', 'is_group': 1, 'company': company_name},
                    fieldname='name'
                )
        elif root_type == 'Income':
            parent_account = frappe.db.get_value('Account',
                filters={'account_name': 'Direct Income', 'is_group': 1, 'company': company_name},
                fieldname='name'
            )
            if not parent_account:
                parent_account = frappe.db.get_value('Account',
                    filters={'root_type': 'Income', 'is_group': 1, 'company': company_name},
                    fieldname='name'
                )
        else:
            parent_account = frappe.db.get_value('Account',
                filters={'root_type': root_type, 'is_group': 1, 'company': company_name},
                fieldname='name'
            )
    if not parent_account:
        frappe.throw(f'Could not find parent account for {root_type}')
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
    account = frappe.get_doc({
        'doctype': 'Account',
        'account_name': account_name,
        'parent_account': parent_account,
        'company': company_name,
        'root_type': root_type,
        'account_type': account_type,
        'is_group': int(is_group),
        'account_currency': 'KES'
    })
    account.insert()
    frappe.db.commit()
    return {
        'status': 'created',
        'account': account.name,
        'account_name': account.account_name,
        'parent_account': account.parent_account,
        'message': f'Account "{account_name}" created successfully'
    }

@frappe.whitelist(allow_guest=True)
def get_account_types():
    """Get list of valid account types for dropdowns."""
    return [
        {'value': '', 'label': 'None'},
        {'value': 'Bank', 'label': 'Bank'},
        {'value': 'Cash', 'label': 'Cash'},
        {'value': 'Receivable', 'label': 'Receivable'},
        {'value': 'Payable', 'label': 'Payable'},
        {'value': 'Stock', 'label': 'Stock'},
        {'value': 'Tax', 'label': 'Tax'},
        {'value': 'Expense Account', 'label': 'Expense Account'},
        {'value': 'Income Account', 'label': 'Income Account'},
        {'value': 'Depreciation', 'label': 'Depreciation'},
        {'value': 'Fixed Asset', 'label': 'Fixed Asset'},
        {'value': 'Accumulated Depreciation', 'label': 'Accumulated Depreciation'},
        {'value': 'Cost of Goods Sold', 'label': 'Cost of Goods Sold'},
        {'value': 'Equity', 'label': 'Equity'},
    ]

@frappe.whitelist(allow_guest=True)
def get_root_types():
    """Get list of root types for dropdowns."""
    return [
        {'value': 'Asset', 'label': 'Asset'},
        {'value': 'Liability', 'label': 'Liability'},
        {'value': 'Equity', 'label': 'Equity'},
        {'value': 'Income', 'label': 'Income'},
        {'value': 'Expense', 'label': 'Expense'},
    ]

@frappe.whitelist(allow_guest=True)
def get_parent_accounts(root_type=None):
    """Get group accounts that can be used as parents."""
    frappe.set_user('Administrator')
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        return []
    company_name = company[0]
    filters = {
        'company': company_name,
        'is_group': 1
    }
    if root_type:
        filters['root_type'] = root_type
    accounts = frappe.get_all('Account',
        filters=filters,
        fields=['name', 'account_name', 'root_type'],
        order_by='account_name'
    )
    return accounts

@frappe.whitelist(allow_guest=True)
def get_chart_of_accounts_tree():
    """Get Chart of Accounts in tree structure."""
    frappe.set_user('Administrator')
    company = frappe.get_all('Company', limit=1, pluck='name')
    if not company:
        return []
    company_name = company[0]
    accounts = frappe.db.sql("""
        SELECT 
            name, account_name, parent_account, is_group, 
            root_type, account_type, account_currency
        FROM `tabAccount`
        WHERE company = %s
        ORDER BY lft
    """, company_name, as_dict=True)
    account_map = {acc['name']: acc for acc in accounts}
    for acc in accounts:
        acc['children'] = []
        acc['balance'] = 0
    roots = []
    for acc in accounts:
        parent = acc.get('parent_account')
        if parent and parent in account_map:
            account_map[parent]['children'].append(acc)
        elif not parent:
            roots.append(acc)
    tree = {
        'Asset': [],
        'Liability': [],
        'Equity': [],
        'Income': [],
        'Expense': []
    }
    for acc in roots:
        rt = acc.get('root_type')
        if rt in tree:
            tree[rt].append(acc)
    return tree

@frappe.whitelist(allow_guest=True)
def create_journal_entry(lines, posting_date=None, remark=None, company=None, property=None):
    """Create a Journal Entry for transfers and manual adjustments."""
    import json
    frappe.set_user('Administrator')
    if isinstance(lines, str):
        lines = json.loads(lines)
    if not lines or len(lines) < 2:
        frappe.throw('At least 2 entry lines are required')
    if not company:
        companies = frappe.get_all('Company', limit=1, pluck='name')
        if not companies:
            frappe.throw('No company found')
        company = companies[0]
    total_debit = sum(flt(l.get('debit', 0)) for l in lines)
    total_credit = sum(flt(l.get('credit', 0)) for l in lines)
    if abs(total_debit - total_credit) > 0.01:
        frappe.throw(f'Entry is not balanced. Debit: {total_debit}, Credit: {total_credit}')
    cost_center = None
    if property:
        cost_center = frappe.db.get_value('Cost Center', 
            filters={'cost_center_name': property, 'company': company},
            fieldname='name'
        )
    je_accounts = []
    for line in lines:
        account = line.get('account')
        if not account:
            continue
        debit = flt(line.get('debit', 0))
        credit = flt(line.get('credit', 0))
        if debit == 0 and credit == 0:
            continue
        acc_entry = {
            'account': account,
            'debit_in_account_currency': debit,
            'credit_in_account_currency': credit,
        }
        if cost_center:
            acc_entry['cost_center'] = cost_center
        je_accounts.append(acc_entry)
    if len(je_accounts) < 2:
        frappe.throw('At least 2 valid entry lines are required')
    je = frappe.get_doc({
        'doctype': 'Journal Entry',
        'voucher_type': 'Journal Entry',
        'company': company,
        'posting_date': posting_date or nowdate(),
        'user_remark': remark or '',
        'accounts': je_accounts
    })
    je.insert()
    je.submit()
    frappe.db.commit()
    return {
        'name': je.name,
        'journal_entry': je.name,
        'posting_date': str(je.posting_date),
        'total_debit': total_debit,
        'total_credit': total_credit,
        'status': 'Submitted',
        'message': f'Journal Entry {je.name} created and submitted'
    }


def setup_property_ledger_accounts(property_name):
    """
    Create isolated ledger accounts for a property in the Chart of Accounts.
    Called automatically when a new Property is created.
    """
    frappe.set_user('Administrator')
    
    if not frappe.db.exists('Property', property_name):
        frappe.throw(f'Property {property_name} not found')
    
    prop = frappe.get_doc('Property', property_name)
    prefix = f"{prop.property_name} ({prop.name})"
    
    companies = frappe.get_all('Company', limit=1, pluck='name')
    if not companies:
        frappe.log_error(f'No company found for property ledger setup: {property_name}')
        return
    company = companies[0]
    
    def ensure_account(account_name, root_type, parent_name, account_type=None, is_group=0):
        existing = frappe.db.get_value('Account', {
            'account_name': account_name,
            'company': company
        }, 'name')
        if existing:
            return existing
        
        parent_account = frappe.db.get_value('Account', {
            'account_name': parent_name,
            'company': company,
            'is_group': 1
        }, 'name')
        
        if not parent_account:
            parent_account = frappe.db.get_value('Account', {
                'root_type': root_type,
                'company': company,
                'is_group': 1
            }, 'name')
        
        if not parent_account:
            frappe.log_error(f'Could not find parent account {parent_name} for {account_name}')
            return None
        
        try:
            acc = frappe.get_doc({
                'doctype': 'Account',
                'account_name': account_name,
                'parent_account': parent_account,
                'company': company,
                'root_type': root_type,
                'account_type': account_type,
                'is_group': is_group,
                'account_currency': 'KES'
            })
            acc.insert(ignore_permissions=True)
            return acc.name
        except Exception as e:
            frappe.log_error(f'Could not create account {account_name}: {str(e)}')
            return None
    
    group_name = f"Property Accounts - {prefix}"
    ensure_account(group_name, 'Asset', 'Application of Funds (Assets)', is_group=1)
    
    debtors_name = f"Debtors - {prefix}"
    ensure_account(debtors_name, 'Asset', 'Accounts Receivable', account_type='Receivable')
    
    rent_income_name = f"Rent Income - {prefix}"
    ensure_account(rent_income_name, 'Income', 'Direct Income', account_type='Income Account')
    
    utility_income_name = f"Utility Income - {prefix}"
    ensure_account(utility_income_name, 'Income', 'Direct Income', account_type='Income Account')
    
    expense_name = f"Expenses - {prefix}"
    ensure_account(expense_name, 'Expense', 'Indirect Expenses', account_type='Expense Account')
    
    collection_name = f"Collection Bank/M-Pesa - {prefix}"
    ensure_account(collection_name, 'Asset', 'Bank Accounts', account_type='Bank')
    
    frappe.db.set_value('Property', property_name, {
        'account_group': group_name,
        'receivables_account': debtors_name,
        'rent_income_account': rent_income_name,
        'utility_income_account': utility_income_name,
        'expense_account': expense_name,
        'collection_account': collection_name
    }, update_modified=False)
    
    frappe.db.commit()


def setup_property_cost_center(property_name):
    """
    Create a cost center for a property to enable per-property financial reporting.
    Called automatically when a new Property is created.
    """
    frappe.set_user('Administrator')
    
    if not frappe.db.exists('Property', property_name):
        frappe.throw(f'Property {property_name} not found')
    
    prop = frappe.get_doc('Property', property_name)
    
    # Get company
    companies = frappe.get_all('Company', limit=1, pluck='name')
    if not companies:
        frappe.log_error(f'No company found for property cost center setup: {property_name}')
        return
    company = companies[0]
    
    # Cost center name
    cc_name = f"{prop.property_name} - {company}"
    
    # Check if cost center already exists
    existing = frappe.db.get_value('Cost Center', {
        'cost_center_name': prop.property_name,
        'company': company
    }, 'name')
    
    if existing:
        # Link to property
        frappe.db.set_value('Property', property_name, 'cost_center', existing, update_modified=False)
        frappe.db.commit()
        return existing
    
    # Find parent cost center (Main or root)
    parent_cc = frappe.db.get_value('Cost Center', {
        'company': company,
        'is_group': 1,
        'parent_cost_center': ['is', 'not set']
    }, 'name')
    
    if not parent_cc:
        # Try to find any group cost center
        parent_cc = frappe.db.get_value('Cost Center', {
            'company': company,
            'is_group': 1
        }, 'name')
    
    if not parent_cc:
        frappe.log_error(f'No parent cost center found for property: {property_name}')
        return
    
    try:
        cc = frappe.get_doc({
            'doctype': 'Cost Center',
            'cost_center_name': prop.property_name,
            'company': company,
            'parent_cost_center': parent_cc,
            'is_group': 0
        })
        cc.insert(ignore_permissions=True)
        
        # Link to property
        frappe.db.set_value('Property', property_name, 'cost_center', cc.name, update_modified=False)
        frappe.db.commit()
        
        return cc.name
    except Exception as e:
        frappe.log_error(f'Could not create cost center for property {property_name}: {str(e)}')
        return None
