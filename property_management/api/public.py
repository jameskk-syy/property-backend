# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Public (unauthenticated) vacancy listings for the Nyumba marketplace frontend.

Lists VACANT units across ALL properties/apartments of every organization,
grouped into "listings" (one card per property + unit-type) with a vacant count.
Supports location / rent / type / amenity filters and page-based pagination for
infinite scroll. No login required (allow_guest), and only non-sensitive fields
are exposed — the exact address and contact phone are returned so the frontend
can mask/reveal them client-side.
"""

import frappe
from frappe.utils import flt, cint


# Map internal unit_type values to the human labels the marketplace UI uses.
_TYPE_LABELS = {
	"Bedsitter": "Bedsitter",
	"Studio": "Studio",
	"1 Bedroom": "One Bedroom",
	"2 Bedroom": "Two Bedroom",
	"3 Bedroom": "Three Bedroom",
	"4 Bedroom": "Four Bedroom",
	"Penthouse": "Penthouse",
	"Servant Quarter": "Servant Quarter",
	"Commercial Shop": "Commercial Shop",
	"Office Space": "Office Space",
}

# Reverse map so the frontend can filter using its own labels.
_LABEL_TO_TYPE = {v: k for k, v in _TYPE_LABELS.items()}


def _split_amenities(text):
	if not text:
		return []
	parts = []
	for chunk in str(text).replace("\n", ",").split(","):
		c = chunk.strip()
		if c:
			parts.append(c)
	return parts


import base64
import mimetypes
import os


def _data_url(file_url):
	"""
	Return the image as a base64 `data:` URL so a cross-origin frontend (Nyumba)
	can render it inline without fetching a separate, unreachable /files URL.

	- An existing data: URL passes through unchanged.
	- A stored /files/... path (public or private) is read from disk and encoded.
	- If the file can't be read, falls back to an absolute URL rather than failing.
	"""
	if not file_url:
		return None
	if file_url.startswith("data:"):
		return file_url

	try:
		# Resolve the File record to read its bytes (works for private files too).
		content = None
		file_name = file_url
		fdoc = frappe.get_all(
			"File", filters={"file_url": file_url},
			fields=["name", "file_name", "is_private"], limit=1,
		)
		if fdoc:
			doc = frappe.get_doc("File", fdoc[0].name)
			content = doc.get_content()
			file_name = fdoc[0].file_name or file_url
		else:
			# No File row: read straight from the files directory on disk.
			from frappe.utils import get_files_path
			rel = file_url.split("/files/")[-1]
			is_private = "/private/" in file_url
			path = os.path.join(get_files_path(is_private=is_private), rel)
			with open(path, "rb") as fh:
				content = fh.read()

		if content is None:
			raise ValueError("no content")

		mime = mimetypes.guess_type(file_name)[0] or "image/jpeg"
		b64 = base64.b64encode(content).decode("ascii")
		return f"data:{mime};base64,{b64}"
	except Exception:
		frappe.log_error(title="vacancy image encode failed", message=frappe.get_traceback())
		# Fallback to an absolute URL so at least the reference is returned.
		if file_url.startswith(("http://", "https://")):
			return file_url
		base = frappe.utils.get_url()
		return f"{base}{file_url}" if file_url.startswith("/") else f"{base}/{file_url}"


def _property_images(prop_name):
	"""All gallery images for a property (cover first), as base64 data URLs."""
	rows = frappe.get_all(
		"Property Image", filters={"parent": prop_name},
		fields=["image", "is_cover"], order_by="idx asc", ignore_permissions=True,
	)
	rows.sort(key=lambda r: 0 if r.get("is_cover") else 1)
	return [_data_url(r["image"]) for r in rows if r.get("image")]


@frappe.whitelist(allow_guest=True)
def list_vacancies(location=None, min_rent=None, max_rent=None, types=None,
				   amenities=None, search=None, page=1, page_size=10):
	"""
	Public vacancy feed. Returns { items, page, page_size, total, has_more }.

	Filters (all optional):
	  - location   : matches Property.address / county / sub_county / property_name (contains)
	  - min_rent / max_rent : unit base_rent range
	  - types      : CSV or JSON list of UI type labels (e.g. "One Bedroom,Studio")
	  - amenities  : CSV or JSON list; a listing must have ALL requested amenities
	  - search     : free text across title/location/address

	Pagination is page-based (1-indexed) over the GROUPED listings, sized for
	infinite scroll (default 10 per page).
	"""
	page = max(1, cint(page) or 1)
	page_size = min(50, max(1, cint(page_size) or 10))

	# Parse list-ish params (accept JSON array or CSV).
	def _as_list(v):
		if not v:
			return []
		if isinstance(v, (list, tuple)):
			return [str(x).strip() for x in v if str(x).strip()]
		s = str(v).strip()
		if s.startswith("["):
			try:
				return [str(x).strip() for x in frappe.parse_json(s) if str(x).strip()]
			except Exception:
				pass
		return [p.strip() for p in s.split(",") if p.strip()]

	type_labels = _as_list(types)
	# Translate UI labels -> internal unit_type values for the query.
	wanted_types = [_LABEL_TO_TYPE.get(t, t) for t in type_labels]
	wanted_amenities = _as_list(amenities)

	# --- Vacant units, joined to their property, across ALL organizations. ---
	# Use a list-of-conditions filter (robust; avoids dict/between edge cases).
	unit_filters = [["Property Unit", "status", "=", "Vacant"]]
	if wanted_types:
		unit_filters.append(["Property Unit", "unit_type", "in", wanted_types])
	if min_rent not in (None, ""):
		unit_filters.append(["Property Unit", "base_rent", ">=", flt(min_rent)])
	if max_rent not in (None, ""):
		unit_filters.append(["Property Unit", "base_rent", "<=", flt(max_rent)])

	units = frappe.get_all(
		"Property Unit", filters=unit_filters,
		fields=["name", "property", "unit_type", "base_rent"],
		order_by="creation desc",
		ignore_permissions=True,
	)
	if not units:
		return {"items": [], "page": page, "page_size": page_size, "total": 0, "has_more": False}

	# Pull the parent properties once.
	prop_names = list({u.property for u in units if u.property})
	props = {
		p.name: p for p in frappe.get_all(
			"Property", filters={"name": ["in", prop_names]},
			fields=["name", "property_name", "address", "county", "sub_county",
					"description", "amenities", "caretaker", "cover_image", "status"],
			ignore_permissions=True,
		)
	}

	# Group vacant units by (property, unit_type) -> one listing card each.
	groups = {}
	for u in units:
		prop = props.get(u.property)
		if not prop:
			continue
		# Only list properties that are live/active.
		if prop.status and prop.status not in ("Active", "Occupied", None, ""):
			continue
		key = (u.property, u.unit_type)
		g = groups.setdefault(key, {"property": prop, "unit_type": u.unit_type,
								   "vacant": 0, "rent": flt(u.base_rent)})
		g["vacant"] += 1
		# Show the lowest vacant rent for the group.
		if flt(u.base_rent) and flt(u.base_rent) < g["rent"]:
			g["rent"] = flt(u.base_rent)

	# Total units per (property, unit_type), for the "x of y vacant" display.
	totals = {}
	for row in frappe.get_all(
		"Property Unit", filters={"property": ["in", prop_names]},
		fields=["property", "unit_type", "count(name) as n"],
		group_by="property, unit_type", ignore_permissions=True,
	):
		totals[(row["property"], row["unit_type"])] = row["n"]

	# Gallery images per property, fetched once and reused across unit-type groups.
	images_by_prop = {p: _property_images(p) for p in {k[0] for k in groups}}

	# Caretaker contacts (name + phone) resolved in one pass.
	caretaker_ids = list({g["property"].caretaker for g in groups.values() if g["property"].caretaker})
	caretakers = {}
	if caretaker_ids:
		for c in frappe.get_all("Caretaker", filters={"name": ["in", caretaker_ids]},
								fields=["name", "caretaker_name", "phone"], ignore_permissions=True):
			caretakers[c.name] = c

	# Build listing objects in the shape the marketplace expects.
	items = []
	for (prop_name, unit_type), g in groups.items():
		prop = g["property"]
		amen = _split_amenities(prop.amenities)
		# Amenity filter: listing must include ALL requested amenities.
		if wanted_amenities:
			low = {a.lower() for a in amen}
			if not all(a.lower() in low for a in wanted_amenities):
				continue

		location_label = prop.sub_county or prop.county or (prop.address or "").split(",")[0].strip() or prop.property_name
		title = prop.property_name or prop_name

		# Free-text search across title/location/address.
		if search:
			s = str(search).lower()
			hay = f"{title} {location_label} {prop.address or ''}".lower()
			if s not in hay:
				continue

		c = caretakers.get(prop.caretaker)
		contact = {
			"name": (c.caretaker_name if c else None) or "Nyumba Agent",
			"phone": (c.phone if c else None) or "",
		}
		gallery = images_by_prop.get(prop_name, [])
		cover = _data_url(prop.cover_image) or (gallery[0] if gallery else None)
		items.append({
			"id": f"{prop_name}::{unit_type}",
			"title": title,
			"type": _TYPE_LABELS.get(unit_type, unit_type),
			"location": location_label,
			"address": prop.address or "",
			"rent": g["rent"],
			"vacant": g["vacant"],
			"total": totals.get((prop_name, unit_type), g["vacant"]),
			"amenities": amen,
			"contact": contact,
			"desc": prop.description or "",
			"cover_image": cover,
		})

	# Stable ordering: most vacant first, then title.
	items.sort(key=lambda x: (-x["vacant"], x["title"]))

	total = len(items)
	start = (page - 1) * page_size
	end = start + page_size
	page_items = items[start:end]

	return {
		"items": page_items,
		"page": page,
		"page_size": page_size,
		"total": total,
		"has_more": end < total,
	}


@frappe.whitelist(allow_guest=True)
def vacancy_filters():
	"""
	Public helper: the distinct locations, the type labels, and amenity options
	present across currently-vacant listings, so the frontend can populate its
	filter controls from real data.
	"""
	units = frappe.get_all(
		"Property Unit", filters={"status": "Vacant"},
		fields=["property", "unit_type"], ignore_permissions=True,
	)
	prop_names = list({u.property for u in units if u.property})
	types = sorted({_TYPE_LABELS.get(u.unit_type, u.unit_type) for u in units})

	locations, amenities = set(), set()
	if prop_names:
		for p in frappe.get_all("Property", filters={"name": ["in", prop_names]},
								fields=["address", "county", "sub_county", "amenities"],
								ignore_permissions=True):
			loc = p.sub_county or p.county or (p.address or "").split(",")[0].strip()
			if loc:
				locations.add(loc)
			for a in _split_amenities(p.amenities):
				amenities.add(a)

	return {
		"locations": ["All areas"] + sorted(locations),
		"types": types,
		"amenities": sorted(amenities),
	}



@frappe.whitelist(allow_guest=True)
def get_vacancy_details(vacancy_id):
	"""
	Get full details for a specific vacancy listing including all gallery images.
	
	Args:
		vacancy_id: The listing ID in format "property_name::unit_type"
	
	Returns full listing details including:
		- All property images (gallery)
		- Property details
		- Available units of this type
		- Contact information
	"""
	if not vacancy_id:
		frappe.throw("vacancy_id is required")
	
	# Parse the vacancy_id (format: "property_name::unit_type")
	parts = vacancy_id.split("::")
	if len(parts) != 2:
		frappe.throw("Invalid vacancy_id format. Expected 'property_name::unit_type'")
	
	prop_name, unit_type = parts[0], parts[1]
	
	# Get the property
	if not frappe.db.exists("Property", prop_name):
		frappe.throw("Property not found")
	
	prop = frappe.get_doc("Property", prop_name)
	
	# Get vacant units of this type
	vacant_units = frappe.get_all(
		"Property Unit",
		filters={
			"property": prop_name,
			"unit_type": unit_type,
			"status": "Vacant"
		},
		fields=["name", "unit_number", "unit_type", "base_rent", "floor_number", 
				"bedrooms", "bathrooms", "square_footage", "description"],
		order_by="unit_number asc",
		ignore_permissions=True
	)
	
	if not vacant_units:
		frappe.throw("No vacant units found for this listing")
	
	# Get total units of this type
	total_units = frappe.db.count("Property Unit", {
		"property": prop_name,
		"unit_type": unit_type
	})
	
	# Get all gallery images
	gallery = _property_images(prop_name)
	cover = _data_url(prop.cover_image) or (gallery[0] if gallery else None)
	
	# Get caretaker contact
	contact = {"name": "Nyumba Agent", "phone": ""}
	if prop.caretaker:
		caretaker = frappe.db.get_value(
			"Caretaker", prop.caretaker, 
			["caretaker_name", "phone"], as_dict=True
		)
		if caretaker:
			contact = {
				"name": caretaker.caretaker_name or "Nyumba Agent",
				"phone": caretaker.phone or ""
			}
	
	# Parse amenities
	amenities = _split_amenities(prop.amenities)
	
	# Location label
	location_label = prop.sub_county or prop.county or (prop.address or "").split(",")[0].strip() or prop.property_name
	
	# Get the lowest rent among vacant units
	min_rent = min([flt(u.base_rent) for u in vacant_units if u.base_rent], default=0)
	
	return {
		"id": vacancy_id,
		"title": prop.property_name or prop_name,
		"type": _TYPE_LABELS.get(unit_type, unit_type),
		"location": location_label,
		"address": prop.address or "",
		"county": prop.county or "",
		"sub_county": prop.sub_county or "",
		"rent": min_rent,
		"vacant": len(vacant_units),
		"total": total_units,
		"amenities": amenities,
		"contact": contact,
		"desc": prop.description or "",
		"cover_image": cover,
		"images": gallery,
		"property_details": {
			"year_built": prop.year_built,
			"total_units": prop.total_units,
			"floors": prop.floors,
		},
		"units": [
			{
				"id": u.name,
				"unit_number": u.unit_number,
				"type": _TYPE_LABELS.get(u.unit_type, u.unit_type),
				"rent": flt(u.base_rent),
				"floor": u.floor_number,
				"bedrooms": u.bedrooms,
				"bathrooms": u.bathrooms,
				"size": u.square_footage,
				"description": u.description or "",
			}
			for u in vacant_units
		]
	}
