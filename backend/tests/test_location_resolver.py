#!/usr/bin/env python3
"""Tests for LocationResolverService (extracted from OrderService)"""

import sys
sys.path.append('.')


# Sample Inflow order data for testing
SAMPLE_LOCAL_ORDER = {
    "orderNumber": "TEST1234",
    "orderRemarks": "Please deliver to LAAH 424",
    "shippingAddress": {
        "address1": "123 Main St",
        "address2": "Room 100",
        "city": "College Station",
        "state": "TX",
        "postalCode": "77843"
    }
}

SAMPLE_SHIPPING_ORDER = {
    "orderNumber": "TEST5678",
    "orderRemarks": "",
    "shippingAddress": {
        "address1": "456 Oak Ave",
        "city": "Houston",
        "state": "TX",
        "postalCode": "77001"
    }
}

SAMPLE_NO_CITY_ORDER = {
    "orderNumber": "TEST9999",
    "orderRemarks": "",
    "shippingAddress": {
        "address1": "789 Elm St",
        "city": "",  # No city
    }
}


def test_location_resolver_import():
    """Test that LocationResolverService can be imported"""
    from app.services.location_resolver_service import LocationResolverService, ResolvedLocation
    assert LocationResolverService is not None
    assert ResolvedLocation is not None
    print("[PASS] LocationResolverService import test passed")


def test_resolved_location_dataclass():
    """Test ResolvedLocation dataclass"""
    from app.services.location_resolver_service import ResolvedLocation

    resolved = ResolvedLocation(
        building_code="LAAH",
        display_location="LAAH 424",
        source="remarks",
        is_local_delivery=True
    )

    assert resolved.building_code == "LAAH"
    assert resolved.display_location == "LAAH 424"
    assert resolved.source == "remarks"
    assert resolved.is_local_delivery == True
    print("[PASS] ResolvedLocation dataclass test passed")


def test_local_delivery_detection():
    """Test that College Station orders are detected as local"""
    from app.services.location_resolver_service import LocationResolverService

    service = LocationResolverService()
    resolved = service.resolve_location(SAMPLE_LOCAL_ORDER)

    assert resolved.is_local_delivery == True, f"Expected local delivery, got is_local_delivery={resolved.is_local_delivery}"
    print("[PASS] Local delivery detection test passed")


def test_shipping_order_detection():
    """Test that Houston orders are detected as shipping"""
    from app.services.location_resolver_service import LocationResolverService

    service = LocationResolverService()
    resolved = service.resolve_location(SAMPLE_SHIPPING_ORDER)

    assert resolved.is_local_delivery == False, f"Expected shipping order, got is_local_delivery={resolved.is_local_delivery}"
    assert resolved.display_location == "Houston"
    print("[PASS] Shipping order detection test passed")


def test_extract_location_from_remarks():
    """Test _extract_delivery_location_from_remarks"""
    from app.services.location_resolver_service import LocationResolverService

    service = LocationResolverService()

    # Test "deliver to" pattern
    result = service._extract_delivery_location_from_remarks("Please deliver to LAAH 424")
    assert result is not None
    assert "laah" in result.lower() or "424" in result

    # Test empty remarks
    result_empty = service._extract_delivery_location_from_remarks("")
    assert result_empty is None

    # Test None-like remarks
    result_none = service._extract_delivery_location_from_remarks(None)
    assert result_none is None

    print("[PASS] Extract location from remarks test passed")


def test_no_city_assumes_local():
    """Test that orders without a city are assumed local"""
    from app.services.location_resolver_service import LocationResolverService

    service = LocationResolverService()
    resolved = service.resolve_location(SAMPLE_NO_CITY_ORDER)

    assert resolved.is_local_delivery == True, "Orders without city should default to local"
    print("[PASS] No city assumes local test passed")


def test_combine_addresses():
    """Test _combine_addresses helper"""
    from app.services.location_resolver_service import LocationResolverService

    service = LocationResolverService()

    # Test with both addresses
    result = service._combine_addresses("123 Main St", "Suite 100")
    assert result == "123 Main St Suite 100"

    # Test with only address1
    result = service._combine_addresses("123 Main St", "")
    assert result == "123 Main St"

    # Test with empty both
    result = service._combine_addresses("", "")
    assert result == ""

    print("[PASS] Combine addresses test passed")


