"""Import FedEx CSV contacts; no FedEx login scraping or Inflow lookup."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime

from app.models.shipping_contact import ShippingContact
from app.services.audit_service import AuditService
from app.utils.exceptions import ValidationError

MAX_CSV_BYTES = 4 * 1024 * 1024
ALIASES = {
    "name": ("contactname", "recipientname", "fullname", "name"),
    "nickname": ("nickname",),
    "first_name": ("firstname", "givenname"),
    "last_name": ("lastname", "surname"),
    "company": ("companyname", "company", "organization"),
    "phone": ("phonenumber", "phone", "telephone", "telephone1"),
    "address1": ("addressline1", "address1", "addressone", "streetaddress1", "streetaddress", "street"),
    "address2": ("addressline2", "address2", "addresstwo", "streetaddress2", "suite"),
    "city": ("city", "town"),
    "state": ("stateprovince", "stateprovincecode", "state", "province"),
    "postal_code": ("postalcode", "zipostalcode", "zippostalcode", "zipcode", "zip"),
    "country": ("countryterritory", "countrycode", "country"),
    "residential": ("residential", "residentialflag", "residentialaddress", "isresidential", "addresstype"),
    "source_id": ("contactid", "recipientid", "addressid", "nickname"),
}

def parse_fedex_csv(content: bytes) -> list[dict]:
    if len(content) > MAX_CSV_BYTES:
        raise ValidationError("Address-book CSV must be 4 MB or smaller")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValidationError("Save the address-book file as UTF-8 CSV and try again") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames or []
        normalized = [re.sub(r"[^a-z0-9]", "", name.strip().lower()) for name in headers]
        if len(set(normalized)) != len(normalized):
            raise ValidationError("The CSV has duplicate column names")
        mapping = {key: next((headers[normalized.index(alias)] for alias in aliases if alias in normalized), None)
                   for key, aliases in ALIASES.items()}
        missing = [key for key in ("address1", "city", "state", "postal_code", "country") if not mapping[key]]
        if not any(mapping[key] for key in ("name", "company", "first_name", "last_name", "nickname")):
            missing.append("contact name or company")
        if missing:
            raise ValidationError("CSV columns were not recognized: " + ", ".join(missing)
                + ". Use a header row: Contact Name, Company, Phone Number, Address Line 1, Address Line 2, City, State, Postal Code, Country, Residential.")
        contacts = {}
        for row_number, row in enumerate(reader, 2):
            if row_number > 10001:
                raise ValidationError("Import at most 10,000 contacts at once")
            # Native FedEx exports can add unlabelled, empty trailing cells.
            # Non-empty extras still indicate data that cannot be mapped safely.
            extras = row.pop(None, [])
            if any(str(value or "").strip() for value in extras):
                raise ValidationError(f"CSV row {row_number} has too many columns; quote addresses containing commas")
            def value(key):
                return (row.get(mapping[key]) or "").strip() if mapping[key] else ""
            if not any(str(v or "").strip() for v in row.values()):
                continue
            recipient = {key: value(key) for key in ("name", "company", "phone", "address1", "address2", "city", "state", "postal_code", "country")}
            nickname = value("nickname")
            full_name = " ".join(part for part in (value("first_name"), value("last_name")) if part)
            recipient["name"] = recipient["name"] or full_name or recipient["company"] or nickname
            if not recipient["name"] or not recipient["address1"]:
                raise ValidationError(f"CSV row {row_number} needs a contact/company name and street address")
            if any(len(v) > 500 for v in [*recipient.values(), nickname]):
                raise ValidationError(f"CSV row {row_number} has a field longer than 500 characters")
            country = recipient["country"].upper()
            recipient["country"] = "US" if country in {"USA", "US", "UNITED STATES", "UNITED STATES OF AMERICA"} else country
            recipient["state"] = recipient["state"].upper()
            if recipient["country"] == "US" and re.fullmatch(r"\d{9}", recipient["postal_code"]):
                recipient["postal_code"] = recipient["postal_code"][:5] + "-" + recipient["postal_code"][5:]
            recipient["residential"] = value("residential").lower() in {"y", "yes", "true", "1", "residential", "r"}
            identity = value("source_id") or json.dumps({key: recipient[key].casefold() for key in ("name", "company", "address1", "address2", "city", "state", "postal_code", "country")}, sort_keys=True)
            contact_id = hashlib.sha256(identity.encode()).hexdigest()
            label = (nickname or (recipient["name"] + (" — " + recipient["company"] if recipient["company"] and recipient["company"] != recipient["name"] else "")))[:500]
            contacts[contact_id] = {"id": contact_id, "label": label, "recipient": recipient}
    except csv.Error as exc:
        raise ValidationError("The file is not a valid comma-separated CSV") from exc
    if not contacts:
        raise ValidationError("The CSV contains no contacts")
    return list(contacts.values())

def import_fedex_contacts(db, content: bytes, actor: str) -> int:
    contacts = parse_fedex_csv(content)
    for item in contacts:
        record = db.get(ShippingContact, item["id"])
        if record is None:
            record = ShippingContact(id=item["id"])
            db.add(record)
        record.label, record.recipient = item["label"], item["recipient"]
        record.search_text = " ".join([record.label, *(str(v) for v in item["recipient"].values())]).lower()
        record.imported_by, record.imported_at = actor, datetime.utcnow()
    AuditService(db).log_system_action("fedex_contacts_imported", user_id=actor, audit_metadata={"count": len(contacts)})
    db.commit()
    return len(contacts)

def saved_fedex_contacts(db, search: str = "") -> list[dict]:
    query = db.query(ShippingContact)
    if search.strip():
        query = query.filter(ShippingContact.search_text.contains(search.strip().lower()[:200], autoescape=True))
    return [{"id": row.id, "label": row.label, "recipient": row.recipient}
            for row in query.order_by(ShippingContact.label).limit(200).all()]
