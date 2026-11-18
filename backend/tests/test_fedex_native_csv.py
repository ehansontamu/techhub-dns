"""Native FedEx export regression cases use fictional contact data only."""

import csv
import io
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.shipping_contact import ShippingContact
from app.schemas.shipment import ShipmentRecipient
from app.services.shipping_contacts import import_fedex_contacts, parse_fedex_csv, saved_fedex_contacts
from app.services.shipping_destinations import destination_contact, postal_matches
from app.utils.exceptions import ValidationError

# Native export header names, including fields intentionally not imported.
HEADERS = '''Nickname FullName FirstName LastName Title Company Department AddressOne AddressTwo City State Zip
PhoneNumber ExtensionNumber FAXNumber PagerNumber MobilePhoneNumber CountryCode EmailAddress VerifiedFlag
AcceptedFlag ValidFlag ResidentialFlag CustomsIDEIN ReferenceDescription ServiceTypeCode PackageTypeCode
CollectionMethodCode BillCode BillAccountNumber DutyBillCode DutyBillAccountNumber CurrencyTypeCode
InsightIDNumber GroundReferenceDescription ShipmentNotificationRecipientEmail RecipientEmailLanguage
RecipientEmailShipmentnotification RecipientEmailExceptionnotification RecipientEmailDeliverynotification
PartnerTypeCodes NetReturnBillAccountNumber CustomsIDTypeCode AddressTypeCode ShipmentNotificationSenderEmail
SenderEmailLanguage SenderEmailShipmentnotification SenderEmailExceptionnotification SenderEmailDeliverynotification
RecipientEmailPickupnotification SenderEmailPickupnotification OpCoTypeCd BrokerAccounttID BrokerTaxID
DefaultBrokerID RecipientEmailTenderednotification SenderEmailTenderednotification UserAccountNumber
DeliveryInstructions EstimatedDeliveryFlag SenderEstimatedDeliveryFlag ShipmentNotificationSenderDeliveryChannel
ShipmentNotificationSenderMobileNo ShipmentNotificationSenderMobileNoCountry ShipmentNotificationSenderMobileNoLanguage'''.split()


def native_csv(*, extra=None, encoding="utf-8", **changes):
    row = dict(Nickname="Dallas 3302", FullName="Example Recipient", FirstName="Example", LastName="Recipient",
        Company="Example Department", AddressOne="100 Example St", AddressTwo="3302", City="DALLAS", State="TX",
        Zip="752350001", PhoneNumber="2145550100", CountryCode="US", ResidentialFlag="C",
        BillAccountNumber="000000000", ServiceTypeCode="IGNORED_SERVICE")
    row.update(changes)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerow([row.get(header, "") for header in HEADERS] + (extra if extra is not None else [""] * 4))
    return output.getvalue().encode(encoding)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'contacts.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def test_native_export_maps_named_columns_and_allows_empty_trailing_cells():
    assert len(HEADERS) == 65
    contact = parse_fedex_csv(native_csv())[0]
    assert contact["label"] == "Dallas 3302"
    assert contact["recipient"] == {
        "name": "Example Recipient", "company": "Example Department", "phone": "2145550100",
        "address1": "100 Example St", "address2": "3302", "city": "DALLAS", "state": "TX",
        "postal_code": "75235-0001", "country": "US", "residential": False}
    ShipmentRecipient.model_validate(contact["recipient"])
    assert "BillAccountNumber" not in str(contact)
    assert "IGNORED_SERVICE" not in str(contact)


@pytest.mark.parametrize("flag,expected", [("R", True), ("C", False), ("", False), ("Y", True), ("N", False)])
def test_native_residential_flags(flag, expected):
    assert parse_fedex_csv(native_csv(ResidentialFlag=flag))[0]["recipient"]["residential"] is expected


def test_zip_plus_four_formatting_preserves_leading_zeroes():
    recipient = parse_fedex_csv(native_csv(City="Boston", State="MA", Zip="021100001"))[0]["recipient"]
    assert recipient["postal_code"] == "02110-0001"


def test_first_and_last_names_are_used_if_full_name_is_blank():
    recipient = parse_fedex_csv(native_csv(FullName="", FirstName="Test", LastName="Recipient"))[0]["recipient"]
    assert recipient["name"] == "Test Recipient"


def test_native_reimport_is_persistent_and_nickname_is_searchable(db):
    assert import_fedex_contacts(db, native_csv(), "admin") == 1
    original = saved_fedex_contacts(db, "Dallas 3302")[0]
    assert import_fedex_contacts(db, native_csv(PhoneNumber="2145550199", AddressTwo="Suite 3302"), "admin") == 1
    db.expire_all()
    updated = saved_fedex_contacts(db, "Dallas 3302")[0]
    assert db.query(ShippingContact).count() == 1
    assert updated["id"] == original["id"]
    assert updated["recipient"]["phone"] == "2145550199"
    assert updated["recipient"]["address2"] == "Suite 3302"


def test_nonempty_trailing_data_is_rejected_before_database_writes(db):
    import_fedex_contacts(db, native_csv(), "admin")
    before = saved_fedex_contacts(db)
    with pytest.raises(ValidationError, match="too many columns"):
        import_fedex_contacts(db, native_csv(extra=["unmapped-data"]), "admin")
    assert saved_fedex_contacts(db) == before


def test_csv_quotes_and_windows_encoding_are_supported():
    contact = parse_fedex_csv(native_csv(Nickname="Café office", Company="Example, Department", encoding="cp1252"))[0]
    assert contact["label"] == "Café office"
    assert contact["recipient"]["company"] == "Example, Department"


@pytest.mark.parametrize("left,right,expected", [
    ("752350001", "75235-0001", True), ("75235", "75235-0001", True),
    ("75235-0001", "752350002", False), ("75235", "77001", False),
    ("021100001", "02110-0001", True), ("", "75235", True)])
def test_postal_matching_respects_zip_and_optional_extension(left, right, expected):
    assert postal_matches(left, right) is expected


def test_imported_location_label_and_postal_format_match_order():
    contact = parse_fedex_csv(native_csv(AddressTwo=""))[0]
    saved = SimpleNamespace(id=contact["id"], label=contact["label"], recipient=contact["recipient"])
    order = SimpleNamespace(inflow_data={"shippingAddress": {"city": "Dallas", "address1": "100 Example St",
        "address2": "3302", "stateProvince": "TX", "postalCode": "75235"}}, delivery_location="")
    assert destination_contact(order, [saved]).id == saved.id
