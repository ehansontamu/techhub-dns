"""FedEx sandbox Ship API. Shipment POSTs are deliberately never retried."""

import base64
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError as SchemaError

from app.config import settings
from app.schemas.shipment import ShipmentRecipient
from app.utils.exceptions import ExternalServiceError, ValidationError

SERVICE_NAMES = {
    "FEDEX_GROUND": "FedEx Ground", "GROUND_HOME_DELIVERY": "FedEx Home Delivery",
    "FEDEX_EXPRESS_SAVER": "FedEx Express Saver", "FEDEX_2_DAY": "FedEx 2Day", "FEDEX_2_DAY_AM": "FedEx 2Day A.M.",
    "STANDARD_OVERNIGHT": "FedEx Standard Overnight", "PRIORITY_OVERNIGHT": "FedEx Priority Overnight", "FIRST_OVERNIGHT": "FedEx First Overnight",
}


def shipping_date() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).date().isoformat()


class FedExRejected(Exception):
    """The request was rejected, or failed before shipment creation was attempted."""


def fedex_party(recipient: dict) -> dict:
    return {
        "contact": {
            "personName": recipient["name"],
            "companyName": recipient.get("company", ""),
            "phoneNumber": "".join(c for c in recipient["phone"] if c.isdigit()),
        },
        "address": {
            "streetLines": [recipient["address1"]] + ([recipient["address2"]] if recipient.get("address2") else []),
            "city": recipient["city"],
            "stateOrProvinceCode": recipient["state"],
            "postalCode": recipient["postal_code"],
            "countryCode": recipient.get("country", "US"),
            "residential": recipient.get("residential", False),
        },
    }


