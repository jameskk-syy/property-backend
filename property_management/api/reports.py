import frappe
from frappe.utils import flt, nowdate, getdate, formatdate, add_months
from frappe.utils.pdf import get_pdf
from collections import OrderedDict


def _get_property_cost_center(property_name):
    """Get cost_center for a property, used to filter GL entries."""
    if not property_name:
        return None
    return frappe.db.get_value("Property", property_name, "cost_center")


@frappe.whitelist()
def rent_collection_report(property=None, from_date=None, to_date=None):
    """
    Rent collection from native ERPNext. Invoiced/outstanding come from submitted
    Sales Invoices; collected comes from the GL income accounts (real money received,
    including direct M-Pesa rent postings before monthly invoicing exists).
    """
    cost_center = _get_property_cost_center(property)
    
    si_filters = {"docstatus": 1}
    if from_date and to_date:
        si_filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    if cost_center:
        si_filters["cost_center"] = cost_center

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=si_filters,
        fields=[
            "name", "customer", "grand_total as total_amount",
            "outstanding_amount", "status", "due_date", "posting_date",
        ],
        order_by="posting_date desc",
    )
    for inv in invoices:
        inv["paid_amount"] = flt(inv.get("total_amount")) - flt(inv.get("outstanding_amount"))
        inv["tenant"] = inv.get("customer")

    total_invoiced = sum(flt(i.get("total_amount")) for i in invoices)
    total_outstanding = sum(flt(i.get("outstanding_amount")) for i in invoices)

    # Collected = real rent income recognised in the GL for the period.
    total_collected = _gl_income(from_date, to_date, cost_center=cost_center) if (from_date and to_date) else _gl_income(cost_center=cost_center)

    return {
        "total_invoiced": total_invoiced,
        "total_collected": total_collected,
        "total_outstanding": total_outstanding,
        "invoice_count": len(invoices),
        "invoices": invoices,
    }


@frappe.whitelist()
def rent_arrears_report(property=None, from_date=None, to_date=None):
    """
    Overdue receivables from submitted Sales Invoices (native ERPNext). Empty until
    monthly rent invoices are generated, which is correct. Includes simple day-bucket
    aging so the frontend can filter by age.
    """
    cost_center = _get_property_cost_center(property)
    today = getdate(nowdate())
    
    si_filters = {"docstatus": 1, "outstanding_amount": [">", 0]}
    if cost_center:
        si_filters["cost_center"] = cost_center
        
    overdue_invoices = frappe.get_all(
        "Sales Invoice",
        filters=si_filters,
        fields=[
            "name", "customer as tenant", "due_date", "posting_date",
            "grand_total as total_amount", "outstanding_amount", "status",
        ],
        order_by="outstanding_amount desc",
    )

    buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for inv in overdue_invoices:
        inv["paid_amount"] = flt(inv.get("total_amount")) - flt(inv.get("outstanding_amount"))
        due = getdate(inv["due_date"]) if inv.get("due_date") else today
        age = (today - due).days
        inv["days_overdue"] = age if age > 0 else 0
        amt = flt(inv["outstanding_amount"])
        if age <= 30:
            buckets["0-30"] += amt
        elif age <= 60:
            buckets["31-60"] += amt
        elif age <= 90:
            buckets["61-90"] += amt
        else:
            buckets["90+"] += amt

    total_arrears = sum(flt(i["outstanding_amount"]) for i in overdue_invoices)
    return {
        "total_arrears": total_arrears,
        "overdue_count": len(overdue_invoices),
        "aging_buckets": buckets,
        "arrears": overdue_invoices,
    }


@frappe.whitelist()
def revenue_and_expense_trend(property=None, months=6):
    """Computes monthly collections and expenses from real database records."""
    from frappe.utils import get_first_day, get_last_day
    cost_center = _get_property_cost_center(property)
    trend = []
    curr = getdate(nowdate())
    for i in range(int(months) - 1, -1, -1):
        m_ref = add_months(curr, -i)
        m_start = get_first_day(m_ref)
        m_end = get_last_day(m_ref)
        trend.append({
            "month": formatdate(m_ref, "MMM"),
            "full_month": formatdate(m_ref, "MMM YYYY"),
            "revenue": _gl_income(m_start, m_end, cost_center=cost_center),
            "expenses": _gl_expense(m_start, m_end, cost_center=cost_center),
        })
    return trend


@frappe.whitelist()
def vacancy_occupancy_report(property=None):
    """Returns unit status breakdown (Vacant, Occupied, Under Maintenance)."""
    filters = {}
    if property:
        filters["property"] = property

    units = frappe.get_all("Property Unit", filters=filters, fields=["name", "unit_number", "property", "status", "base_rent", "unit_type", "floor"])

    occupied = [u for u in units if u.status == "Occupied"]
    vacant = [u for u in units if u.status == "Vacant"]
    maintenance = [u for u in units if u.status == "Maintenance"]

    total_units = len(units)
    occupancy_rate = (len(occupied) / total_units * 100.0) if total_units > 0 else 0.0

    return {
        "total_units": total_units,
        "occupied_count": len(occupied),
        "vacant_count": len(vacant),
        "maintenance_count": len(maintenance),
        "occupancy_rate_percentage": round(occupancy_rate, 2),
        "units": units
    }


@frappe.whitelist()
def expense_report(property=None, from_date=None, to_date=None):
    """
    Expenses from the native GL (Expense accounts), broken down by account. This
    reflects real posted spend (approved & posted expenses, purchase invoices, etc.),
    not the legacy Property Expense table.
    """
    cost_center = _get_property_cost_center(property)
    breakdown = _gl_account_breakdown("Expense", from_date, to_date, cost_center=cost_center)
    by_category = {row["account_name"]: row["balance"] for row in breakdown}
    return {
        "total_expense": sum(flt(r["balance"]) for r in breakdown),
        "by_category": by_category,
        "expense_records": breakdown,
    }


