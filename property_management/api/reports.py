import frappe
from frappe.utils import flt, nowdate, getdate, formatdate, add_months
from frappe.utils.pdf import get_pdf
from collections import OrderedDict


@frappe.whitelist()
def rent_collection_report(property=None, from_date=None, to_date=None):
    """
    Rent collection from native ERPNext. Invoiced/outstanding come from submitted
    Sales Invoices; collected comes from the GL income accounts (real money received,
    including direct M-Pesa rent postings before monthly invoicing exists).
    """
    si_filters = {"docstatus": 1}
    if from_date and to_date:
        si_filters["posting_date"] = ["between", [str(from_date), str(to_date)]]

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
    total_collected = _gl_income(from_date, to_date) if (from_date and to_date) else _gl_income()

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
    today = getdate(nowdate())
    overdue_invoices = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0]},
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
    trend = []
    curr = getdate(nowdate())
    for i in range(int(months) - 1, -1, -1):
        m_ref = add_months(curr, -i)
        m_start = get_first_day(m_ref)
        m_end = get_last_day(m_ref)
        trend.append({
            "month": formatdate(m_ref, "MMM"),
            "full_month": formatdate(m_ref, "MMM YYYY"),
            "revenue": _gl_income(m_start, m_end),
            "expenses": _gl_expense(m_start, m_end),
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
    breakdown = _gl_account_breakdown("Expense", from_date, to_date)
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
    income_breakdown = _gl_account_breakdown("Income", from_date, to_date)
    expense_breakdown = _gl_account_breakdown("Expense", from_date, to_date)
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


def _gl_income(from_date=None, to_date=None, company=None):
    """
    Real revenue from the GL = credit - debit on Income accounts for the period.
    Deposits (a liability) are correctly excluded. Scoped to a company if given.
    """
    accounts = _income_accounts(company)
    if not accounts:
        return 0.0
    filters = {"account": ["in", accounts], "is_cancelled": 0}
    if from_date and to_date:
        filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
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


def _gl_expense(from_date=None, to_date=None, company=None):
    """Real expenses from the GL = debit - credit on Expense accounts for the period."""
    accounts = _expense_accounts(company)
    if not accounts:
        return 0.0
    filters = {"account": ["in", accounts], "is_cancelled": 0}
    if from_date and to_date:
        filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
    rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(debit) as d", "sum(credit) as c"])
    if not rows:
        return 0.0
    return flt(rows[0].d) - flt(rows[0].c)


def _gl_account_breakdown(root_type, from_date=None, to_date=None, company=None):
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

