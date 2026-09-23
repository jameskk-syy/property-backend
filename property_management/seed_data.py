import frappe
from frappe.utils import nowdate, add_months, getdate, flt

def seed_sample_data():
    print("Starting data seed for DADIS Estates Property Management...")
    
    # 1. Organization
    org_name = "DADIS Estates"
    if not frappe.db.exists("Organization", org_name):
        frappe.get_doc({
            "doctype": "Organization",
            "organization_name": org_name,
            "currency": "KES"
        }).insert(ignore_permissions=True)
        print(f"Created Organization: {org_name}")

    # 2. Landlords
    landlords_data = [
        {"name": "Susan Njoroge", "phone": "+254 722 111 222", "email": "susan.njoroge@gmail.com"},
        {"name": "David Mutua", "phone": "+254 733 333 444", "email": "david.mutua@outlook.com"},
        {"name": "Faith Mwangi", "phone": "+254 711 555 666", "email": "faith.mwangi@yahoo.com"}
    ]
    landlords = []
    for ld in landlords_data:
        existing = frappe.db.get_value("Landlord", {"landlord_name": ld["name"]}, "name")
        if not existing:
            doc = frappe.get_doc({
                "doctype": "Landlord",
                "organization": org_name,
                "landlord_name": ld["name"],
                "phone": ld["phone"],
                "email": ld["email"]
            }).insert(ignore_permissions=True)
            landlords.append(doc.name)
        else:
            landlords.append(existing)
    print(f"Landlords ready: {len(landlords)}")

    # 3. Caretakers
    caretakers_data = [
        {"name": "John Kiptoo", "phone": "+254 712 999 888", "email": "john.kiptoo@dadisestates.co.ke"},
        {"name": "Grace Wanjiru", "phone": "+254 724 444 333", "email": "grace.wanjiru@dadisestates.co.ke"},
        {"name": "Peter Otieno", "phone": "+254 701 777 888", "email": "peter.otieno@dadisestates.co.ke"}
    ]
    caretakers = []
    for cd in caretakers_data:
        existing = frappe.db.get_value("Caretaker", {"caretaker_name": cd["name"]}, "name")
        if not existing:
            doc = frappe.get_doc({
                "doctype": "Caretaker",
                "organization": org_name,
                "caretaker_name": cd["name"],
                "phone": cd["phone"],
                "email": cd["email"]
            }).insert(ignore_permissions=True)
            caretakers.append(doc.name)
        else:
            caretakers.append(existing)
    print(f"Caretakers ready: {len(caretakers)}")

    # 4. Properties
    properties_data = [
        {
            "code": "PROP-GVA",
            "name": "Greenview Apartments",
            "address": "Argwings Kodhek Rd, Kilimani, Nairobi",
            "type": "Residential",
            "landlord": landlords[0],
            "caretaker": caretakers[1],
            "units_count": 8,
            "base_rent": 45000,
        },
        {
            "code": "PROP-SNC",
            "name": "Sunrise Court",
            "address": "Riara Road, Kilimani, Nairobi",
            "type": "Residential",
            "landlord": landlords[1],
            "caretaker": caretakers[0],
            "units_count": 6,
            "base_rent": 35000,
        },
        {
            "code": "PROP-VVH",
            "name": "Valley View Heights",
            "address": "Kindaruma Road, Kilimani, Nairobi",
            "type": "Mixed Use",
            "landlord": landlords[2],
            "caretaker": caretakers[2],
            "units_count": 6,
            "base_rent": 55000,
        }
    ]

    properties = []
    for pd in properties_data:
        existing = frappe.db.get_value("Property", {"property_code": pd["code"]}, "name")
        if not existing:
            doc = frappe.get_doc({
                "doctype": "Property",
                "organization": org_name,
                "property_code": pd["code"],
                "property_name": pd["name"],
                "address": pd["address"],
                "property_type": pd["type"],
                "landlord": pd["landlord"],
                "caretaker": pd["caretaker"],
                "status": "Active"
            }).insert(ignore_permissions=True)
            prop_name = doc.name
        else:
            prop_name = existing
        properties.append({"name": prop_name, "config": pd})
    print(f"Properties ready: {len(properties)}")

    # 5. Tenants
    tenants_data = [
        {"name": "James Maina", "phone": "+254 711 234 567", "id_no": "29847583", "email": "james.maina@gmail.com"},
        {"name": "Sarah Kamau", "phone": "+254 722 345 678", "id_no": "31485920", "email": "sarah.kamau@yahoo.com"},
        {"name": "Kelvin Omondi", "phone": "+254 733 456 789", "id_no": "28374921", "email": "kelvin.omondi@outlook.com"},
        {"name": "Lucy Wambui", "phone": "+254 701 567 890", "id_no": "33495821", "email": "lucy.wambui@gmail.com"},
        {"name": "Brian Kiprop", "phone": "+254 712 678 901", "id_no": "30194827", "email": "brian.kiprop@gmail.com"},
        {"name": "Mercy Achieng", "phone": "+254 723 789 012", "id_no": "27384910", "email": "mercy.achieng@hotmail.com"},
        {"name": "Dennis Mutiso", "phone": "+254 734 890 123", "id_no": "32194857", "email": "dennis.mutiso@gmail.com"},
        {"name": "Esther Nyambura", "phone": "+254 702 901 234", "id_no": "29485719", "email": "esther.nyambura@yahoo.com"},
        {"name": "Victor Odhiambo", "phone": "+254 713 012 345", "id_no": "34958271", "email": "victor.odhiambo@gmail.com"},
        {"name": "Alice Chebet", "phone": "+254 724 123 456", "id_no": "31827495", "email": "alice.chebet@gmail.com"}
    ]
    tenants = []
    for td in tenants_data:
        existing = frappe.db.get_value("Property Tenant", {"tenant_name": td["name"]}, "name")
        if not existing:
            doc = frappe.get_doc({
                "doctype": "Property Tenant",
                "organization": org_name,
                "tenant_name": td["name"],
                "phone": td["phone"],
                "email": td["email"],
                "national_id": td["id_no"],
                "status": "Active"
            }).insert(ignore_permissions=True)
            tenants.append(doc.name)
        else:
            tenants.append(existing)
    print(f"Tenants ready: {len(tenants)}")

    # 6. Units
    all_units = []
    unit_prefixes = ["A-", "B-", "C-"]
    tenant_idx = 0
    for p_idx, p in enumerate(properties):
        prefix = unit_prefixes[p_idx % len(unit_prefixes)]
        count = p["config"]["units_count"]
        base_rent = p["config"]["base_rent"]

        for u in range(1, count + 1):
            unit_num = f"{prefix}{100 + u}"
            existing = frappe.db.get_value("Property Unit", {"property": p["name"], "unit_number": unit_num}, "name")
            is_occupied = (u <= count - 1)  # 1 vacant per property
            unit_type = "2 Bedroom" if u % 2 == 0 else "1 Bedroom"
            floor = f"{(u - 1) // 3 + 1}st Floor"

            if not existing:
                doc = frappe.get_doc({
                    "doctype": "Property Unit",
                    "organization": org_name,
                    "property": p["name"],
                    "unit_number": unit_num,
                    "unit_type": unit_type,
                    "floor": floor,
                    "base_rent": base_rent,
                    "security_deposit": base_rent,
                    "status": "Occupied" if is_occupied else "Vacant"
                }).insert(ignore_permissions=True)
                unit_name = doc.name
            else:
                unit_name = existing

            assigned_tenant = None
            if is_occupied and tenant_idx < len(tenants):
                assigned_tenant = tenants[tenant_idx]
                tenant_idx = (tenant_idx + 1) % len(tenants)

            all_units.append({
                "name": unit_name,
                "unit_number": unit_num,
                "property": p["name"],
                "base_rent": base_rent,
                "tenant": assigned_tenant,
                "status": "Occupied" if is_occupied else "Vacant"
            })
    print(f"Units ready: {len(all_units)}")

    # 7. Property Invoices & Payments for the last 6 months
    today = getdate(nowdate())
    invoice_count = 0
    
    for m in range(5, -1, -1):
        month_date = add_months(today, -m)
        posting_date = month_date.strftime("%Y-%m-01")
        due_date = month_date.strftime("%Y-%m-05")

        for u in all_units:
            if not u["tenant"]:
                continue

            rent_amt = flt(u["base_rent"])
            
            # Determine payment status
            if m == 0 and u["unit_number"] in ("A-102", "B-103"):
                paid_amt = 0.0
                outstanding = rent_amt
                status = "Overdue"
            elif m == 0 and u["unit_number"] in ("A-104", "C-102"):
                paid_amt = rent_amt / 2.0
                outstanding = rent_amt / 2.0
                status = "Partially Paid"
            else:
                paid_amt = rent_amt
                outstanding = 0.0
                status = "Paid"

            # Check if invoice already exists for this unit & posting date
            existing = frappe.db.get_value("Property Invoice", {"unit": u["name"], "posting_date": posting_date}, "name")
            if not existing:
                doc = frappe.get_doc({
                    "doctype": "Property Invoice",
                    "organization": org_name,
                    "property": u["property"],
                    "unit": u["name"],
                    "tenant": u["tenant"],
                    "invoice_type": "Rent",
                    "posting_date": posting_date,
                    "due_date": due_date,
                    "total_amount": rent_amt,
                    "paid_amount": paid_amt,
                    "outstanding_amount": outstanding,
                    "status": status,
                    "items": [
                        {
                            "doctype": "Property Invoice Item",
                            "item_name": "Monthly Rent",
                            "description": f"Rent for {u['unit_number']} - {month_date.strftime('%B %Y')}",
                            "quantity": 1,
                            "rate": rent_amt,
                            "amount": rent_amt
                        }
                    ]
                })
                doc.flags.ignore_mandatory = True
                doc.insert(ignore_permissions=True)
                invoice_count += 1

    print(f"Invoices generated: {invoice_count}")

    # 8. Property Expenses
    expenses_data = [
        {"vendor": "Nairobi Water Co.", "category": "Utility", "amount": 42000, "desc": "Monthly bulk water supply & meter reading", "months_ago": 0},
        {"vendor": "Alpha Security Services", "category": "General", "amount": 85000, "desc": "Day and night 24/7 security guard detail", "months_ago": 0},
        {"vendor": "Apex Lift Maintenance", "category": "Maintenance", "amount": 45000, "desc": "Quarterly elevator inspection and hydraulic servicing", "months_ago": 0},
        {"vendor": "CleanCity Waste Handlers", "category": "General", "amount": 18000, "desc": "Weekly garbage collection and sorting", "months_ago": 0},
        {"vendor": "Kenya Power & Lighting", "category": "Utility", "amount": 38000, "desc": "Common area, security lights and pump electricity", "months_ago": 1},
        {"vendor": "Fundi Pro Plumbing", "category": "Plumbing", "amount": 28000, "desc": "Booster pump motor replacement and pipe repair", "months_ago": 1},
        {"vendor": "Alpha Security Services", "category": "General", "amount": 85000, "desc": "Security guarding services", "months_ago": 1},
        {"vendor": "Modern Paints & Hardware", "category": "Painting", "amount": 62000, "desc": "Perimeter wall and corridor repaint", "months_ago": 2},
        {"vendor": "Nairobi Water Co.", "category": "Utility", "amount": 40000, "desc": "Monthly water supply", "months_ago": 2},
        {"vendor": "Alpha Security Services", "category": "General", "amount": 85000, "desc": "Security guarding detail", "months_ago": 2},
        {"vendor": "Nairobi Water Co.", "category": "Utility", "amount": 39500, "desc": "Monthly water supply", "months_ago": 3},
        {"vendor": "Alpha Security Services", "category": "General", "amount": 85000, "desc": "Security guarding detail", "months_ago": 3}
    ]

    for exp in expenses_data:
        prop = properties[0]["name"]
        exp_date = add_months(today, -exp["months_ago"]).strftime("%Y-%m-10 10:00:00")
        doc = frappe.get_doc({
            "doctype": "Property Expense",
            "organization": org_name,
            "property": prop,
            "vendor_name": exp["vendor"],
            "expense_category": exp["category"],
            "amount": exp["amount"],
            "work_description": exp["desc"],
            "status": "Approved",
            "approved_at": exp_date
        }).insert(ignore_permissions=True)
    print("Property expenses seeded successfully.")

    frappe.db.commit()
    print("Database seeding completed successfully! All reports have rich live operational data.")