class FedExService:
    base_url = "https://apis-sandbox.fedex.com"
    environment = "sandbox"

    def config_status(self) -> dict:
        missing = [name for name in ("fedex_client_id", "fedex_client_secret", "fedex_account_number", "fedex_shipper_json")
                   if not str(getattr(settings, name, None) or "").strip()]
        if not settings.fedex_enabled:
            missing.append("fedex_enabled")
        if not missing:
            try:
                self.shipper()
            except ValidationError:
                missing.append("valid fedex_shipper_json")
        return {"configured": not missing, "missing": missing, "environment": self.environment}

    def setup_status(self) -> dict:
        """Report loaded configuration only; never expose values or contact FedEx."""
        config = self.config_status()
        checks = [{"setting": "FEDEX_ENABLED", "label": "Shipping enabled",
                   "status": "enabled" if settings.fedex_enabled else "disabled"}]
        for name, label in (("fedex_client_id", "Sandbox API key"),
                            ("fedex_client_secret", "Sandbox API secret"),
                            ("fedex_account_number", "FedEx test account")):
            checks.append({"setting": name.upper(), "label": label,
                           "status": "set" if str(getattr(settings, name, None) or "").strip() else "missing"})
        sender_status = "missing"
        if str(settings.fedex_shipper_json or "").strip():
            try:
                self.shipper()
                sender_status = "valid"
            except ValidationError:
                sender_status = "invalid"
        checks.append({"setting": "FEDEX_SHIPPER_JSON", "label": "Sender address and phone", "status": sender_status})
        return {**config, "checks": checks, "connection_verified": False}

    def shipper(self) -> dict:
        try:
            return ShipmentRecipient.model_validate(json.loads(settings.fedex_shipper_json or "{}")).model_dump()
        except (ValueError, TypeError, SchemaError) as exc:
            raise ValidationError("Configure a valid US FedEx shipper address and phone in FEDEX_SHIPPER_JSON") from exc

    def build_request(self, draft: dict, boxes: list[dict], order_number: str) -> dict:
        if not self.config_status()["configured"]:
            raise ValidationError("FedEx sandbox credentials, account, and shipper must be configured first")
        return {
            "labelResponseOptions": "LABEL",
            "processingOptionType": "SYNCHRONOUS_ONLY",
            "oneLabelAtATime": False,
            "accountNumber": {"value": settings.fedex_account_number},
            "requestedShipment": {
                "shipper": fedex_party(self.shipper()),
                "recipients": [fedex_party(draft["recipient"])],
                "shipDatestamp": shipping_date(),
                "serviceType": draft["service_type"],
                "packagingType": "YOUR_PACKAGING",
                "pickupType": "USE_SCHEDULED_PICKUP",
                "shippingChargesPayment": {
                    "paymentType": "SENDER",
                    "payor": {"responsibleParty": {"accountNumber": {"value": settings.fedex_account_number}}},
                },
                "labelSpecification": {"imageType": "PDF", "labelStockType": "PAPER_4X6", "labelFormatType": "COMMON2D"},
                "totalPackageCount": len(boxes),
                "requestedPackageLineItems": [
                    {
                        "sequenceNumber": i + 1,
                        "groupPackageCount": 1,
                        "weight": {"units": "LB", "value": box["shipping_weight_lb"]},
                        "customerReferences": [{"customerReferenceType": "CUSTOMER_REFERENCE", "value": f"{order_number} / {box['name']}"[:40]}],
                    }
                    for i, box in enumerate(boxes)
                ],
            },
        }

    def _access_token(self) -> str:
        try:
            with httpx.Client(timeout=20) as client:
                token_response = client.post(self.base_url + "/oauth/token", data={
                    "grant_type": "client_credentials", "client_id": settings.fedex_client_id,
                    "client_secret": settings.fedex_client_secret,
                })
                token_response.raise_for_status()
                return token_response.json()["access_token"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise FedExRejected("FedEx sandbox authentication failed; check the configured credentials") from exc

    def rates(self, draft: dict, boxes: list[dict]) -> list[dict]:
        if not self.config_status()["configured"]:
            raise ValidationError("Configure FedEx sandbox Ship and Rates API access before requesting prices")
        payload = {
            "accountNumber": {"value": settings.fedex_account_number},
            "requestedShipment": {
                "shipper": {"address": fedex_party(self.shipper())["address"]},
                "recipient": {"address": fedex_party(draft["recipient"])["address"]},
                "shipDateStamp": shipping_date(),
                "pickupType": "USE_SCHEDULED_PICKUP", "packagingType": "YOUR_PACKAGING",
                "rateRequestType": ["ACCOUNT"], "totalPackageCount": len(boxes),
                "requestedPackageLineItems": [{"groupPackageCount": 1, "weight": {"units": "LB", "value": box["shipping_weight_lb"]}} for box in boxes],
            },
        }
        try:
            token = self._access_token()
            with httpx.Client(timeout=45) as client:
                response = client.post(self.base_url + "/rate/v1/rates/quotes", json=payload,
                                       headers={"Authorization": f"Bearer {token}", "x-locale": "en_US"})
                response.raise_for_status()
                return self.extract_rates(response.json(), draft["recipient"].get("residential", False))
        except (httpx.HTTPError, ValueError, TypeError, FedExRejected) as exc:
            raise ExternalServiceError("FedEx", "sandbox rate quote", "Prices could not be retrieved. Check Rates API access and the saved address, then retry.") from exc

    @staticmethod
    def extract_rates(result: dict, residential: bool) -> list[dict]:
        # Compare whole-shipment ACCOUNT totals in USD, including returned surcharges.
        options = {}
        for reply in result.get("output", {}).get("rateReplyDetails", []):
            service = reply.get("serviceType")
            if service not in SERVICE_NAMES:
                continue
            if (service == "FEDEX_GROUND" and residential) or (service == "GROUND_HOME_DELIVERY" and not residential):
                continue
            for detail in reply.get("ratedShipmentDetails", []):
                if detail.get("rateType") not in {"ACCOUNT", "PREFERRED_ACCOUNT", "PAYOR_ACCOUNT_SHIPMENT"}:
                    continue
                currency = detail.get("currency") or (detail.get("shipmentRateDetail") or {}).get("currency")
                if currency != "USD":
                    continue
                try:
                    price = Decimal(str(detail["totalNetCharge"]))
                except (KeyError, InvalidOperation):
                    continue
                if not price.is_finite() or price < 0:
                    continue
                option = {"service_type": service, "name": SERVICE_NAMES[service], "amount": str(price.quantize(Decimal("0.01"))), "currency": currency}
                if service not in options or price < Decimal(options[service]["amount"]):
                    options[service] = option
        if not options:
            raise ValueError("No supported domestic USD account rates were returned")
        return sorted(options.values(), key=lambda option: (Decimal(option["amount"]), option["service_type"]))

    def create_shipment(self, payload: dict, transaction_id: str) -> dict:
        # An OAuth failure is safe to retry; no shipment request has been made yet.
        token = self._access_token()

        # No retry transport: a lost response does not prove FedEx failed to create it.
        with httpx.Client(timeout=60) as client:
            response = client.post(self.base_url + "/ship/v1/shipments", json=payload, headers={
                "Authorization": f"Bearer {token}", "x-customer-transaction-id": transaction_id,
                "x-locale": "en_US", "Content-Type": "application/json",
            })
        result = response.json()
        if 400 <= response.status_code < 500 and isinstance(result, dict) and result.get("errors") and not result.get("output"):
            codes = ", ".join(str(error.get("code", "REJECTED")) for error in result["errors"] if isinstance(error, dict))
            raise FedExRejected(f"FedEx rejected the shipment ({codes[:300]}). Correct the packing/address or account settings before retrying.")
        response.raise_for_status()
        if not isinstance(result, dict):
            raise ValueError("Unexpected FedEx response")
        return result

    @staticmethod
    def extract_packages(result: dict, boxes: list[dict]) -> tuple[str, list[dict]]:
        shipments = result.get("output", {}).get("transactionShipments", [])
        if len(shipments) != 1:
            raise ValueError("Expected one synchronous FedEx shipment")
        shipment = shipments[0]
        pieces = shipment.get("pieceResponses", [])
        if len(pieces) != len(boxes):
            raise ValueError("FedEx did not return a label for every box")
        by_sequence = {int(piece.get("packageSequenceNumber", i + 1)): piece for i, piece in enumerate(pieces)}
        if set(by_sequence) != set(range(1, len(boxes) + 1)):
            raise ValueError("FedEx returned unexpected box sequence numbers")
        packages = []
        for i, box in enumerate(boxes, start=1):
            piece = by_sequence[i]
            documents = piece.get("packageDocuments", [])
            label = next((doc.get("encodedLabel") for doc in documents if doc.get("contentType") == "LABEL" and doc.get("encodedLabel")), None)
            if not label or not base64.b64decode(label, validate=True).startswith(b"%PDF-"):
                raise ValueError("FedEx label is missing or is not a PDF")
            tracking = piece.get("trackingNumber")
            if not tracking:
                raise ValueError("FedEx tracking number is missing")
            packages.append({**box, "tracking_number": tracking, "encoded_label": label})
        return str(shipment.get("masterTrackingNumber") or packages[0]["tracking_number"]), packages
