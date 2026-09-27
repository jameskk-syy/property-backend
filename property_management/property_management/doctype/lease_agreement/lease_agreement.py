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
	 "Landlord/Property Manager: Dadis Estates Limited\n\nNote: Copy of National ID or Passport to be provided."),
	("2. TENANCY TERM",
	 "a. The tenancy shall commence on the Commencement Date stated above and shall continue on a month-to-month basis unless a fixed term is expressly stated in writing below.\n\nb. If a fixed term applies, the term shall be as specified in the lease details above.\n\nc. Any renewal, change of rent, change of premises or material variation to this Agreement should be recorded in writing and acknowledged by both parties."),
	("3. RENT, PAYMENT AND ARREARS",
	 "a. The Tenant shall pay the full monthly rent on or before the 5th day of each month.\n\nb. Rent shall be paid only through the authorized payment channels set out in Clause 18. The Tenant should retain proof of payment and, where requested, forward the payment reference/slip to the caretaker or management.\n\nc. A late-payment charge of Kshs. 500 shall apply where rent remains unpaid after the 5th day. Where rent remains unpaid after the 10th day, a further/default charge of Kshs. 1,000 may apply, provided that such charges are lawful and are not applied contrary to applicable law.\n\nd. Persistent or material rent arrears constitute a breach of this Agreement. The Landlord may issue the appropriate demand, notice and/or commence lawful recovery or termination proceedings available under applicable law.\n\ne. Any payment received shall be properly recorded by management. The Tenant should promptly report any discrepancy in the rent statement."),
	("4. SECURITY DEPOSIT",
	 "a. The Tenant shall pay the security deposit stated in the schedule below before taking possession, unless otherwise agreed in writing.\n\nb. The deposit is security for the Tenant's obligations and is not rent. The Tenant may not use the deposit as the last month's rent without the Landlord's written consent.\n\nc. At the end of the tenancy, the Landlord may deduct from the deposit amounts properly due under this Agreement, including unpaid rent/utilities, missing items, paint and any cost of repairs.\n\nd. The Landlord shall provide a reasonable statement of deductions where deductions are made. Any undisputed balance shall be refunded within a reasonable period after vacant possession, inspection and reconciliation of outstanding bills."),
	("5. CONDITION, INVENTORY AND HANDOVER",
	 "a. The Tenant confirms that the premises have been inspected before occupation and are accepted in their recorded condition, subject to defects reported to management in writing.\n\nb. Where an inventory or move-in inspection form is provided, it forms part of this Agreement.\n\nc. The Tenant shall notify management promptly of leaks, electrical faults, broken fittings, structural defects or other matters requiring attention. Costs related to the repairs occurring during the tenancy shall be borne by the tenant.\n\nd. The Tenant shall return the premises, keys, access devices and Landlord's fixtures in substantially the same condition as received."),
	("6. CARE OF PREMISES AND FIXTURES",
	 "a. The Tenant shall take reasonable care of the premises and all fixtures, fittings and equipment provided by the Landlord, including switches, sockets, meter boxes, water heaters, bulb holders, sinks, shelves, doors, locks, windows, sanitary fittings and painted surfaces.\n\nb. The Tenant shall not remove, tamper with, bypass, overload, alter or damage electrical, water, security or metering installations.\n\nc. Damage caused by the Tenant, occupants or visitors shall be repaired at the Tenant's reasonable cost, subject to appropriate evidence and/or assessment."),
	("7. CLEANLINESS, HYGIENE AND WASTE",
	 "a. The Tenant shall keep the premises and shared areas reasonably clean and hygienic and shall use sanitation facilities responsibly.\n\nb. The Tenant shall comply with the property's waste-collection arrangements and pay any agreed garbage/waste fee, or a revised lawful charge communicated in advance.\n\nc. Waste shall be placed only in designated collection areas and shall not be dumped in corridors, drains, stairways or other prohibited areas."),
	("8. NOISE, NUISANCE AND CONDUCT",
	 "a. The Tenant shall not cause excessive noise, disturbance, harassment, threats or nuisance to other occupants, neighbours, staff or visitors.\n\nb. Loud music, television, radios, speakers or other activities shall be kept at a reasonable level, particularly during designated quiet hours.\n\nc. The Tenant shall comply with reasonable security, gate and common-area rules issued by management from time to time."),
	("9. USE, OCCUPANTS AND SUBLETTING",
	 "a. The premises shall be used solely as a private residence unless written permission is given for another lawful use.\n\nb. The Tenant shall not use the premises for an illegal activity, hazardous activity, commercial activity that causes nuisance, or any activity that exposes the property to unreasonable risk.\n\nc. Only the Tenant and approved occupants may reside in the premises. The Tenant shall provide management with material changes in occupancy where reasonably required for security and property records.\n\nd. The Tenant shall not assign, sublet, license or otherwise give possession of the premises to another person without the Landlord's prior written consent, except to the extent permitted by applicable law."),
	("10. ALTERATIONS AND INSTALLATIONS",
	 "a. The Tenant shall not drill, construct, repaint, install permanent fixtures, satellite equipment, additional electrical appliances, partitions or other alterations without prior written approval where approval is reasonably required.\n\nb. Any approved work shall comply with applicable safety requirements and shall be carried out at the Tenant's cost unless otherwise agreed in writing."),
	("11. ELECTRICITY, WATER AND OTHER UTILITIES",
	 "a. The Tenant shall pay electricity charges attributable to the premises promptly and before the applicable due date.\n\nb. The Tenant shall pay any separately metered water or other utility charges attributable to the premises as communicated by management.\n\nc. The Tenant shall not tamper with utility meters, wiring, pipes or connections. Any suspected fault or irregularity shall be reported immediately.\n\nd. Utility deposits, where applicable, shall be recorded in the schedule below and reconciled in accordance with the actual account and applicable law."),
	("12. ACCESS, INSPECTION AND REPAIRS",
	 "a. The Landlord, manager, caretaker or authorized contractor may enter the premises at a reasonable time, upon reasonable notice where practicable, for inspection, repairs, maintenance, valuation or other legitimate property-management purposes.\n\nb. In an emergency, including fire, flooding, serious electrical danger, security risk or suspected major damage, management may enter without prior notice where reasonably necessary to protect persons or property.\n\nc. The Tenant shall provide reasonable access for essential repairs and maintenance."),
	("13. SECURITY AND GATE RULES",
	 "a. The Tenant shall comply with reasonable gate, visitor, parking and access-control procedures communicated by management.\n\nb. The Tenant shall not duplicate or transfer keys, access cards or other security devices without authorization.\n\nc. The Tenant remains responsible for the conduct of invited visitors while on the premises."),
	("14. REPAIRS AND RESPONSIBILITIES",
	 "a. The Landlord shall be responsible for major structural repairs and other repairs that are the Landlord's responsibility under applicable law, except where damage results from the Tenant's negligence, misuse or breach.\n\nb. The Tenant shall be responsible for minor damage, cleaning and replacement caused by the Tenant's misuse, negligence or failure to take reasonable care.\n\nc. The Tenant shall not engage an external contractor for material repairs at the Landlord's cost without prior approval, except where urgent action is reasonably necessary to prevent immediate serious damage and management cannot reasonably be reached."),
	("15. DEFAULT AND REMEDIES",
	 "a. A breach includes non-payment of rent, unauthorized subletting, serious nuisance, unlawful use, deliberate damage, utility tampering or material breach of the property rules.\n\nb. Where a breach is capable of remedy, management may give the Tenant written notice requiring the breach to be remedied within a reasonable or legally prescribed period.\n\nc. If the breach is not remedied, or where the breach is sufficiently serious to justify termination under applicable law, the Landlord may take the lawful steps available to recover possession, arrears, damages or other sums due.\n\nd. Nothing in this Agreement authorizes either party to act contrary to mandatory requirements of Kenyan law."),
	("16. TERMINATION AND VACATING",
	 "Either party may terminate a month-to-month tenancy by giving one clear month's written notice, or such other notice as may be required by applicable law or expressly agreed for the tenancy.\n\nNotice should state the intended termination/vacating date and be delivered through writing.\n\nThe Tenant shall pay all rent and other lawful charges up to the date of vacant possession and shall return all keys/access devices.\n\nThe Tenant shall remove personal belongings and leave the premises clean and reasonably fit for handover.\n\nAny lawful loss or cost arising from inadequate notice may be recovered from the deposit."),
	("17. DISPUTE RESOLUTION AND GOVERNING LAW",
	 "a. The parties shall first attempt in good faith to resolve any dispute through written communication and discussion with the property manager.\n\nb. If the dispute is not resolved, either party may refer the matter to the appropriate court, tribunal, authority or other lawful dispute-resolution mechanism with jurisdiction.\n\nc. This Agreement shall be interpreted subject to the laws of Kenya, including any mandatory protections applicable to the particular tenancy."),
	("18. AUTHORIZED PAYMENT DETAILS",
	 "Account Name: DADIS ESTATES LIMITED\nCo-operative Bank Account: 01192274991800\nBusiness No.: 400200\nAccount No. / Paybill Reference: 40045557\nBank / Branch: Co-operative Bank of Kenya / Stima Plaza\n\nIMPORTANT: Tenants should make payments only through authorised channels and retain the transaction reference. Management may request the payment reference and house/room number to reconcile the account."),
	("19. TENANCY FINANCIAL SCHEDULE",
	 "Monthly Rent, Security/House Deposit, Water Deposit (if applicable), Electricity Deposit (if applicable), Garbage/Waste Fee per month and any Other Agreed Charge — all amounts in Kshs. as specified in the lease details above."),
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

		def format_clause_body(body):
			"""Format clause body with blue points (a. b. c. etc) on separate lines"""
			import re
			lines = body.split('\n')
			result = []
			for line in lines:
				if not line.strip():
					result.append('<br/>')
					continue
				# Check for point pattern (a. b. c. etc)
				match = re.match(r'^([a-z])\.\s*(.*)$', line, re.IGNORECASE)
				if match:
					result.append(f'<span style="color:#2563eb;font-weight:600;">{match.group(1)}.</span> {esc(match.group(2))}<br/>')
				else:
					result.append(f'{esc(line)}<br/>')
			return ''.join(result)

		clauses_html = "".join(
			f'<div class="clause"><h4>{esc(title)}</h4><p>{format_clause_body(body)}</p></div>'
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
			premises identified below. It is intended to promote clear responsibilities, proper property management, peaceful occupation
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