@frappe.whitelist()
def payroll_salary_report(property=None, period=None):
    """Aggregates payroll gross salaries, statutory deductions, and net payouts."""
    filters = {}
    if property:
        filters["property"] = property
    if period:
        filters["payroll_period"] = period

    runs = frappe.get_all("Payroll Run", filters=filters, fields=["name", "payroll_period", "total_gross", "total_deductions", "total_net", "status"])
    return {
        "total_runs": len(runs),
        "total_gross": sum(flt(r.total_gross) for r in runs),
        "total_deductions": sum(flt(r.total_deductions) for r in runs),
        "total_net": sum(flt(r.total_net) for r in runs),
        "payroll_runs": runs
    }


@frappe.whitelist()
def landlord_remittance_report(property=None, landlord=None, from_date=None, to_date=None):
    """
    Calculates live landlord remittance summary:
    Gross Collections - Management Commission % - Property Expenses = Net Remittance.
    """
    prop_filters = {}
    if property:
        prop_filters["name"] = property
    if landlord:
        prop_filters["landlord"] = landlord

    properties = frappe.get_all("Property", filters=prop_filters, fields=["name", "property_name", "landlord"])
    prop_names = [p.name for p in properties]
    remittances = []

    # Collections/expenses per property from the GL cost center (falls back to
    # portfolio totals on the single/first property when cost centers aren't split).
    collected_by_prop = {}
    expenses_by_prop = {}
    if prop_names:
        # GL income/expense keyed by cost center matching the property.
        for prop in properties:
            cc = frappe.db.get_value("Property", prop.name, "cost_center")
            if cc:
                inc_filters = {"account": ["in", _income_accounts()], "cost_center": cc, "is_cancelled": 0}
                exp_filters = {"account": ["in", _expense_accounts()], "cost_center": cc, "is_cancelled": 0}
                if from_date and to_date:
                    inc_filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
                    exp_filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
                inc = frappe.get_all("GL Entry", filters=inc_filters, fields=["sum(credit) as c", "sum(debit) as d"])
                exp = frappe.get_all("GL Entry", filters=exp_filters, fields=["sum(debit) as d", "sum(credit) as c"])
                collected_by_prop[prop.name] = (flt(inc[0].c) - flt(inc[0].d)) if inc else 0.0
                expenses_by_prop[prop.name] = (flt(exp[0].d) - flt(exp[0].c)) if exp else 0.0

        # Fallback: no cost centers set → attribute portfolio totals to first property.
        if not collected_by_prop and properties:
            collected_by_prop[properties[0].name] = _gl_income(from_date, to_date)
            expenses_by_prop[properties[0].name] = _gl_expense(from_date, to_date)

    for prop in properties:
        gross_collected = collected_by_prop.get(prop.name, 0.0)
        total_expenses = expenses_by_prop.get(prop.name, 0.0)

        comm_rate = 10.0
        mgmt_fee = gross_collected * (comm_rate / 100.0)
        net_remittance = max(0.0, gross_collected - mgmt_fee - total_expenses)

        remittances.append({
            "property": prop.name,
            "property_name": prop.property_name,
            "landlord": prop.landlord,
            "gross_collected": gross_collected,
            "commission_rate": comm_rate,
            "management_fee": mgmt_fee,
            "property_expenses": total_expenses,
            "net_remittance": net_remittance,
            "status": "Pending Payout" if net_remittance > 0 else "Settled"
        })

    return {
        "total_gross_collected": sum(r["gross_collected"] for r in remittances),
        "total_management_fees": sum(r["management_fee"] for r in remittances),
        "total_property_expenses": sum(r["property_expenses"] for r in remittances),
        "total_net_remittance": sum(r["net_remittance"] for r in remittances),
        "remittances": remittances
    }


@frappe.whitelist()
def construction_budget_vs_actual(project_id):
    """Returns budget lines vs actual spend for a construction project."""
    project = frappe.get_doc("Construction Project", project_id)
    return {
        "project_name": project.project_name,
        "status": project.status,
        "total_budget": project.total_budget,
        "total_actual_spend": project.total_actual_spend,
        "budget_variance": project.budget_variance,
        "budget_lines": project.get("budget_lines", [])
    }


@frappe.whitelist()
def profit_and_loss(property=None, from_date=None, to_date=None):
    """
    P&L from the native ERPNext General Ledger (GL Entry), NOT the legacy
    custom Journal Entry doctype (which is empty). Income = credit-debit,
    Expenses = debit-credit, over the period.
    """
    cost_center = _get_property_cost_center(property)
    income_breakdown = _gl_account_breakdown("Income", from_date, to_date, cost_center=cost_center)
    expense_breakdown = _gl_account_breakdown("Expense", from_date, to_date, cost_center=cost_center)
    total_income = sum(i["balance"] for i in income_breakdown)
    total_expenses = sum(e["balance"] for e in expense_breakdown)
    return {
        "property": property,
        "from_date": from_date,
        "to_date": to_date,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_profit": total_income - total_expenses,
        "income_breakdown": income_breakdown,
        "expense_breakdown": expense_breakdown,
    }


@frappe.whitelist()
def bank_reconciliation_report(bank_account, from_date=None, to_date=None):
    """Returns matched vs unmatched bank transactions."""
    filters = {"bank_account": bank_account}
    txs = frappe.get_all(
        "Bank Transaction",
        filters=filters,
        fields=["name", "transaction_date", "amount", "direction", "reference", "reconciled", "matched_journal_entry"]
    )

    reconciled = [t for t in txs if t.reconciled]
    unreconciled = [t for t in txs if not t.reconciled]

    return {
        "bank_account": bank_account,
        "total_transactions": len(txs),
        "reconciled_count": len(reconciled),
        "unreconciled_count": len(unreconciled),
        "reconciled_total": sum(flt(t.amount) for t in reconciled),
        "unreconciled_total": sum(flt(t.amount) for t in unreconciled),
        "transactions": txs
    }


