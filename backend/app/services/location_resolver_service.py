"""
Location Resolver Service for extracting building codes and delivery locations.

Extracted from OrderService for better separation of concerns.
"""

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Any, Optional

from app.utils.building_mapper import (
    extract_building_code_from_location,
    extract_specific_building_code,
    get_building_abbreviation,
)

logger = logging.getLogger(__name__)


@dataclass
class ResolvedLocation:
    """Result of location resolution."""
    building_code: Optional[str]
    display_location: str
    source: str  # "remarks", "address", "arcgis", "raw"
    is_local_delivery: bool


class LocationResolverService:
    """
    Service for resolving delivery locations from Inflow order data.

    Uses priority-based resolution:
    1. Check order remarks for building codes
    2. Check alternative location patterns in remarks
    3. Check shipping addresses (address2, then address1)
    4. Use ArcGIS lookup as fallback
    5. Return raw address as last resort
    """

    # Patterns for extracting alternative delivery locations from remarks
    LOCATION_PATTERNS = [
        r'deliver\s+to\s+([^\r\n,]+)',      # "deliver to LAAH 424"
        r'delivery\s+to\s+([^\r\n,]+)',     # "delivery to LAAH 424"
        r'deliver\s+at\s+([^\r\n,]+)',      # "deliver at LAAH 424"
        r'deliver\s+to\s+([^\r\n,;]+?)(?:\s*[-–—]|\s*$|\r|\n|,|;|$)',
        r'need\s+to\s+([^\r\n,;]+?)(?:\s*[-–—]|\s*$|\r|\n|,|;|$)',
        r'located\s+at\s+([^\r\n,;]+?)(?:\s*[-–—]|\s*$|\r|\n|,|;|$)',
    ]

    # Local delivery cities (Bryan/College Station area)
    LOCAL_CITIES = {"BRYAN", "COLLEGE STATION"}
    LOCAL_CITY_MATCH_THRESHOLD = 0.9

    def resolve_location(self, inflow_data: Dict[str, Any]) -> ResolvedLocation:
        """
        Resolve delivery location from Inflow order data.

        Args:
            inflow_data: Raw Inflow order payload

        Returns:
            ResolvedLocation with building_code, display_location, source, and is_local_delivery
        """
        order_number = inflow_data.get("orderNumber", "UNKNOWN")
        order_remarks = inflow_data.get("orderRemarks", "")
        shipping_addr_obj = inflow_data.get("shippingAddress", {})

        # Extract address components
        address1 = shipping_addr_obj.get("address1", "")
        address2 = shipping_addr_obj.get("address2", "")
        shipping_address = self._combine_addresses(address1, address2)

        # Determine city and local delivery status
        city = self._get_city(shipping_addr_obj, shipping_address, order_number)
        is_local = self._is_local_delivery(city)

        if not is_local:
            # For shipping orders, use city as location
            return ResolvedLocation(
                building_code=None,
                display_location=city if city else shipping_address,
                source="city",
                is_local_delivery=False
            )

        # For local deliveries, try to extract building codes
        building_code, source = self._resolve_building_code(
            order_number, order_remarks, address1, address2, shipping_address
        )

        if building_code:
            return ResolvedLocation(
                building_code=building_code,
                display_location=building_code,
                source=source,
                is_local_delivery=True
            )

        # Fallback: use raw address
        alternative_location = self._extract_delivery_location_from_remarks(order_remarks)
        fallback = alternative_location or shipping_address

        return ResolvedLocation(
            building_code=None,
            display_location=fallback,
            source="raw",
            is_local_delivery=True
        )

    def _combine_addresses(self, address1: str, address2: str) -> str:
        """Combine address1 and address2 into a single string."""
        parts = [part for part in [address1, address2] if part]
        return " ".join(parts) if parts else address1

    def _get_city(self, shipping_addr: Dict, full_address: str, order_number: str) -> str:
        """Extract city from shipping address, with fallback detection."""
        city = shipping_addr.get("city", "").strip() if shipping_addr.get("city") else ""

        # Try to detect city from address if missing
        if not city and full_address:
            inferred_city = self._infer_city_from_address(full_address)
            if inferred_city:
                city = inferred_city
                logger.info(
                    "City inferred from address for order %s: '%s'",
                    order_number,
                    inferred_city,
                )

        return city

    def _is_local_delivery(self, city: str) -> bool:
        """Check if city is in the local delivery area (Bryan/College Station)."""
        if not city:
            return True  # Assume local if no city specified
        return self.is_local_delivery_city(city)

    @staticmethod
    def _normalize_city_name(city: str) -> str:
        return re.sub(r"[^A-Z]", "", city.upper())

    def is_local_delivery_city(self, city: str) -> bool:
        """Allow obvious local-city typos so local deliveries do not route to shipping."""
        normalized_city = self._normalize_city_name(city)
        if not normalized_city:
            return True

        for local_city in self.LOCAL_CITIES:
            normalized_local_city = self._normalize_city_name(local_city)
            if normalized_city == normalized_local_city:
                return True

            similarity = SequenceMatcher(
                None, normalized_city, normalized_local_city
            ).ratio()
            if similarity >= self.LOCAL_CITY_MATCH_THRESHOLD:
                logger.info(
                    "Treating city '%s' as local delivery based on close match to '%s' (%.2f)",
                    city,
                    local_city,
                    similarity,
                )
                return True

        return False

    @staticmethod
    def _infer_city_from_address(full_address: str) -> str:
        address_upper = full_address.upper()
        if "HOUSTON" in address_upper:
            return "Houston"
        return ""

    def _resolve_building_code(
        self,
        order_number: str,
        order_remarks: str,
        address1: str,
        address2: str,
        shipping_address: str
    ) -> tuple[Optional[str], str]:
        """
        Try to resolve building code using priority-based approach.

        Returns:
            Tuple of (building_code, source) where source indicates where code was found
        """
        # Known physical addresses are more authoritative than generic building names
        # that may appear elsewhere in the order remarks.
        for address, source in (
            (address2, "address2"),
            (shipping_address, "address"),
        ):
            building_code = extract_specific_building_code(address)
            if building_code:
                logger.info(
                    "[%s] Specific building code '%s' found in %s",
                    order_number,
                    building_code,
                    source,
                )
                return building_code, source

        # PRIORITY 1: Check order remarks directly
        if order_remarks:
            building_code = extract_building_code_from_location(order_remarks)
            if building_code:
                logger.info(f"[{order_number}] Building code '{building_code}' found in remarks")
                return building_code, "remarks"

        # PRIORITY 2: Check alternative location patterns in remarks
        if order_remarks:
            alternative_location = self._extract_delivery_location_from_remarks(order_remarks)
            if alternative_location:
                building_code = extract_building_code_from_location(alternative_location)
                if building_code:
                    logger.info(f"[{order_number}] Building code '{building_code}' from alt location")
                    return building_code, "remarks_pattern"

        # PRIORITY 3: Check address2 (often contains building info)
        if address2:
            building_code = extract_building_code_from_location(address2)
            if building_code:
                logger.info(f"[{order_number}] Building code '{building_code}' from address2")
                return building_code, "address2"

        # PRIORITY 4: Check combined shipping address
        if shipping_address:
            building_code = extract_building_code_from_location(shipping_address)
            if building_code:
                logger.info(f"[{order_number}] Building code '{building_code}' from shipping address")
                return building_code, "address"

        # PRIORITY 5: Try ArcGIS lookup
        if shipping_address:
            building_code = get_building_abbreviation(None, shipping_address)
            if building_code:
                logger.info(f"[{order_number}] Building code '{building_code}' from ArcGIS")
                return building_code, "arcgis"

        if address2:
            building_code = get_building_abbreviation(None, address2)
            if building_code:
                logger.info(f"[{order_number}] Building code '{building_code}' from ArcGIS (address2)")
                return building_code, "arcgis"

        logger.debug(f"[{order_number}] No building code found")
        return None, "none"

    def _extract_delivery_location_from_remarks(self, order_remarks: str) -> Optional[str]:
        """
        Extract alternative delivery location from order remarks.

        Looks for patterns like "deliver to [location]" or "delivery to [location]"

        Example: "deliver to LAAH 424" -> "LAAH 424"
        """
        if not order_remarks:
            return None

        remarks_lower = order_remarks.lower()

        for pattern in self.LOCATION_PATTERNS:
            match = re.search(pattern, remarks_lower, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                location = re.sub(r'[.,;:]+$', '', location)
                if location:
                    return location

        return None


# Singleton for easy import
location_resolver_service = LocationResolverService()
