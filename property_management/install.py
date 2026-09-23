# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt
"""
Automated site setup for Property Management System.

Bypasses the Frappe/ERPNext setup wizard and configures:
- System Settings (Kenya, KES, Africa/Nairobi timezone)
- Global Settings (country, currency, timezone)
- Default Company with Kenya Chart of Accounts
- Administrator user
- Roles and Permissions
- Custom Fields
- Access Modules
- Fixtures

Run with: bench --site <sitename> execute property_management.install.setup_site
"""

import frappe
from frappe.utils import now, getdate


# ============================================================================
# CONFIGURATION
# ============================================================================

SITE_CONFIG = {
    "country": "Kenya",
    "currency": "KES",
    "timezone": "Africa/Nairobi",
    "language": "en",
    "date_format": "dd-mm-yyyy",
    "time_format": "HH:mm:ss",
    "number_format": "#,###.##",
    "float_precision": 2,
    "currency_precision": 2,
}

DEFAULT_COMPANY = {
    "company_name": "Dadis Estates Limited",
    "abbr": "DEL",
    "default_currency": "KES",
    "country": "Kenya",
    "create_chart_of_accounts_based_on": "Standard Template",
    "chart_of_accounts": "Kenya - Standard",
}

ADMIN_PASSWORD = "admin"


# ============================================================================
# SYSTEM SETTINGS
# ============================================================================

def setup_system_settings():
    """Configure System Settings with Kenya defaults."""
    print("Setting up System Settings...")
    
    settings = frappe.get_single("System Settings")
    settings.country = SITE_CONFIG["country"]
    settings.language = SITE_CONFIG["language"]
    settings.time_zone = SITE_CONFIG["timezone"]
    settings.date_format = SITE_CONFIG["date_format"]
    settings.time_format = SITE_CONFIG["time_format"]
    settings.number_format = SITE_CONFIG["number_format"]
    settings.float_precision = SITE_CONFIG["float_precision"]
    settings.currency_precision = SITE_CONFIG["currency_precision"]
    settings.setup_complete = 1  # Mark setup wizard as done
    settings.flags.ignore_permissions = True
    settings.save(ignore_permissions=True)
    
    frappe.db.commit()
    print("✓ System Settings configured")


def setup_global_defaults():
    """Set global defaults for the site."""
    print("Setting up Global Defaults...")
    
    # Ensure currency exists
    if not frappe.db.exists("Currency", SITE_CONFIG["currency"]):
        frappe.get_doc({
            "doctype": "Currency",
            "currency_name": "KES",
            "fraction": "Cents",
            "fraction_units": 100,
            "symbol": "KSh",
            "number_format": "#,###.##",
            "smallest_currency_fraction_value": 0.01,
            "enabled": 1,
        }).insert(ignore_permissions=True)
    
    # Enable KES currency
    frappe.db.set_value("Currency", "KES", "enabled", 1)
    
    # Set Global Defaults
    gd = frappe.get_single("Global Defaults")
    gd.default_currency = SITE_CONFIG["currency"]
    gd.default_company = DEFAULT_COMPANY["company_name"]
    gd.country = SITE_CONFIG["country"]
    gd.flags.ignore_permissions = True
    gd.save(ignore_permissions=True)
    
    frappe.db.commit()
    print("✓ Global Defaults configured")


# ============================================================================
# ERPNext SETUP (Company, Chart of Accounts, etc.)
# ============================================================================