@frappe.whitelist()
def generate_report_pdf(report_name=None, report_type=None, property=None, from_date=None, to_date=None):
    """
    Renders HTML print layout and returns PDF stream for financial & operations reports.
    Report names: 'profit_and_loss', 'financial_summary', 'rent_collection', 'rent_arrears', 'landlord_remittance', 'expense', 'payroll', 'vacancy'.
    """
    rtype = report_name or report_type or "financial_summary"

    if rtype in ("profit_and_loss", "financial_summary"):
        data = profit_and_loss(property, from_date, to_date)
        title = f"Profit & Loss / Financial Statement - {property or 'All Properties'}"
        body_html = f"""
        <table style="width: 100%; border-collapse: collapse; margin-top: 15px;">
            <tr style="background-color: #f8fafc; border-bottom: 2px solid #cbd5e1;">
                <th style="text-align: left; padding: 10px;">Metric</th>
                <th style="text-align: right; padding: 10px;">Amount (KSh)</th>
            </tr>
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 10px; color: #16a34a; font-weight: bold;">Total Operating Revenue</td>
                <td style="text-align: right; padding: 10px; font-weight: bold;">KSh {data.get('total_income', 0):,.2f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 10px; color: #dc2626; font-weight: bold;">Total Operating Expenses</td>
                <td style="text-align: right; padding: 10px; font-weight: bold;">KSh {data.get('total_expenses', 0):,.2f}</td>
            </tr>
            <tr style="background-color: #f1f5f9; font-size: 16px;">
                <td style="padding: 12px; font-weight: bold;">Net Operating Income (NOI)</td>
                <td style="text-align: right; padding: 12px; font-weight: bold; color: {'#16a34a' if data.get('net_profit', 0) >= 0 else '#dc2626'};">KSh {data.get('net_profit', 0):,.2f}</td>
            </tr>
        </table>
        """
    elif rtype == "rent_collection":
        data = rent_collection_report(property, from_date, to_date)
        title = f"Rent Collection Report - {property or 'All Properties'}"
        body_html = f"""
        <table style="width: 100%; border-collapse: collapse; margin-top: 15px;">
            <tr style="background-color: #f8fafc; border-bottom: 2px solid #cbd5e1;">
                <th style="text-align: left; padding: 10px;">Metric</th>
                <th style="text-align: right; padding: 10px;">Amount (KSh)</th>
            </tr>
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 10px;">Total Invoiced Rent</td>
                <td style="text-align: right; padding: 10px;">KSh {data.get('total_invoiced', 0):,.2f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 10px; color: #16a34a; font-weight: bold;">Total Rent Collected</td>
                <td style="text-align: right; padding: 10px; font-weight: bold;">KSh {data.get('total_collected', 0):,.2f}</td>
            </tr>
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 10px; color: #ea580c; font-weight: bold;">Outstanding Balance</td>
                <td style="text-align: right; padding: 10px; font-weight: bold;">KSh {data.get('total_outstanding', 0):,.2f}</td>
            </tr>
        </table>
        """
    elif rtype == "landlord_remittance":
        data = landlord_remittance_report(property, None, from_date, to_date)
        title = f"Landlord Remittance Statement - {property or 'All Properties'}"
        body_html = f"""
        <table style="width: 100%; border-collapse: collapse; margin-top: 15px;">
            <tr style="background-color: #f8fafc; border-bottom: 2px solid #cbd5e1;">
                <th style="text-align: left; padding: 10px;">Property</th>
                <th style="text-align: right; padding: 10px;">Gross Collected</th>
                <th style="text-align: right; padding: 10px;">Mgmt Fee</th>
                <th style="text-align: right; padding: 10px;">Expenses</th>
                <th style="text-align: right; padding: 10px;">Net Payout</th>
            </tr>
            {"".join(f'<tr style="border-bottom: 1px solid #e2e8f0;"><td style="padding: 8px;">{r["property_name"]} ({r["landlord"]})</td><td style="text-align: right; padding: 8px;">KSh {r["gross_collected"]:,.2f}</td><td style="text-align: right; padding: 8px; color: #dc2626;">-KSh {r["management_fee"]:,.2f}</td><td style="text-align: right; padding: 8px; color: #ea580c;">-KSh {r["property_expenses"]:,.2f}</td><td style="text-align: right; padding: 8px; font-weight: bold; color: #16a34a;">KSh {r["net_remittance"]:,.2f}</td></tr>' for r in data.get("remittances", []))}
            <tr style="background-color: #f1f5f9; font-weight: bold;">
                <td style="padding: 10px;">Total</td>
                <td style="text-align: right; padding: 10px;">KSh {data.get('total_gross_collected', 0):,.2f}</td>
                <td style="text-align: right; padding: 10px; color: #dc2626;">-KSh {data.get('total_management_fees', 0):,.2f}</td>
                <td style="text-align: right; padding: 10px; color: #ea580c;">-KSh {data.get('total_property_expenses', 0):,.2f}</td>
                <td style="text-align: right; padding: 10px; color: #16a34a;">KSh {data.get('total_net_remittance', 0):,.2f}</td>
            </tr>
        </table>
        """
    elif rtype == "rent_arrears":
        data = rent_arrears_report(property, from_date, to_date)
        title = f"Rent Arrears & Defaulters - {property or 'All Properties'}"
        body_html = f"""
        <h3>Total Arrears: KSh {data.get('total_arrears', 0):,.2f} (Overdue Accounts: {data.get('overdue_count', 0)})</h3>
        """
    else:
        title = f"Portfolio Report - {rtype.title().replace('_', ' ')}"
        body_html = f"<p>Report generated on {nowdate()}</p>"

    html = f"""
    <html>
        <head>
            <style>
                body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; padding: 25px; color: #1e293b; }}
                h1 {{ color: #0f172a; margin-bottom: 4px; font-size: 22px; }}
                h2 {{ color: #475569; font-size: 15px; margin-top: 0; margin-bottom: 20px; font-weight: normal; }}
                .header-box {{ border-bottom: 2px solid #0f172a; padding-bottom: 12px; margin-bottom: 20px; }}
                .footer {{ margin-top: 40px; font-size: 11px; color: #94a3b8; border-top: 1px solid #e2e8f0; padding-top: 10px; }}
            </style>
        </head>
        <body>
            <div class="header-box">
                <h1>DADIS Estates &bull; Nest Property Platform</h1>
                <h2>{title} &bull; Generated: {nowdate()}</h2>
            </div>
            {body_html}
            <div class="footer">
                Certified true report produced automatically by Nest Property Management System & Frappe Core.
            </div>
        </body>
    </html>
    """
    pdf_content = get_pdf(html)
    frappe.response["filename"] = f"{rtype}_report.pdf"
    frappe.response["filecontent"] = pdf_content
    frappe.response["type"] = "pdf"

