# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

from frappe.utils import flt, get_datetime

import frappe
from frappe.model.document import Document


# Full tenancy agreement clauses — kept identical to the onboarding UI
# (nest/src/components/patterns/LeaseAgreementDialog.jsx CLAUSES). If the UI text
# changes, update it here too so the generated PDF stays in lockstep.
LEASE_CLAUSES = [
	("1. PARTIES AND PREMISES",
	 "Landlord/Property Manager: Dadis Estates Limited. This section records the Tenant's full name, National ID/Passport number, telephone/contact, property/house name, house/room number, commencement date and monthly rent. A copy of the National ID or Passport is to be provided."),
	("2. TENANCY TERM",
	 "a. The tenancy commences on the Commencement Date stated and continues month-to-month unless a fixed term is expressly stated in writing. b. If a fixed term applies, the term runs from the agreed start to the agreed end date. c. Any renewal, change of rent, change of premises or material variation should be recorded in writing and acknowledged by both parties."),
	("3. RENT, PAYMENT AND ARREARS",
	 "a. The Tenant shall pay the full monthly rent on or before the 5th day of each month. b. Rent shall be paid only through the authorized payment channels in Clause 18, retaining proof of payment. c. A late-payment charge of Kshs. 500 applies after the 5th day; a further charge of Kshs. 1,000 may apply after the 10th day, where lawful. d. Persistent or material arrears constitute a breach and the Landlord may pursue lawful recovery or termination. e. Payments shall be properly recorded; the Tenant should report any discrepancy."),
	("4. SECURITY DEPOSIT",
	 "a. The Tenant shall pay the security deposit before taking possession unless otherwise agreed in writing. b. The deposit is security for obligations and is not rent, and may not be used as last month's rent without written consent. c. At the end of the tenancy the Landlord may deduct amounts properly due, including unpaid rent/utilities, missing items and repair costs. d. A reasonable statement of deductions shall be provided, with any undisputed balance refunded within a reasonable period after inspection and reconciliation."),
	("5. CONDITION, INVENTORY AND HANDOVER",
	 "a. The Tenant confirms the premises were inspected and accepted in their recorded condition, subject to reported defects. b. Any inventory or move-in inspection form forms part of this Agreement. c. The Tenant shall promptly notify management of leaks, electrical faults, broken fittings or defects; repair costs during the tenancy are borne by the Tenant. d. The Tenant shall return the premises, keys, access devices and fixtures in substantially the same condition as received."),
	("6. CARE OF PREMISES AND FIXTURES",
	 "a. The Tenant shall take reasonable care of the premises and all fixtures, fittings and equipment provided. b. The Tenant shall not remove, tamper with, bypass, overload, alter or damage electrical, water, security or metering installations. c. Damage caused by the Tenant, occupants or visitors shall be repaired at the Tenant's reasonable cost, subject to evidence and/or assessment."),
	("7. CLEANLINESS, HYGIENE AND WASTE",
	 "a. The Tenant shall keep the premises and shared areas reasonably clean and hygienic. b. The Tenant shall comply with the property's waste-collection arrangements and pay any agreed garbage/waste fee, or a revised lawful charge communicated in advance. c. Waste shall be placed only in designated collection areas."),
	("8. NOISE, NUISANCE AND CONDUCT",
	 "a. The Tenant shall not cause excessive noise, disturbance, harassment, threats or nuisance. b. Loud music, television, radios or speakers shall be kept at a reasonable level, particularly during quiet hours. c. The Tenant shall comply with reasonable security, gate and common-area rules."),
	("9. USE, OCCUPANTS AND SUBLETTING",
	 "a. The premises shall be used solely as a private residence unless written permission is given. b. The Tenant shall not use the premises for illegal, hazardous or nuisance-causing activity. c. Only the Tenant and approved occupants may reside there, with material changes reported where required. d. The Tenant shall not assign, sublet or license the premises without the Landlord's prior written consent, except as permitted by law."),
	("10. ALTERATIONS AND INSTALLATIONS",
	 "a. The Tenant shall not drill, construct, repaint, install permanent fixtures, satellite equipment, additional appliances or partitions without prior written approval where required. b. Any approved work shall comply with safety requirements and be carried out at the Tenant's cost unless otherwise agreed."),
	("11. ELECTRICITY, WATER AND OTHER UTILITIES",
	 "a. The Tenant shall pay electricity charges attributable to the premises before the due date. b. The Tenant shall pay any separately metered water or utility charges as communicated. c. The Tenant shall not tamper with meters, wiring, pipes or connections and shall report faults immediately. d. Utility deposits, where applicable, are recorded in the schedule and reconciled per the actual account and law."),
	("12. ACCESS, INSPECTION AND REPAIRS",
	 "a. The Landlord, manager, caretaker or authorized contractor may enter at a reasonable time, on reasonable notice where practicable, for inspection, repairs, maintenance, valuation or other legitimate purposes. b. In an emergency, entry may occur without prior notice where reasonably necessary to protect persons or property. c. The Tenant shall provide reasonable access for essential repairs."),
	("13. SECURITY AND GATE RULES",
	 "a. The Tenant shall comply with reasonable gate, visitor, parking and access-control procedures. b. The Tenant shall not duplicate or transfer keys, access cards or security devices without authorization. c. The Tenant remains responsible for the conduct of invited visitors."),
	("14. REPAIRS AND RESPONSIBILITIES",
	 "a. The Landlord is responsible for major structural repairs and repairs that are the Landlord's responsibility under law, except where damage results from the Tenant's negligence, misuse or breach. b. The Tenant is responsible for minor damage, cleaning and replacement caused by misuse or negligence. c. The Tenant shall not engage an external contractor for material repairs at the Landlord's cost without prior approval, except in urgent cases to prevent immediate serious damage."),
	("15. DEFAULT AND REMEDIES",
	 "a. A breach includes non-payment of rent, unauthorized subletting, serious nuisance, unlawful use, deliberate damage, utility tampering or material breach of property rules. b. Where a breach is capable of remedy, management may give written notice requiring the breach to be remedied within a reasonable or legally prescribed period. c. If not remedied, or where serious enough to justify termination, the Landlord may take lawful steps to recover possession, arrears or damages. d. Nothing authorizes either party to act contrary to mandatory Kenyan law."),
	("16. TERMINATION AND VACATING",
	 "Either party may terminate a month-to-month tenancy by giving one clear month's written notice, or as required by law. Notice should state the intended vacating date and be delivered in writing. The Tenant shall pay all rent and lawful charges up to vacant possession and return all keys/access devices, remove personal belongings and leave the premises clean. Any lawful loss from inadequate notice may be recovered from the deposit."),
	("17. DISPUTE RESOLUTION AND GOVERNING LAW",
	 "a. The parties shall first attempt in good faith to resolve any dispute through written communication with the property manager. b. If unresolved, either party may refer the matter to the appropriate court, tribunal or lawful dispute-resolution mechanism. c. This Agreement is interpreted subject to the laws of Kenya, including mandatory protections applicable to the tenancy."),
	("18. AUTHORIZED PAYMENT DETAILS",
	 "Account Name: DADIS ESTATES LIMITED. Co-operative Bank Account: 01192274991800. Business No.: 400200. Account No./Paybill Reference: 40045557. Bank/Branch: Co-operative Bank of Kenya / Stima Plaza. Tenants should pay only through authorised channels and retain the transaction reference. Management may request the reference and house/room number to reconcile the account."),
	("19. TENANCY FINANCIAL SCHEDULE",
	 "Records Monthly Rent, Security/House Deposit, Water Deposit (if applicable), Electricity Deposit (if applicable), Garbage/Waste Fee per month and any Other Agreed Charge, all in Kshs."),
]