def test_west_campus_portable_address_maps_to_portables_label():
    """Test that West Campus portable addresses resolve to a stable label."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    addresses = [
        "781 West Campus Blvd Building 0067",
        "769 West Campus Blvd. Bldg. 0066 - Suite 110 - TAMU 2592",
    ]

    expected = {
        "781 West Campus Blvd Building 0067": "Portables 0067",
        "769 West Campus Blvd. Bldg. 0066 - Suite 110 - TAMU 2592": "Portables 0066",
    }

    for address in addresses:
        extracted = extract_building_code_from_location(address)
        assert extracted == expected[address]

    service = LocationResolverService()
    resolved = service.resolve_location(
        {
            "orderNumber": "TESTPORTABLE",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "781 West Campus Blvd",
                "address2": "Building 0067",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )

    assert resolved.is_local_delivery is True
    assert resolved.building_code == "Portables 0067"
    assert resolved.display_location == "Portables 0067"
    assert resolved.source == "address"

    resolved_alt_format = service.resolve_location(
        {
            "orderNumber": "TESTPORTABLE2",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "769 West Campus Blvd. Bldg. 0066 - Suite 110 - TAMU 2592",
                "address2": "",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )
    assert resolved_alt_format.building_code == "Portables 0066"
    assert resolved_alt_format.display_location == "Portables 0066"
    assert resolved_alt_format.source == "address"

    print("[PASS] West Campus portable address mapping test passed")


def test_east_29th_street_variants_normalize_to_street_label():
    """Test that noisy 2900 E 29th Street variants collapse to one display label."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "2900 East 29th Street Health Hub, Room S11",
        "2900 East 29th St. Room S11",
        "2900 E 29th St",
    ]

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == "E 29th St"

    service = LocationResolverService()

    resolved_with_address2 = service.resolve_location(
        {
            "orderNumber": "TEST29TH1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "2900 East 29th Street",
                "address2": "Health Hub, Room S11",
                "city": "Bryan",
                "state": "TX",
                "postalCode": "77802",
            },
        }
    )
    assert resolved_with_address2.building_code == "E 29th St"
    assert resolved_with_address2.display_location == "E 29th St"
    assert resolved_with_address2.source == "address"

    resolved_single_line = service.resolve_location(
        {
            "orderNumber": "TEST29TH2",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "2900 E 29th St",
                "address2": "",
                "city": "Bryan",
                "state": "TX",
                "postalCode": "77802",
            },
        }
    )
    assert resolved_single_line.building_code == "E 29th St"
    assert resolved_single_line.display_location == "E 29th St"
    assert resolved_single_line.source == "address"

    print("[PASS] East 29th Street normalization test passed")


def test_jcain_variants_normalize_to_building_code():
    """Test that Cain/Mechanical Engineering variants normalize to JCAIN."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "Mechanical Engineering 327 J.J. Cain Building",
        "College Of Engineering Dept of Mechanical Engineering",
        "College Of Engineering",
        "Materials Science and Engineering 327 J.J. Cain Building",
    ]

    expected = {
        "Mechanical Engineering 327 J.J. Cain Building": "JCAIN",
        "College Of Engineering Dept of Mechanical Engineering": "JCAIN",
        "College Of Engineering": None,
        "Materials Science and Engineering 327 J.J. Cain Building": "JCAIN",
    }

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == expected[address]

    service = LocationResolverService()
    resolved = service.resolve_location(
        {
            "orderNumber": "TESTJCAIN1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "Mechanical Engineering 327 J.J. Cain Building",
                "address2": "",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )
    assert resolved.building_code == "JCAIN"
    assert resolved.display_location == "JCAIN"
    assert resolved.source == "address"

    print("[PASS] JCAIN normalization test passed")

def test_library_annex_variants_normalize_to_anex():
    """Test that library annex / 5000 TAMU variants normalize to ANEX."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "5000 TAMU LIBR Annex",
        "library annex 6th floor digital initiatives suite",
        "5000 TAMU",
    ]

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == "ANEX"

    service = LocationResolverService()

    resolved_split_address = service.resolve_location(
        {
            "orderNumber": "TESTANEX1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "5000 TAMU",
                "address2": "LIBR Annex",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )
    assert resolved_split_address.building_code == "ANEX"
    assert resolved_split_address.display_location == "ANEX"
    assert resolved_split_address.source in {"address2", "address"}

    resolved_single_line = service.resolve_location(
        {
            "orderNumber": "TESTANEX2",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "5000 TAMU LIBR Annex",
                "address2": "",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )
    assert resolved_single_line.building_code == "ANEX"
    assert resolved_single_line.display_location == "ANEX"
    assert resolved_single_line.source == "address"

    print("[PASS] Library Annex normalization test passed")


def test_esl_rellis_address_normalizes_to_display_label():
    """Test that 1210 Avenue A resolves to ESL RELLIS."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "1210 Avenue A",
        "1210 Avenue A, Bryan, TX, 77807",
    ]

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == "ESL RELLIS"

    service = LocationResolverService()
    resolved = service.resolve_location(
        {
            "orderNumber": "TESTRELLIS1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "1210 Avenue A",
                "address2": "",
                "city": "Bryan",
                "state": "TX",
                "postalCode": "77807",
            },
        }
    )
    assert resolved.building_code == "ESL RELLIS"
    assert resolved.display_location == "ESL RELLIS"
    assert resolved.source == "address"

    print("[PASS] ESL RELLIS normalization test passed")


def test_allen_variants_normalize_to_display_label():
    """Test that 4220 TAMU / ALLEN variants resolve to ALLEN."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "4220 TAMU",
        "ALLEN BLDG ROOM 2004A",
        "4220 TAMU ALLEN BLDG ROOM 2004A, College Station, TX, 77845",
    ]

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == "ALLEN"

    service = LocationResolverService()
    resolved = service.resolve_location(
        {
            "orderNumber": "TESTALLEN1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "4220 TAMU",
                "address2": "ALLEN BLDG ROOM 2004A",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77845",
            },
        }
    )
    assert resolved.building_code == "ALLEN"
    assert resolved.display_location == "ALLEN"
    assert resolved.source in {"address2", "address"}

    print("[PASS] ALLEN normalization test passed")