@frappe.whitelist()
def report_branding(property=None):
    """
    Branding for report headers: company (organization) name, company logo URL if
    set, and the property name when a specific property is in scope. Falls back
    gracefully so exports always get at least a company name.
    """
    company_name = None
    logo = None

    # Prefer the ERPNext Company linked to the resolved Organization.
    try:
        from property_management.api.utils import resolve_organization
        org = resolve_organization()
    except Exception:
        org = None

    erpnext_company = None
    if org:
        company_name = frappe.db.get_value("Organization", org, "organization_name") or org
        erpnext_company = frappe.db.get_value("Organization", org, "erpnext_company")
    if not erpnext_company:
        companies = frappe.get_all("Company", fields=["name", "company_name"], limit=1)
        if companies:
            erpnext_company = companies[0].name
            company_name = company_name or companies[0].company_name

    if erpnext_company:
        company_name = company_name or frappe.db.get_value("Company", erpnext_company, "company_name") or erpnext_company
        logo = frappe.db.get_value("Company", erpnext_company, "company_logo")

    property_name = None
    if property and frappe.db.exists("Property", property):
        property_name = frappe.db.get_value("Property", property, "property_name") or property

    return {
        "company_name": company_name or "Dadis Estates Limited",
        "logo": logo,
        "property_name": property_name,
    }


@frappe.whitelist()
def recent_payments(limit=50):
    """
    Real money received across the platform: submitted Payment Entries plus paid
    M-Pesa Transactions (rent/deposit received during tenant onboarding are posted
    via M-Pesa, so they live here, not as Payment Entries).
    """
    out = []

    pes = frappe.get_all(
        "Payment Entry",
        filters={"docstatus": 1},
        fields=["name", "party", "paid_amount", "posting_date", "mode_of_payment"],
        order_by="posting_date desc", limit_page_length=int(limit),
    )
    for p in pes:
        out.append({
            "id": p.name,
            "tenant": p.party or "Party",
            "property": "Portfolio",
            "amount": flt(p.paid_amount),
            "date": str(p.posting_date)[:10],
            "method": p.mode_of_payment or "Bank",
            "status": "Reconciled",
        })

    txns = frappe.get_all(
        "Mpesa Transaction",
        filters={"status": "Paid"},
        fields=["name", "kind", "amount", "mpesa_receipt", "reference_doctype", "reference_name", "modified"],
        order_by="modified desc", limit_page_length=int(limit),
    )
    for t in txns:
        tenant_name = ""
        prop_name = ""
        if t.reference_doctype == "Lease Agreement" and frappe.db.exists("Lease Agreement", t.reference_name):
            le = frappe.db.get_value("Lease Agreement", t.reference_name, ["tenant", "property"], as_dict=True)
            if le:
                tenant_name = frappe.db.get_value("Property Tenant", le.tenant, "tenant_name") or le.tenant
                prop_name = frappe.db.get_value("Property", le.property, "property_name") or le.property
        out.append({
            "id": t.mpesa_receipt or t.name,
            "tenant": tenant_name or "Tenant",
            "property": prop_name or "Portfolio",
            "amount": flt(t.amount),
            "date": str(t.modified)[:10],
            "method": f"M-Pesa ({t.kind})" if t.kind else "M-Pesa",
            "status": "Paid",
        })

    out.sort(key=lambda x: x["date"], reverse=True)
    return out[: int(limit)]


@frappe.whitelist()
def get_properties_summary():
    """Returns list of properties with live counts of total units and occupied units."""
    props = frappe.get_all(
        "Property",
        fields=["name", "property_code", "property_name", "property_type", "address", "landlord", "caretaker", "status"],
        order_by="creation desc"
    )

    # Single grouped query for unit counts across ALL properties (avoids N+1).
    counts = frappe.db.get_all(
        "Property Unit",
        fields=["property", "status", "count(name) as cnt"],
        group_by="property, status",
    )
    by_prop = {}
    for row in counts:
        d = by_prop.setdefault(row["property"], {"total": 0, "Occupied": 0, "Vacant": 0})
        d["total"] += row["cnt"]
        if row["status"] in ("Occupied", "Vacant"):
            d[row["status"]] += row["cnt"]

    for p in props:
        c = by_prop.get(p.name, {"total": 0, "Occupied": 0, "Vacant": 0})
        p["total_units"] = c["total"]
        p["occupied_units"] = c["Occupied"]
        p["vacant_units"] = c["Vacant"]
    return props


@frappe.whitelist()
def get_dashboard_summary():
    """
    Returns live dashboard KPIs (cached ~60s to keep the landing page snappy):
    - revenue_this_month, total_overdue
    - revenue_trend (6-month chart data)
    - occupancy_breakdown (pie chart data)
    - recent_payments (last 5)
    """
    cache_key = "pm_dashboard_summary"
    cached = frappe.cache().get_value(cache_key)
    if cached:
        return cached
    result = _compute_dashboard_summary()
    frappe.cache().set_value(cache_key, result, expires_in_sec=60)
    return result


def _income_accounts(company=None):
    """Non-group Income accounts, optionally for one company."""
    filters = {"root_type": "Income", "is_group": 0}
    if company:
        filters["company"] = company
    return frappe.get_all("Account", filters=filters, pluck="name")


def _gl_income(from_date=None, to_date=None, company=None, cost_center=None):
    """
    Real revenue from the GL = credit - debit on Income accounts for the period.
    Deposits (a liability) are correctly excluded. Scoped to a company/cost_center if given.
    """
    accounts = _income_accounts(company)
    if not accounts:
        return 0.0
    filters = {"account": ["in", accounts], "is_cancelled": 0}
    if from_date and to_date:
        filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    if cost_center:
        filters["cost_center"] = cost_center
    rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(credit) as c", "sum(debit) as d"])
    if not rows:
        return 0.0
    return flt(rows[0].c) - flt(rows[0].d)