class LeaseAgreement(Document):
	def validate(self):
		if self.end_date and self.start_date and self.end_date <= self.start_date:
			frappe.throw("End date must be after start date.")

	def on_update(self):
		if self.unit:
			unit_doc = frappe.get_doc("Property Unit", self.unit)
			if self.status == "Active":
				unit_doc.status = "Occupied"
			elif self.status in ["Terminated", "Expired"]:
				unit_doc.status = "Vacant"
			unit_doc.save(ignore_permissions=True)

	def generate_agreement_pdf(self):
		"""
		Render the FULL tenancy agreement to a PDF and attach it to this Lease
		Agreement — the same content shown in the onboarding UI (Annex A): the
		tenant & premises details block, all 20 clauses, the acknowledgement, and
		the two embedded digital signatures with dates. Returns the file URL.
		Re-generating replaces the stored PDF reference.
		"""
		from frappe.utils.pdf import get_pdf
		from frappe.utils import getdate, nowdate

		tenant = frappe.db.get_value(
			"Property Tenant", self.tenant,
			["tenant_name", "national_id", "phone", "email", "income_range"], as_dict=True,
		) or frappe._dict()
		tenant_name = tenant.get("tenant_name") or self.tenant
		property_name = frappe.db.get_value("Property", self.property, "property_name") or self.property
		unit_number = frappe.db.get_value("Property Unit", self.unit, "unit_number") or self.unit
		org_name = self.organization or "Dadis Estates Limited"

		def esc(v):
			if v in (None, ""):
				return "—"
			return (
				str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
			)

		def _sig(data_url):
			if data_url and str(data_url).startswith("data:image"):
				return f'<img src="{data_url}" style="max-height:70px;" />'
			return '<div style="height:70px;"></div>'

		signed_display = str(self.signed_on)[:16].replace("T", " ") if self.signed_on else "Not yet signed"
		sig_date = (str(self.signed_on)[:10] if self.signed_on else nowdate())

		# The 20 clauses — kept in lockstep with the onboarding UI (LeaseAgreementDialog).
		clauses = LEASE_CLAUSES

		detail_rows = [
			("Landlord / Property Manager", org_name),
			("Tenant Full Name", tenant_name),
			("National ID / Passport No.", tenant.get("national_id")),
			("Telephone / Contact", tenant.get("phone")),
			("Email", tenant.get("email")),
			("Property / House Name", property_name),
			("House / Room No.", unit_number),
			("Income Range", tenant.get("income_range")),
			("Commencement Date", self.start_date),
			("Lease End Date", self.end_date),
			("Monthly Rent (Kshs.)", f"{flt(self.rent_amount):,.2f}"),
			("Security Deposit (Kshs., refundable)", f"{flt(self.deposit_amount):,.2f}"),
		]
		details_html = "".join(
			f'<tr><td class="k">{esc(label)}</td><td>{esc(value)}</td></tr>'
			for label, value in detail_rows
		)

		clauses_html = "".join(
			f'<div class="clause"><h4>{esc(title)}</h4><p>{esc(body)}</p></div>'
			for title, body in clauses
		)

		html = f"""
		<html><head><style>
			body {{ font-family: Helvetica, Arial, sans-serif; color:#1e293b; padding:28px; font-size:12px; line-height:1.5; }}
			h1 {{ font-size:20px; margin-bottom:2px; }}
			h2 {{ font-size:12px; color:#475569; font-weight:normal; margin-top:0; }}
			.box {{ border-bottom:2px solid #0f172a; padding-bottom:10px; margin-bottom:16px; }}
			.intro {{ font-size:12px; color:#475569; margin-bottom:14px; }}
			.section-title {{ font-size:15px; font-weight:bold; color:#0f172a; margin:6px 0 4px; }}
			.details {{ width:100%; border-collapse:collapse; margin:6px 0 18px; background:#f8fafc; }}
			.details td {{ padding:6px 10px; border-bottom:1px solid #e2e8f0; font-size:12px; }}
			.details .k {{ color:#64748b; width:45%; }}
			.clause {{ margin-bottom:10px; }}
			.clause h4 {{ font-size:12.5px; font-weight:bold; color:#0f172a; margin:0 0 2px; }}
			.clause p {{ margin:0; color:#334155; }}
			.ack {{ margin-top:14px; padding-top:10px; border-top:1px solid #e2e8f0; }}
			.sigtable {{ width:100%; margin-top:36px; border-collapse:collapse; }}
			.sigtable td {{ width:50%; vertical-align:bottom; padding:0 12px; }}
			.sigline {{ border-bottom:1px solid #333; }}
			.cap {{ font-size:11px; color:#475569; margin-top:6px; }}
			.footer {{ margin-top:26px; font-size:10px; color:#94a3b8; border-top:1px solid #e2e8f0; padding-top:8px; }}
		</style></head><body>
			<div class="box">
				<h1>{esc(org_name)}</h1>
				<h2>Tenancy Agreement — Annex A &bull; {esc(property_name)} &bull; Unit {esc(unit_number)}</h2>
			</div>

			<div class="section-title">ANNEX A: TENANCY AGREEMENT</div>
			<p class="intro">This Agreement sets out the terms governing the letting and occupation of the residential
			premises identified below, promoting clear responsibilities, proper property management, peaceful occupation
			and transparent handling of rent, deposits, utilities, repairs and termination.</p>

			<div class="section-title">Tenant &amp; Premises Details</div>
			<table class="details">{details_html}</table>

			{clauses_html}

			<div class="ack">
				<h4 style="font-size:12.5px;font-weight:bold;color:#0f172a;margin:0 0 2px;">20. ACKNOWLEDGEMENT</h4>
				<p style="margin:0;color:#334155;">By signing below, the parties confirm that they have read and understood this Agreement,
				that the information supplied is accurate, and that they agree to comply with its terms subject to applicable law.
				The Tenant acknowledges receipt of a copy of the Agreement.</p>
			</div>

			<table class="sigtable">
				<tr>
					<td>{_sig(self.tenant_signature)}<div class="sigline"></div><div class="cap">Tenant: {esc(tenant_name)}<br/>Date: {esc(sig_date)}</div></td>
					<td>{_sig(self.caretaker_signature)}<div class="sigline"></div><div class="cap">Witness / Caretaker — For {esc(org_name)}<br/>Date: {esc(sig_date)}</div></td>
				</tr>
			</table>

			<div class="footer">Signed on: {esc(signed_display)} &bull; Generated by Nest Property Management System for {esc(org_name)}.</div>
		</body></html>
		"""

		pdf_bytes = get_pdf(html)
		fname = f"Lease-{self.name}.pdf"
		# Remove a previous generated file if present.
		if self.agreement_pdf:
			old = frappe.db.get_value("File", {"file_url": self.agreement_pdf}, "name")
			if old:
				frappe.delete_doc("File", old, ignore_permissions=True, force=True)

		filedoc = frappe.get_doc({
			"doctype": "File",
			"file_name": fname,
			"attached_to_doctype": "Lease Agreement",
			"attached_to_name": self.name,
			"is_private": 1,
			"content": pdf_bytes,
		})
		filedoc.insert(ignore_permissions=True)
		self.db_set("agreement_pdf", filedoc.file_url, update_modified=False)
		return filedoc.file_url