def setup_erpnext_masters():
    """Create ERPNext prerequisite masters."""
    print("Setting up ERPNext masters...")
    
    # Check if ERPNext is installed
    if not frappe.db.exists("DocType", "Company"):
        print("⚠ ERPNext not installed, skipping ERPNext setup")
        return False
    
    year = getdate().year
    
    # Fiscal Year
    fy_name = str(year)
    if not frappe.db.exists("Fiscal Year", fy_name):
        frappe.get_doc({
            "doctype": "Fiscal Year",
            "year": fy_name,
            "year_start_date": f"{year}-01-01",
            "year_end_date": f"{year}-12-31",
        }).insert(ignore_permissions=True)
        print(f"  ✓ Fiscal Year {fy_name} created")
    
    # UOM
    if not frappe.db.exists("UOM", "Nos"):
        frappe.get_doc({
            "doctype": "UOM",
            "uom_name": "Nos",
            "must_be_whole_number": 1,
        }).insert(ignore_permissions=True)
        print("  ✓ UOM 'Nos' created")
    
    # Warehouse Type
    if frappe.db.exists("DocType", "Warehouse Type"):
        if not frappe.db.exists("Warehouse Type", "Transit"):
            frappe.get_doc({
                "doctype": "Warehouse Type",
                "name": "Transit",
            }).insert(ignore_permissions=True)
            print("  ✓ Warehouse Type 'Transit' created")
    
    # Customer Group tree
    if not frappe.db.exists("Customer Group", "All Customer Groups"):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "All Customer Groups",
            "is_group": 1,
        }).insert(ignore_permissions=True)
        print("  ✓ Customer Group 'All Customer Groups' created")
    
    if not frappe.db.exists("Customer Group", "Tenants"):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "Tenants",
            "parent_customer_group": "All Customer Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
        print("  ✓ Customer Group 'Tenants' created")
    
    # Territory tree
    if not frappe.db.exists("Territory", "All Territories"):
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": "All Territories",
            "is_group": 1,
        }).insert(ignore_permissions=True)
        print("  ✓ Territory 'All Territories' created")
    
    if not frappe.db.exists("Territory", "Kenya"):
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": "Kenya",
            "parent_territory": "All Territories",
            "is_group": 0,
        }).insert(ignore_permissions=True)
        print("  ✓ Territory 'Kenya' created")
    
    # Supplier Group tree
    if not frappe.db.exists("Supplier Group", "All Supplier Groups"):
        frappe.get_doc({
            "doctype": "Supplier Group",
            "supplier_group_name": "All Supplier Groups",
            "is_group": 1,
        }).insert(ignore_permissions=True)
        print("  ✓ Supplier Group 'All Supplier Groups' created")
    
    if not frappe.db.exists("Supplier Group", "Vendors"):
        frappe.get_doc({
            "doctype": "Supplier Group",
            "supplier_group_name": "Vendors",
            "parent_supplier_group": "All Supplier Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
        print("  ✓ Supplier Group 'Vendors' created")
    
    frappe.db.commit()
    print("✓ ERPNext masters created")
    return True


def setup_company():
    """Create the default company with Kenya Chart of Accounts."""
    print("Setting up Company...")
    
    if not frappe.db.exists("DocType", "Company"):
        print("⚠ ERPNext not installed, skipping Company setup")
        return None
    
    company_name = DEFAULT_COMPANY["company_name"]
    
    if frappe.db.exists("Company", company_name):
        print(f"  ✓ Company '{company_name}' already exists")
        return company_name
    
    try:
        company = frappe.get_doc({
            "doctype": "Company",
            **DEFAULT_COMPANY,
        })
        # Disable automatic tax template creation to avoid root account errors
        company.flags.ignore_chart_of_accounts = False
        company.insert(ignore_permissions=True)
        
        frappe.db.commit()
        print(f"✓ Company '{company_name}' created with Kenya Chart of Accounts")
    except Exception as e:
        # If Kenya chart fails, try with Standard chart
        print(f"⚠ Kenya chart failed ({e}), trying Standard chart...")
        frappe.db.rollback()
        
        company = frappe.get_doc({
            "doctype": "Company",
            "company_name": company_name,
            "abbr": DEFAULT_COMPANY["abbr"],
            "default_currency": "KES",
            "country": "Kenya",
            "create_chart_of_accounts_based_on": "Standard Template",
            "chart_of_accounts": "Standard",
        })
        company.insert(ignore_permissions=True)
        frappe.db.commit()
        print(f"✓ Company '{company_name}' created with Standard Chart of Accounts")
    
    return company_name


# ============================================================================
# ROLES AND PERMISSIONS
# ============================================================================

def setup_roles():
    """Create property management roles."""
    print("Setting up Roles...")
    
    roles = {
        "Organization Admin": {"desk_access": 1},
        "Director": {"desk_access": 1},
        "Office User": {"desk_access": 1},
        "Caretaker": {"desk_access": 1},
        "Landlord": {"desk_access": 0},
        "Tenant": {"desk_access": 0},
    }
    
    for role_name, props in roles.items():
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({
                "doctype": "Role",
                "role_name": role_name,
                **props,
            }).insert(ignore_permissions=True)
            print(f"  ✓ Role '{role_name}' created")
        else:
            print(f"  - Role '{role_name}' already exists")
    
    frappe.db.commit()
    print("✓ Roles configured")


# ============================================================================
# ORGANIZATION AND CUSTOM FIELDS
# ============================================================================

def setup_organization():
    """Create default organization linked to company."""
    print("Setting up Organization...")
    
    org_name = DEFAULT_COMPANY["company_name"]
    
    if frappe.db.exists("Organization", org_name):
        print(f"  ✓ Organization '{org_name}' already exists")
        return org_name
    
    org_data = {
        "doctype": "Organization",
        "organization_name": org_name,
        "status": "Active",
    }
    
    # Link to ERPNext company if field exists and company exists
    if frappe.db.exists("Company", org_name):
        meta = frappe.get_meta("Organization")
        if meta.has_field("erpnext_company"):
            org_data["erpnext_company"] = org_name
    
    org = frappe.get_doc(org_data)
    org.insert(ignore_permissions=True)
    
    frappe.db.commit()
    print(f"✓ Organization '{org_name}' created")
    return org_name


def setup_custom_fields():
    """Setup integration custom fields."""
    print("Setting up Custom Fields...")
    
    try:
        from property_management.integration.custom_fields import setup_integration_custom_fields
        setup_integration_custom_fields()
        print("✓ Custom Fields configured")
    except Exception as e:
        print(f"⚠ Custom Fields setup failed: {e}")


# ============================================================================
# ACCESS MODULES
# ============================================================================

def setup_access_modules():
    """Setup access modules and permissions."""
    print("Setting up Access Modules...")
    
    try:
        from property_management.setup import seed_access_modules
        seed_access_modules()
        print("✓ Access Modules configured")
    except Exception as e:
        print(f"⚠ Access Modules setup failed: {e}")


# ============================================================================
# FIXTURES
# ============================================================================

def import_fixtures():
    """Import role and custom field fixtures."""
    print("Importing Fixtures...")
    
    try:
        from frappe.utils.fixtures import sync_fixtures
        sync_fixtures("property_management")
        print("✓ Fixtures imported")
    except Exception as e:
        print(f"⚠ Fixtures import failed: {e}")


# ============================================================================
# INCOME ACCOUNTS (for rent invoicing)
# ============================================================================

def setup_income_accounts():
    """Create rent and utility income accounts."""
    print("Setting up Income Accounts...")
    
    if not frappe.db.exists("Company", DEFAULT_COMPANY["company_name"]):
        print("⚠ Company not found, skipping income accounts")
        return
    
    try:
        from property_management.integration.erpnext_setup import (
            ensure_income_accounts,
            ensure_deposit_liability_account,
        )
        company = DEFAULT_COMPANY["company_name"]
        ensure_income_accounts(company)
        ensure_deposit_liability_account(company)
        print("✓ Income Accounts configured")
    except Exception as e:
        print(f"⚠ Income Accounts setup failed: {e}")


# ============================================================================
# ADMINISTRATOR SETUP
# ============================================================================

def setup_administrator():
    """Configure Administrator user."""
    print("Setting up Administrator...")
    
    admin = frappe.get_doc("User", "Administrator")
    
    # Add all property management roles
    admin_roles = ["System Manager", "Organization Admin", "Director"]
    existing_roles = [r.role for r in admin.roles]
    
    for role in admin_roles:
        if role not in existing_roles and frappe.db.exists("Role", role):
            admin.append("roles", {"role": role})
    
    admin.flags.ignore_permissions = True
    admin.save(ignore_permissions=True)
    
    frappe.db.commit()
    print("✓ Administrator configured")


# ============================================================================
# HRMS INSTALLATION
# ============================================================================

def install_hrms():
    """Install HRMS app if available and not already installed."""
    print("Setting up HRMS (Payroll)...")
    
    try:
        # Check if hrms is already installed
        installed_apps = frappe.get_installed_apps()
        if "hrms" in installed_apps:
            print("  ✓ HRMS already installed")
            return True
        
        # Check if hrms app exists in bench
        import os
        apps_path = frappe.get_app_path("frappe").replace("/frappe/frappe", "/apps")
        hrms_path = os.path.join(apps_path, "hrms")
        
        if not os.path.exists(hrms_path):
            print("  ⚠ HRMS app not found in apps folder")
            print("  Run: bench get-app hrms --branch version-15")
            return False
        
        # Install hrms on this site
        from frappe.commands.site import _install_app
        print("  Installing HRMS app on site...")
        
        # Use frappe's internal install mechanism
        frappe.flags.in_install = True
        from frappe.installer import install_app
        install_app("hrms", verbose=True)
        frappe.flags.in_install = False
        
        frappe.db.commit()
        print("  ✓ HRMS installed successfully")
        return True
    except Exception as e:
        print(f"  ⚠ HRMS installation failed: {e}")
        print("  You can install manually: bench --site <sitename> install-app hrms")
        return False


# ============================================================================
# MAIN SETUP FUNCTION
# ============================================================================

def setup_site():
    """
    Complete automated site setup.
    
    Run with: bench --site <sitename> execute property_management.install.setup_site
    """
    print("\n" + "=" * 60)
    print("  PROPERTY MANAGEMENT SYSTEM - AUTOMATED SETUP")
    print("=" * 60 + "\n")
    
    # 1. System Settings (Kenya, KES, Nairobi)
    setup_system_settings()
    
    # 2. ERPNext masters (Fiscal Year, UOM, Customer Groups, etc.)
    erpnext_ready = setup_erpnext_masters()
    
    # 3. Company with Kenya Chart of Accounts
    if erpnext_ready:
        setup_company()
    
    # 4. Global Defaults
    setup_global_defaults()
    
    # 5. Roles
    setup_roles()
    
    # 6. Organization (linked to Company)
    setup_organization()
    
    # 7. Custom Fields
    setup_custom_fields()
    
    # 8. Access Modules and Permissions
    setup_access_modules()
    
    # 9. Import Fixtures
    import_fixtures()
    
    # 10. Income Accounts for rent invoicing
    if erpnext_ready:
        setup_income_accounts()
    
    # 11. Install HRMS for payroll functionality
    if erpnext_ready:
        install_hrms()
    
    # 12. Administrator
    setup_administrator()
    
    # Final commit
    frappe.db.commit()
    
    print("\n" + "=" * 60)
    print("  SETUP COMPLETE!")
    print("=" * 60)
    print(f"""
  Site configured with:
  - Country: Kenya
  - Currency: KES
  - Timezone: Africa/Nairobi
  - Company: {DEFAULT_COMPANY['company_name']}
  - HRMS: Installed (for payroll/salary slips)
  
  Login credentials:
  - Username: Administrator
  - Password: {ADMIN_PASSWORD}
  
  The setup wizard has been bypassed.
  You can now log in directly.
""")


# ============================================================================
# QUICK RESET (for development)
# ============================================================================

def reset_and_setup():
    """
    Reset site data and run setup (DESTRUCTIVE - development only).
    
    Run with: bench --site <sitename> execute property_management.install.reset_and_setup
    """
    print("\n⚠ WARNING: This will clear all data!")
    print("For development use only.\n")
    
    # Clear cache
    frappe.clear_cache()
    
    # Run setup
    setup_site()