def _expense_accounts(company=None):
    """Non-group Expense accounts, optionally for one company."""
    filters = {"root_type": "Expense", "is_group": 0}
    if company:
        filters["company"] = company
    return frappe.get_all("Account", filters=filters, pluck="name")


def _gl_expense(from_date=None, to_date=None, company=None, cost_center=None):
    """Real expenses from the GL = debit - credit on Expense accounts for the period."""
    accounts = _expense_accounts(company)
    if not accounts:
        return 0.0
    filters = {"account": ["in", accounts], "is_cancelled": 0}
    if from_date and to_date:
        filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    if cost_center:
        filters["cost_center"] = cost_center
    rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(debit) as d", "sum(credit) as c"])
    if not rows:
        return 0.0
    return flt(rows[0].d) - flt(rows[0].c)


def _gl_account_breakdown(root_type, from_date=None, to_date=None, company=None, cost_center=None):
    """Per-account net balance for a root_type (Income: credit-debit, Expense: debit-credit)."""
    filters = {"root_type": root_type, "is_group": 0}
    if company:
        filters["company"] = company
    accounts = frappe.get_all("Account", filters=filters, fields=["name", "account_name"])
    if not accounts:
        return []
    name_map = {a.name: a.account_name for a in accounts}
    gl_filters = {"account": ["in", list(name_map.keys())], "is_cancelled": 0}
    if from_date and to_date:
        gl_filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    if cost_center:
        gl_filters["cost_center"] = cost_center
    rows = frappe.get_all(
        "GL Entry", filters=gl_filters,
        fields=["account", "sum(debit) as d", "sum(credit) as c"], group_by="account",
    )
    out = []
    for r in rows:
        bal = (flt(r.c) - flt(r.d)) if root_type == "Income" else (flt(r.d) - flt(r.c))
        if abs(bal) < 0.005:
            continue
        out.append({
            "account": r.account,
            "account_name": name_map.get(r.account, r.account),
            "account_type": root_type,
            "balance": bal,
        })
    out.sort(key=lambda x: x["balance"], reverse=True)
    return out


def _compute_dashboard_summary():
    from frappe.utils import get_first_day, get_last_day, add_months, formatdate
    today = getdate(nowdate())
    first_of_month = get_first_day(today)
    last_of_month = get_last_day(today)

    # Revenue this month — from the GL (income accounts), NOT legacy invoices.
    revenue_this_month = _gl_income(first_of_month, last_of_month)

    # Total overdue — from submitted Sales Invoices' outstanding (0 until monthly
    # invoices are generated; grows correctly once they are).
    overdue_rows = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<", str(today)]},
        fields=["outstanding_amount"],
    )
    total_overdue = sum(flt(i.outstanding_amount) for i in overdue_rows)

    # Occupancy breakdown
    all_units = frappe.get_all("Property Unit", fields=["status"])
    total_units = len(all_units)
    occupied = len([u for u in all_units if u.status == "Occupied"])
    vacant = len([u for u in all_units if u.status == "Vacant"])
    maintenance = len([u for u in all_units if u.status not in ("Occupied", "Vacant")])

    if total_units > 0:
        occupancy_breakdown = [
            {"name": "Occupied", "value": round(occupied / total_units * 100), "color": "#14b98a"},
            {"name": "Vacant", "value": round(vacant / total_units * 100), "color": "#f59e0b"},
            {"name": "Maintenance", "value": round(maintenance / total_units * 100), "color": "#ef4444"},
        ]
    else:
        occupancy_breakdown = [
            {"name": "Occupied", "value": 0, "color": "#14b98a"},
            {"name": "Vacant", "value": 0, "color": "#f59e0b"},
            {"name": "Maintenance", "value": 0, "color": "#ef4444"},
        ]

    # Revenue trend (last 6 months) — GL income per month.
    revenue_trend = []
    for i in range(5, -1, -1):
        m_ref = add_months(today, -i)
        m_start = get_first_day(m_ref)
        m_end = get_last_day(m_ref)
        revenue_trend.append({
            "month": formatdate(m_ref, "MMM"),
            "full_month": formatdate(m_ref, "MMM YYYY"),
            "revenue": _gl_income(m_start, m_end),
            "expenses": _gl_expense(m_start, m_end),
        })

    # Recent payments — real receipts from paid M-Pesa Transactions.
    txns = frappe.get_all(
        "Mpesa Transaction",
        filters={"status": "Paid"},
        fields=["name", "kind", "amount", "mpesa_receipt", "reference_doctype", "reference_name", "modified"],
        order_by="modified desc", limit_page_length=5,
    )
    payments_list = []
    for t in txns:
        tenant_name = ""
        prop_name = ""
        if t.reference_doctype == "Lease Agreement" and frappe.db.exists("Lease Agreement", t.reference_name):
            le = frappe.db.get_value("Lease Agreement", t.reference_name, ["tenant", "property"], as_dict=True)
            if le:
                tenant_name = frappe.db.get_value("Property Tenant", le.tenant, "tenant_name") or le.tenant
                prop_name = frappe.db.get_value("Property", le.property, "property_name") or le.property
        payments_list.append({
            "id": t.mpesa_receipt or t.name,
            "tenant": tenant_name or "Tenant",
            "property": prop_name or "Portfolio",
            "amount": flt(t.amount),
            "date": str(t.modified)[:10],
            "method": "M-Pesa",
            "status": "Paid",
        })

    return {
        "revenue_this_month": revenue_this_month,
        "total_overdue": total_overdue,
        "total_units": total_units,
        "occupied_units": occupied,
        "vacant_units": vacant,
        "occupancy_breakdown": occupancy_breakdown,
        "revenue_trend": revenue_trend,
        "recent_payments": payments_list,
    }