def test_zach_address_variants_normalize_to_display_label():
    """Test that 125 Spence Street / TAMU 3579 variants resolve to ZACH."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "125 Spence Street",
        "125 Spence St",
        "TAMU 3579",
        "4220 TAMU ALLEN BLDG ROOM 2004A, College Station, TX, 77845",
    ]

    expected = {
        "125 Spence Street": "ZACH",
        "125 Spence St": "ZACH",
        "TAMU 3579": "ZACH",
        "4220 TAMU ALLEN BLDG ROOM 2004A, College Station, TX, 77845": "ALLEN",
    }

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == expected[address]

    service = LocationResolverService()
    resolved = service.resolve_location(
        {
            "orderNumber": "TESTZACHADDR1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "125 Spence Street",
                "address2": "",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )
    assert resolved.building_code == "ZACH"
    assert resolved.display_location == "ZACH"
    assert resolved.source == "address"

    print("[PASS] ZACH address normalization test passed")
def test_fermier_variants_normalize_to_display_label():
    """Test that Fermier Hall / MS 3367 variants resolve to FERM."""
    from app.services.location_resolver_service import LocationResolverService
    from app.utils.building_mapper import extract_building_code_from_location

    variants = [
        "106 Fermier Hall, MS 3367, College Station, TX, 77843",
        "Fermier Hall",
        "MS 3367",
    ]

    for address in variants:
        extracted = extract_building_code_from_location(address)
        assert extracted == "FERM"

    service = LocationResolverService()
    resolved = service.resolve_location(
        {
            "orderNumber": "TESTFERM1",
            "orderRemarks": "",
            "shippingAddress": {
                "address1": "106 Fermier Hall",
                "address2": "MS 3367",
                "city": "College Station",
                "state": "TX",
                "postalCode": "77843",
            },
        }
    )
    assert resolved.building_code == "FERM"
    assert resolved.display_location == "FERM"
    assert resolved.source in {"address2", "address"}

    print("[PASS] FERM normalization test passed")
if __name__ == "__main__":
    print("Running LocationResolverService tests...")
    print()

    # Import tests
    test_location_resolver_import()
    test_resolved_location_dataclass()

    # Unit tests
    test_combine_addresses()
    test_extract_location_from_remarks()
    test_west_campus_portable_address_maps_to_portables_label()
    test_east_29th_street_variants_normalize_to_street_label()
    test_jcain_variants_normalize_to_building_code()
    test_library_annex_variants_normalize_to_anex()
    test_esl_rellis_address_normalizes_to_display_label()
    test_allen_variants_normalize_to_display_label()
    test_zach_address_variants_normalize_to_display_label()
    test_fermier_variants_normalize_to_display_label()

    # Integration tests
    test_local_delivery_detection()
    test_shipping_order_detection()
    test_no_city_assumes_local()

    print()
    print("[SUCCESS] All LocationResolverService tests passed!")