# ============================================================================
# STRUCTURED FINANCIAL STATEMENTS
#
# Returns fully-structured statements (title + columns + rows) built from REAL
# records so the frontend renders whatever the backend sends — no hardcoded
# line items. Each row is:
#   {"kind": "section|item|subtotal|total|grand|spacer",
#    "label": str,
#    "values": {colKey: number}}   # only for item/subtotal/total/grand rows
#
# kind = "financial_statement" report types:
#   income_statement, balance_sheet, cashflow_statement,
#   cost_tracking, loan_schedule, project_pipeline
# ============================================================================

from frappe.utils import get_first_day, get_last_day

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _row(kind, label="", values=None):
    r = {"kind": kind, "label": label}
    if values is not None:
        r["values"] = values
    return r


def _year_bounds(year):
    return f"{year}-01-01", f"{year}-12-31"


def _month_bounds(year, month_index):
    from datetime import date
    start = date(year, month_index + 1, 1)
    return str(get_first_day(start)), str(get_last_day(start))


def _resolve_company_for_property(property=None):
    """Company name for scoping Account lists, mirroring v2 resolution."""
    company = None
    if property:
        org = frappe.db.get_value("Property", property, "organization")
        if org:
            company = frappe.db.get_value("Organization", org, "erpnext_company")
    if not company:
        try:
            from property_management.api.utils import resolve_organization
            org = resolve_organization()
            company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
        except Exception:
            company = None
    if not company:
        companies = frappe.get_all("Company", pluck="name", limit=1)
        company = companies[0] if companies else None
    return company


def _accounts_by_root(root_type, company=None):
    filters = {"root_type": root_type, "is_group": 0}
    if company:
        filters["company"] = company
    return frappe.get_all("Account", filters=filters, fields=["name", "account_name", "account_type"])


def _gl_account_income_by_cc(account, from_date, to_date, cost_center):
    """Credit-debit for one account within a cost center over a period."""
    if not account or not cost_center:
        return 0.0
    filters = {"account": account, "is_cancelled": 0, "cost_center": cost_center}
    if from_date and to_date:
        filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(credit) as c", "sum(debit) as d"])
    if not rows:
        return 0.0
    return flt(rows[0].c) - flt(rows[0].d)


def _gl_account_balance(account, from_date=None, to_date=None, cost_center=None, sign="debit"):
    """Net balance for a single account. sign='debit' -> debit-credit, else credit-debit."""
    filters = {"account": account, "is_cancelled": 0}
    if from_date and to_date:
        filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    if cost_center:
        filters["cost_center"] = cost_center
    rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(debit) as d", "sum(credit) as c"])
    if not rows:
        return 0.0
    d, c = flt(rows[0].d), flt(rows[0].c)
    return (d - c) if sign == "debit" else (c - d)


# ---- Income Statement -------------------------------------------------------
def _income_statement(property, year, months):
    """
    Rental income grouped per Property (real records), services + expenses from
    the GL. Months across; each property is a line item under 'A - Rental Income'.
    """
    company = _resolve_company_for_property(property)
    month_keys = _MONTHS[:months]
    columns = [{"key": "label", "header": "", "kind": "label"}]
    columns += [{"key": m, "header": m, "kind": "money"} for m in month_keys]

    # Properties in scope (one line each).
    prop_filters = {}
    if property:
        prop_filters["name"] = property
    properties = frappe.get_all("Property", filters=prop_filters,
                                fields=["name", "property_name", "cost_center", "rent_income_account", "utility_income_account"])

    rows = [_row("section", "Revenues"), _row("section", "A - Rental Income")]

    # Per-property monthly rental income. Prefer the property's COST CENTER, which
    # captures rent posted to any income account tagged to that property (e.g.
    # direct M-Pesa receipts that land on a shared "Rent Income" account but carry
    # the property's cost center). Fall back to the dedicated rent_income_account
    # only when the property has no cost center.
    # Precompute, per property/month: total income (by cost center) and utility
    # income (by cost center), so rental = total − utilities (no double counting).
    rental_subtotal = {m: 0.0 for m in month_keys}
    services_subtotal = {m: 0.0 for m in month_keys}
    per_prop_util = {}  # property.name -> {month: utility_income}
    for p in properties:
        util_vals = {}
        rent_vals = {}
        cc = p.get("cost_center")
        for mi, m in enumerate(month_keys):
            fd, td = _month_bounds(year, mi)
            total_inc = _gl_income(fd, td, cost_center=cc) if cc else (
                _gl_account_balance(p["rent_income_account"], fd, td, sign="credit")
                if p.get("rent_income_account") else 0.0)
            util_inc = _gl_account_income_by_cc(p.get("utility_income_account"), fd, td, cc) if cc else 0.0
            rent_inc = total_inc - util_inc
            if rent_inc:
                rent_vals[m] = rent_inc
                rental_subtotal[m] += rent_inc
            if util_inc:
                util_vals[m] = util_inc
                services_subtotal[m] += util_inc
        per_prop_util[p["name"]] = util_vals
        rows.append(_row("item", p.get("property_name") or p["name"], rent_vals))
    rows.append(_row("subtotal", "Sub-Total", {k: v for k, v in rental_subtotal.items() if v}))

    # Services income (utility income) — one line per property that has any.
    rows.append(_row("spacer"))
    rows.append(_row("section", "B - Services Income"))
    for p in properties:
        util_vals = per_prop_util.get(p["name"], {})
        if util_vals:
            rows.append(_row("item", "%s - Utilities" % (p.get("property_name") or p["name"]), util_vals))
    rows.append(_row("subtotal", "Sub-Total", {k: v for k, v in services_subtotal.items() if v}))

    # Operating revenue = rental + services.
    op_rev = {}
    for m in month_keys:
        tot = rental_subtotal.get(m, 0.0) + services_subtotal.get(m, 0.0)
        if tot:
            op_rev[m] = tot
    rows.append(_row("spacer"))
    rows.append(_row("total", "Operating Revenue", op_rev))

    # Expenses — real expense accounts from the GL, one line each.
    rows.append(_row("spacer"))
    rows.append(_row("section", "Expenses"))
    rows.append(_row("section", "A - Rental Operating Costs"))
    op_exp = {m: 0.0 for m in month_keys}
    for acc in _accounts_by_root("Expense", company):
        vals = {}
        for mi, m in enumerate(month_keys):
            fd, td = _month_bounds(year, mi)
            cc = _get_property_cost_center(property) if property else None
            amt = _gl_account_balance(acc["name"], fd, td, cost_center=cc, sign="debit")
            if amt:
                vals[m] = amt
                op_exp[m] += amt
        if vals:
            rows.append(_row("item", acc.get("account_name") or acc["name"], vals))
    rows.append(_row("total", "Operating Expenses", {k: v for k, v in op_exp.items() if v}))

    # Revenue after expenses + profit.
    net = {}
    for m in month_keys:
        n = op_rev.get(m, 0.0) - op_exp.get(m, 0.0)
        if n:
            net[m] = n
    rows.append(_row("spacer"))
    rows.append(_row("total", "Revenue", net))
    rows.append(_row("spacer"))
    rows.append(_row("section", "Financing Costs"))
    rows.append(_row("total", "Profit Before Tax", net))
    rows.append(_row("spacer"))
    rows.append(_row("grand", "Profit", net))

    return {"id": "income_statement", "title": f"INCOME STATEMENT \u2013 {year} (KShs.)",
            "columns": columns, "rows": rows}


# ---- Balance Sheet ----------------------------------------------------------
def _balance_sheet(property, year):
    """Assets / Liabilities / Equity per real GL account (mirrors v2 balance_sheet logic)."""
    fd, td = _year_bounds(year)
    company = _resolve_company_for_property(property)
    cost_center = _get_property_cost_center(property) if property else None
    columns = [{"key": "label", "header": "ASSETS", "kind": "label"},
               {"key": "amount", "header": f"Yr {year} KShs.", "kind": "money"}]

    def section_rows(root_type, sign, header):
        out = [_row("section", header)]
        total = 0.0
        for acc in _accounts_by_root(root_type, company):
            bal = _gl_account_balance(acc["name"], fd, td, cost_center=cost_center, sign=sign)
            if abs(bal) > 0.005:
                out.append(_row("item", acc.get("account_name") or acc["name"], {"amount": bal}))
                total += bal
        return out, total

    asset_rows, total_assets = section_rows("Asset", "debit", "Non-Current Assets")
    liab_rows, total_liab = section_rows("Liability", "credit", "Non-Current Liabilities")
    equity_rows, total_equity = section_rows("Equity", "credit", "EQUITY")

    # Net profit rolls into equity.
    income_total = sum(_gl_account_balance(a["name"], fd, td, cost_center=cost_center, sign="credit")
                       for a in _accounts_by_root("Income", company))
    expense_total = sum(_gl_account_balance(a["name"], fd, td, cost_center=cost_center, sign="debit")
                        for a in _accounts_by_root("Expense", company))
    net_profit = income_total - expense_total
    equity_total_final = total_equity + net_profit

    rows = []
    rows += asset_rows
    rows.append(_row("total", "Total Assets", {"amount": total_assets}))
    rows.append(_row("spacer"))
    rows += equity_rows
    if net_profit:
        rows.append(_row("item", "Retained Earnings", {"amount": net_profit}))
    rows.append(_row("total", "Total Equity", {"amount": equity_total_final}))
    rows.append(_row("spacer"))
    rows += liab_rows
    rows.append(_row("subtotal", "Sub-Total", {"amount": total_liab}))
    rows.append(_row("spacer"))
    rows.append(_row("total", "Total Equity & Liabilities", {"amount": equity_total_final + total_liab}))

    return {"id": "balance_sheet", "title": f"BALANCE SHEET \u2013 {year} (KShs.)",
            "columns": columns, "rows": rows}


# ---- Cashflow Statement -----------------------------------------------------
def _cashflow_statement(property, year):
    """Indirect-method operating cash from GL net profit; investing/financing left blank (no source doctypes)."""
    fd, td = _year_bounds(year)
    cost_center = _get_property_cost_center(property) if property else None
    income = _gl_income(fd, td, cost_center=cost_center)
    expense = _gl_expense(fd, td, cost_center=cost_center)
    net_profit = income - expense

    columns = [{"key": "label", "header": "", "kind": "label"},
               {"key": "amount", "header": "", "kind": "money"}]
    rows = [
        _row("section", "Cashflow From Operating Activities"),
        _row("item", "Cash Generated from Operations", {"amount": net_profit} if net_profit else {}),
        _row("total", "Net cash generated from Operating Activities", {"amount": net_profit} if net_profit else {}),
        _row("spacer"),
        _row("section", "Cash from Investing Activities"),
        _row("total", "Net Cash used in Investing Activities", {}),
        _row("spacer"),
        _row("section", "Cash from Financing Activities"),
        _row("total", "Net Cash from Financing Activities", {}),
        _row("spacer"),
        _row("total", "Net increase/decrease in Cash and cash equivalents", {"amount": net_profit} if net_profit else {}),
    ]
    return {"id": "cashflow_statement", "title": f"CASHFLOW STATEMENT \u2013 {year} (KShs.)",
            "columns": columns, "rows": rows}


# ---- Cost Tracking ----------------------------------------------------------
def _cost_tracking(property, year):
    """Monthly expense per GL expense account + Totals column. All real GL data."""
    company = _resolve_company_for_property(property)
    cost_center = _get_property_cost_center(property) if property else None
    columns = [{"key": "label", "header": "Operating Costs", "kind": "label"}]
    columns += [{"key": m, "header": m, "kind": "money"} for m in _MONTHS]
    columns.append({"key": "total", "header": "Totals", "kind": "money", "emphasise": True})

    rows = [_row("section", "Operating Costs")]
    grand_by_month = {m: 0.0 for m in _MONTHS}
    grand_total = 0.0
    for acc in _accounts_by_root("Expense", company):
        vals = {}
        line_total = 0.0
        for mi, m in enumerate(_MONTHS):
            fd, td = _month_bounds(year, mi)
            amt = _gl_account_balance(acc["name"], fd, td, cost_center=cost_center, sign="debit")
            if amt:
                vals[m] = amt
                grand_by_month[m] += amt
                line_total += amt
        if line_total:
            vals["total"] = line_total
            grand_total += line_total
            rows.append(_row("item", acc.get("account_name") or acc["name"], vals))
    total_vals = {k: v for k, v in grand_by_month.items() if v}
    if grand_total:
        total_vals["total"] = grand_total
    rows.append(_row("total", "Total", total_vals))
    return {"id": "cost_tracking", "title": f"COST TRACKING \u2013 {year} (KShs.)",
            "columns": columns, "rows": rows}


# ---- Loan Schedule ----------------------------------------------------------
def _loan_schedule(property, year):
    """
    Loans sourced from Liability accounts in the GL (no dedicated Loan doctype
    exists). Each liability account = one loan line; balance = credit-debit.
    """
    company = _resolve_company_for_property(property)
    fd, td = _year_bounds(year)
    columns = [
        {"key": "label", "header": "Loans", "kind": "label"},
        {"key": "loan_bal", "header": f"Loan Bal - {year}", "kind": "money"},
    ]
    rows = []
    total = 0.0
    for acc in _accounts_by_root("Liability", company):
        bal = _gl_account_balance(acc["name"], fd, td, sign="credit")
        if abs(bal) > 0.005:
            rows.append(_row("item", acc.get("account_name") or acc["name"], {"loan_bal": bal}))
            total += bal
    rows.append(_row("total", "TOTAL", {"loan_bal": total}))
    return {"id": "loan_schedule", "title": f"FINANCING COSTS \u2013 LOAN SCHEDULE ({year}) (KShs.)",
            "columns": columns, "rows": rows}


# ---- Project Pipeline -------------------------------------------------------
def _project_pipeline(property):
    """Construction Project records (real). sqf/units/rent-per-unit fields don't exist -> blank."""
    columns = [
        {"key": "no", "header": "No.", "kind": "text"},
        {"key": "label", "header": "Project Name", "kind": "label"},
        {"key": "status", "header": "Status", "kind": "text"},
        {"key": "delivery", "header": "Delivery Date", "kind": "text"},
        {"key": "project_cost", "header": "Project Cost", "kind": "money"},
        {"key": "actual_spend", "header": "Actual Spend", "kind": "money"},
    ]
    filters = {}
    if property:
        filters["property"] = property
    projects = frappe.get_all("Construction Project", filters=filters,
                              fields=["project_name", "status", "expected_end_date", "total_budget", "total_actual_spend"],
                              order_by="creation asc")
    rows = []
    total_budget = 0.0
    total_actual = 0.0
    for i, p in enumerate(projects, start=1):
        rows.append({
            "kind": "item", "no": i, "label": p.get("project_name"),
            "status": p.get("status") or "",
            "delivery": str(p.get("expected_end_date"))[:10] if p.get("expected_end_date") else "",
            "values": {
                "project_cost": flt(p.get("total_budget")),
                "actual_spend": flt(p.get("total_actual_spend")),
            },
        })
        total_budget += flt(p.get("total_budget"))
        total_actual += flt(p.get("total_actual_spend"))
    rows.append(_row("total", "TOTAL", {"project_cost": total_budget, "actual_spend": total_actual}))
    return {"id": "project_pipeline", "title": "PROJECT PIPELINE", "columns": columns, "rows": rows}


@frappe.whitelist()
def financial_statement(kind=None, property=None, year=None, months=12, from_date=None, to_date=None):
    """
    Return a fully-structured financial statement built from real records.
    kind: income_statement | balance_sheet | cashflow_statement |
          cost_tracking | loan_schedule | project_pipeline
    """
    year = int(year) if year else getdate(nowdate()).year
    months = int(months) if months else 12
    months = max(1, min(12, months))

    if kind == "income_statement":
        return _income_statement(property, year, months)
    if kind == "balance_sheet":
        return _balance_sheet(property, year)
    if kind == "cashflow_statement":
        return _cashflow_statement(property, year)
    if kind == "cost_tracking":
        return _cost_tracking(property, year)
    if kind == "loan_schedule":
        return _loan_schedule(property, year)
    if kind == "project_pipeline":
        return _project_pipeline(property)

    frappe.throw(f"Unknown statement kind: {kind}")


@frappe.whitelist()
def rent_collection_by_property(months=6):
    """
    Monthly rent COLLECTED (GL income) broken down per property, for a grouped
    bar chart on the admin dashboard.

    Returns:
      {
        "months": ["Apr", "May", ...],          # chronological labels
        "properties": [{"key": "p_<name>", "id": <property>, "name": <property_name>}],
        "series": [ {"month": "Apr", "p_<name>": 12000, ...}, ... ]  # one row per month
      }
    Each property becomes a series keyed by a stable, chart-safe key ("p_<docname>")
    so the frontend can render one <Bar> per property regardless of naming.
    """
    from frappe.utils import get_first_day, get_last_day

    months = max(1, min(12, int(months or 6)))
    today = getdate(nowdate())

    # Properties that have a cost center can be attributed in the GL. Include all
    # properties for labels; those without a cost center simply show zero.
    props = frappe.get_all(
        "Property",
        fields=["name", "property_name", "cost_center"],
        order_by="property_name asc",
    )
    prop_meta = []
    for p in props:
        prop_meta.append({
            "key": f"p_{p.name}",
            "id": p.name,
            "name": p.property_name or p.name,
            "cost_center": p.cost_center,
        })

    month_labels = []
    series = []
    for i in range(months - 1, -1, -1):
        m_ref = add_months(today, -i)
        m_start = get_first_day(m_ref)
        m_end = get_last_day(m_ref)
        label = formatdate(m_ref, "MMM")
        month_labels.append(label)

        row = {"month": label, "full_month": formatdate(m_ref, "MMM YYYY")}
        for pm in prop_meta:
            amount = 0.0
            if pm["cost_center"]:
                amount = _gl_income(m_start, m_end, cost_center=pm["cost_center"])
            row[pm["key"]] = flt(amount)
        series.append(row)

    return {
        "months": month_labels,
        # Strip cost_center from the public payload; the frontend only needs key/id/name.
        "properties": [{"key": pm["key"], "id": pm["id"], "name": pm["name"]} for pm in prop_meta],
        "series": series,
    }
