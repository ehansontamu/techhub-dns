from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from dotenv import load_dotenv

from app.services.bigcommerce_chat.classifiers import (
    brands_for_group,
    brand_terms_for_group,
    dimension_rules,
    normalize_text as classify_normalize_text,
    organization_aliases,
    phrase_matches,
    product_matches_group_brand,
)

load_dotenv()


ORG_ALIASES: dict[str, list[str]] = organization_aliases()

PRODUCT_ALIASES: dict[str, list[str]] = {
    "hp machines": ["hp elitebook", "hp probook", "hp prodesk", "hp zbook", "hp workstation", "hp desktop", "hp laptop"],
    "hp machine": ["hp elitebook", "hp probook", "hp prodesk", "hp zbook", "hp workstation", "hp desktop", "hp laptop"],
    "hp computers": ["hp elitebook", "hp probook", "hp prodesk", "hp zbook", "hp workstation", "hp desktop", "hp laptop"],
    "hp computer": ["hp elitebook", "hp probook", "hp prodesk", "hp zbook", "hp workstation", "hp desktop", "hp laptop"],
    "hp laptops": ["hp elitebook", "hp probook", "hp zbook", "hp laptop"],
    "hp laptop": ["hp elitebook", "hp probook", "hp zbook", "hp laptop"],
    "dell machines": ["dell latitude", "dell precision", "dell optiplex", "dell pro 14", "dell pro 16", "dell pro slim", "dell pro max", "dell workstation", "dell desktop", "dell laptop"],
    "dell machine": ["dell latitude", "dell precision", "dell optiplex", "dell pro 14", "dell pro 16", "dell pro slim", "dell pro max", "dell workstation", "dell desktop", "dell laptop"],
    "dell computers": ["dell latitude", "dell precision", "dell optiplex", "dell pro 14", "dell pro 16", "dell pro slim", "dell pro max", "dell workstation", "dell desktop", "dell laptop"],
    "dell computer": ["dell latitude", "dell precision", "dell optiplex", "dell pro 14", "dell pro 16", "dell pro slim", "dell pro max", "dell workstation", "dell desktop", "dell laptop"],
    "dell laptops": ["dell latitude", "dell precision", "dell pro 14", "dell pro 16", "dell laptop"],
    "dell laptop": ["dell latitude", "dell precision", "dell pro 14", "dell pro 16", "dell laptop"],
    "lenovo machines": ["lenovo thinkpad", "lenovo thinkcentre", "lenovo thinkstation", "lenovo yoga", "lenovo ideapad", "lenovo legion", "lenovo desktop", "lenovo laptop", "lenovo workstation"],
    "lenovo machine": ["lenovo thinkpad", "lenovo thinkcentre", "lenovo thinkstation", "lenovo yoga", "lenovo ideapad", "lenovo legion", "lenovo desktop", "lenovo laptop", "lenovo workstation"],
    "lenovo computers": ["lenovo thinkpad", "lenovo thinkcentre", "lenovo thinkstation", "lenovo yoga", "lenovo ideapad", "lenovo legion", "lenovo desktop", "lenovo laptop", "lenovo workstation"],
    "lenovo computer": ["lenovo thinkpad", "lenovo thinkcentre", "lenovo thinkstation", "lenovo yoga", "lenovo ideapad", "lenovo legion", "lenovo desktop", "lenovo laptop", "lenovo workstation"],
    "lenovo laptops": ["lenovo thinkpad", "lenovo yoga", "lenovo ideapad", "lenovo legion", "lenovo laptop"],
    "lenovo laptop": ["lenovo thinkpad", "lenovo yoga", "lenovo ideapad", "lenovo legion", "lenovo laptop"],
    "macs": ["mac", "macbook", "imac"],
}


DEFAULT_MAX_ORDER_SCAN = 5000
DEFAULT_MAX_LINE_ITEM_ORDER_SCAN = 500
BC_REQUEST_TIMEOUT_SECONDS = 15
RATE_LIMIT_RETRY_ATTEMPTS = 3
RATE_LIMIT_LOW_WATERMARK = 5
RATE_LIMIT_MAX_SLEEP_SECONDS = 30
OPEN_FULFILLMENT_STATUSES = {
    "Awaiting Fulfillment",
    "Awaiting Shipment",
    "Partially Shipped",
}
FULFILLED_STATUSES = {"Shipped", "Completed"}


class BigCommerceConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class BigCommerceConfig:
    store_hash: str
    access_token: str

    @classmethod
    def from_env(cls) -> "BigCommerceConfig":
        store_hash = os.getenv("BC_STORE_HASH", "").strip()
        access_token = os.getenv("BC_ACCESS_TOKEN", "").strip()

        if not store_hash or not access_token:
            raise BigCommerceConfigError(
                "Set BC_STORE_HASH and BC_ACCESS_TOKEN in your .env file."
            )

        return cls(store_hash=store_hash, access_token=access_token)


class BigCommerceClient:
    """Tiny read-only BigCommerce REST client.

    This class intentionally implements GET only. Keep writes out of this file
    unless you deliberately change the project from read-only.
    """

    def __init__(self, config: BigCommerceConfig | None = None) -> None:
        self.config = config or BigCommerceConfig.from_env()
        self.base_url = f"https://api.bigcommerce.com/stores/{self.config.store_hash}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Auth-Token": self.config.access_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @staticmethod
    def _rate_limit_sleep_seconds(response: requests.Response) -> float:
        value = response.headers.get("X-Rate-Limit-Time-Reset-Ms")
        if not value:
            return 1.0
        try:
            return max(0.0, min(float(value) / 1000, RATE_LIMIT_MAX_SLEEP_SECONDS))
        except ValueError:
            return 1.0

    def _pause_if_quota_is_low(self, response: requests.Response) -> None:
        left = response.headers.get("X-Rate-Limit-Requests-Left")
        if left is None:
            return
        try:
            requests_left = int(left)
        except ValueError:
            return
        if requests_left <= RATE_LIMIT_LOW_WATERMARK:
            time.sleep(self._rate_limit_sleep_seconds(response))

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/"):
            path = f"/{path}"

        for attempt in range(RATE_LIMIT_RETRY_ATTEMPTS + 1):
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params or {},
                timeout=BC_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 429 and attempt < RATE_LIMIT_RETRY_ATTEMPTS:
                time.sleep(self._rate_limit_sleep_seconds(response))
                continue

            response.raise_for_status()
            self._pause_if_quota_is_low(response)
            response_text = getattr(response, "text", None)
            if response_text is not None and not response_text.strip():
                return None
            return response.json()

        raise RuntimeError("Unreachable BigCommerce rate limit retry state.")

    def post(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("BigCommerce client is read-only. POST is disabled.")

    def put(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("BigCommerce client is read-only. PUT is disabled.")

    def patch(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("BigCommerce client is read-only. PATCH is disabled.")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("BigCommerce client is read-only. DELETE is disabled.")


def _client() -> BigCommerceClient:
    return BigCommerceClient()


def _iso_days_ago(days: int) -> str:
    days = max(1, min(days, 365))
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_date_start(date_value: str) -> str:
    parsed = datetime.fromisoformat(date_value).replace(tzinfo=timezone.utc)
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _iso_date_end_exclusive(date_value: str) -> str:
    parsed = datetime.fromisoformat(date_value).replace(tzinfo=timezone.utc)
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _money(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _search_terms(term: str) -> set[str]:
    cleaned = term.strip().lower()
    terms = {cleaned} if cleaned else set()
    aliases = {
        "jim": "james",
        "james": "jim",
        "bob": "robert",
        "rob": "robert",
        "robert": "bob",
        "bill": "william",
        "will": "william",
        "william": "bill",
        "mike": "michael",
        "michael": "mike",
        "dave": "david",
        "david": "dave",
        "liz": "elizabeth",
        "beth": "elizabeth",
        "elizabeth": "liz",
    }
    if cleaned in aliases:
        terms.add(aliases[cleaned])
    if cleaned in ORG_ALIASES:
        terms.update(ORG_ALIASES[cleaned])
    if cleaned in PRODUCT_ALIASES:
        terms.update(PRODUCT_ALIASES[cleaned])
    return terms


def _normalize_text(value: str) -> str:
    return classify_normalize_text(value)


def _order_search_blob(order: dict[str, Any]) -> str:
    billing = order.get("billing_address") or {}
    form_values = _extract_form_field_values(order)
    values = [
        billing.get("first_name"),
        billing.get("last_name"),
        billing.get("company"),
        billing.get("email"),
        order.get("customer_message"),
        order.get("staff_notes"),
        *form_values,
    ]
    return _normalize_text(" ".join(str(value or "") for value in values))


def _matches_fuzzy_term(blob: str, term: str) -> bool:
    return phrase_matches(blob, term)


def _extract_form_field_values(order: dict[str, Any]) -> list[str]:
    values: list[str] = []

    def collect(fields: Any) -> None:
        if not isinstance(fields, list):
            return
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            value = field.get("value")
            if name:
                values.append(str(name))
            if value:
                values.append(str(value))

    collect(order.get("form_fields"))

    billing = order.get("billing_address") or {}
    collect(billing.get("form_fields"))

    shipping_addresses = order.get("shipping_addresses") or []
    if isinstance(shipping_addresses, list):
        for address in shipping_addresses:
            if isinstance(address, dict):
                collect(address.get("form_fields"))

    return values


def _summarize_form_fields(order: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    def collect(source: str, form_fields: Any) -> None:
        if not isinstance(form_fields, list):
            return
        for field in form_fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            if not name:
                continue
            fields[f"{source}: {name}"] = field.get("value")

    collect("order", order.get("form_fields"))
    billing = order.get("billing_address") or {}
    collect("billing", billing.get("form_fields"))

    shipping_addresses = order.get("shipping_addresses") or []
    if isinstance(shipping_addresses, list):
        for index, address in enumerate(shipping_addresses, start=1):
            if isinstance(address, dict):
                collect(f"shipping {index}", address.get("form_fields"))

    return fields


def _form_field_value(order: dict[str, Any], field_name: str) -> str | None:
    target = _normalize_text(field_name)

    def find(fields: Any) -> str | None:
        if not isinstance(fields, list):
            return None
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            value = field.get("value")
            if name and _normalize_text(str(name)) == target and value:
                return str(value)
        return None

    found = find(order.get("form_fields"))
    if found:
        return found

    billing = order.get("billing_address") or {}
    found = find(billing.get("form_fields"))
    if found:
        return found

    shipping_addresses = order.get("shipping_addresses") or []
    if isinstance(shipping_addresses, list):
        for address in shipping_addresses:
            if isinstance(address, dict):
                found = find(address.get("form_fields"))
                if found:
                    return found

    return None


def _dimension_config(dimension: str) -> dict[str, Any]:
    return dimension_rules().get(dimension, {})


def _dimension_field(dimension: str) -> str | None:
    field = _dimension_config(dimension).get("field")
    return str(field) if field else None


def _dimension_label(dimension: str) -> str:
    return str(_dimension_config(dimension).get("label") or dimension.replace("_", " "))


def _split_multi_value(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [part.strip() for part in value.replace("\n", ",").split(",")]
    return [part for part in parts if part]


def _dimension_values(order: dict[str, Any], dimension: str) -> list[str]:
    field = _dimension_field(dimension)
    if not field:
        return ["Unknown"]

    value = _form_field_value(order, field)
    if dimension == "college_unit" and value:
        return [_clean_unit_prefix(value)]
    values = _split_multi_value(value)
    return values or ["Unknown"]


def _primary_dimension_value(order: dict[str, Any], dimension: str) -> str:
    return _dimension_values(order, dimension)[0]


def _dimension_search_terms(dimension: str, query: str) -> set[str]:
    terms = _search_terms(query)
    aliases = _dimension_config(dimension).get("aliases", {})
    cleaned = query.strip().lower()
    if cleaned in aliases:
        terms.update(str(alias) for alias in aliases[cleaned])
    return terms


def _order_matches_dimension(order: dict[str, Any], dimension: str, query: str) -> bool:
    values = _dimension_values(order, dimension)
    blob = _normalize_text(" ".join(values))
    return any(_matches_fuzzy_term(blob, term) for term in _dimension_search_terms(dimension, query))


def _person_label(order: dict[str, Any]) -> str:
    billing = order.get("billing_address") or {}
    recipient = _form_field_value(order, "Recipient UIN(s) or Name(s) (comma separated)")

    name = " ".join(
        part for part in [billing.get("first_name"), billing.get("last_name")] if part
    ).strip()
    if name:
        return name
    if recipient:
        return recipient
    if billing.get("email"):
        return str(billing["email"])
    return "Unknown"


def _billing_contact_label(order: dict[str, Any]) -> str:
    return _person_label(order)


def _customer_record_label(customer: dict[str, Any]) -> str:
    name = " ".join(
        part
        for part in [customer.get("first_name"), customer.get("last_name")]
        if part
    ).strip()
    return name or customer.get("email") or f"Customer {customer.get('id')}"


def _customer_records_by_id(customer_ids: list[int]) -> dict[int, dict[str, Any]]:
    unique_ids = sorted({int(customer_id) for customer_id in customer_ids if customer_id})
    records: dict[int, dict[str, Any]] = {}
    if not unique_ids:
        return records

    client = _client()
    for index in range(0, len(unique_ids), 50):
        batch = unique_ids[index : index + 50]
        data = client.get("/v3/customers", {"id:in": ",".join(str(value) for value in batch)})
        for customer in data.get("data", []):
            records[int(customer["id"])] = customer
    return records


def _customer_lookup_for_orders(orders: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return _customer_records_by_id(
        [int(order.get("customer_id") or 0) for order in orders if order.get("customer_id")]
    )


def _placed_by_label(
    order: dict[str, Any],
    customer_lookup: dict[int, dict[str, Any]] | None = None,
) -> str:
    customer_id = int(order.get("customer_id") or 0)
    if customer_id and customer_lookup and customer_id in customer_lookup:
        return _customer_record_label(customer_lookup[customer_id])
    if customer_id:
        records = _customer_records_by_id([customer_id])
        if customer_id in records:
            return _customer_record_label(records[customer_id])
    return _billing_contact_label(order)


def _customer_record_summary(customer: dict[str, Any] | None) -> dict[str, Any] | None:
    if not customer:
        return None
    return {
        "id": customer.get("id"),
        "name": _customer_record_label(customer),
        "email": customer.get("email"),
        "company": customer.get("company"),
    }


def _address_name(address: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in [address.get("first_name"), address.get("last_name")]
        if part
    ).strip()


def _summarize_address(address: dict[str, Any]) -> dict[str, Any]:
    street_lines = [
        str(address.get(field))
        for field in ["street_1", "street_2"]
        if address.get(field)
    ]
    return {
        "name": _address_name(address) or None,
        "company": address.get("company"),
        "street": ", ".join(street_lines) or None,
        "city": address.get("city"),
        "state": address.get("state") or address.get("state_iso2"),
        "zip": address.get("zip"),
        "country": address.get("country"),
        "email": address.get("email"),
        "phone": address.get("phone"),
        "form_fields": {
            str(field.get("name")): field.get("value")
            for field in address.get("form_fields") or []
            if isinstance(field, dict) and field.get("name")
        },
    }


def _shipping_addresses_for_order(order_id: int) -> list[dict[str, Any]]:
    try:
        data = _client().get(f"/v2/orders/{int(order_id)}/shipping_addresses")
    except (requests.HTTPError, ValueError):
        return []
    if isinstance(data, list):
        return data
    return []


def _shipments_for_order(order_id: int) -> list[dict[str, Any]]:
    try:
        data = _client().get(f"/v2/orders/{int(order_id)}/shipments")
    except (requests.HTTPError, ValueError):
        return []
    if isinstance(data, list):
        return data
    return []


def _unit_label(order: dict[str, Any]) -> str:
    college_unit = _primary_dimension_value(order, "college_unit")
    if college_unit != "Unknown":
        return college_unit
    billing = order.get("billing_address") or {}
    if billing.get("company"):
        return str(billing["company"])
    return "Unknown"


def _department_code_label(order: dict[str, Any]) -> str:
    return _primary_dimension_value(order, "department_code")


def _group_label(order: dict[str, Any], group_by: str) -> str:
    if group_by == "department_code":
        return _department_code_label(order)
    if group_by in {"college_unit", "unit", "department", "school"}:
        return _unit_label(order)
    return _person_label(order)


def _related_label(order: dict[str, Any], group_by: str) -> str:
    if group_by in {"college_unit", "unit", "department", "school"}:
        return _person_label(order)
    return _unit_label(order)


def _matches_person_name(label: str, terms: set[str]) -> bool:
    normalized = _normalize_text(label)
    return any(_matches_fuzzy_term(normalized, term) for term in terms)


def _clean_unit_prefix(value: str) -> str:
    cleaned = value.strip()
    for prefix in ["TAMU - ", "Texas A&M University - "]:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def _product_matches_keyword(product: dict[str, Any], keyword: str) -> bool:
    normalized_keyword = _normalize_text(keyword)
    if normalized_keyword in {"computer", "computers", "machine", "machines"}:
        return any(
            product_matches_group_brand(product, "computers", brand)
            for brand in brands_for_group("computers")
        )

    terms = _search_terms(keyword)
    blob = _normalize_text(
        " ".join(
            str(product.get(field) or "")
            for field in ["name", "sku", "product_id"]
        )
    )
    blob_tokens = set(blob.split())

    for term in terms:
        normalized = _normalize_text(term)
        if not normalized:
            continue
        if normalized in blob:
            return True

        tokens = [
            token
            for token in normalized.split()
            if len(token) > 1 and token not in {"and", "the"}
        ]
        if tokens and all(token in blob_tokens for token in tokens):
            return True

    return False


def _summarize_order(order: dict[str, Any]) -> dict[str, Any]:
    billing = order.get("billing_address") or {}
    return {
        "id": order.get("id"),
        "date_created": order.get("date_created"),
        "date_modified": order.get("date_modified"),
        "status": order.get("status"),
        "status_id": order.get("status_id"),
        "customer_id": order.get("customer_id"),
        "placed_by": None,
        "billing_contact": _billing_contact_label(order),
        "customer": " ".join(
            part
            for part in [billing.get("first_name"), billing.get("last_name")]
            if part
        )
        or order.get("customer_message")
        or None,
        "company": billing.get("company"),
        "email": billing.get("email"),
        "total_inc_tax": _money(order.get("total_inc_tax")),
        "subtotal_inc_tax": _money(order.get("subtotal_inc_tax")),
        "items_total": order.get("items_total"),
        "payment_method": order.get("payment_method"),
        "staff_notes": order.get("staff_notes"),
        "customer_message": order.get("customer_message"),
        "form_fields": _summarize_form_fields(order),
    }


def _summarize_product(product: dict[str, Any]) -> dict[str, Any]:
    images = product.get("images") or []
    categories = product.get("categories") or []
    return {
        "id": product.get("id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "price": _money(product.get("price")),
        "inventory_level": product.get("inventory_level"),
        "inventory_tracking": product.get("inventory_tracking"),
        "is_visible": product.get("is_visible"),
        "categories": categories,
        "image_count": len(images),
    }


def get_order(order_id: int) -> dict[str, Any]:
    """Get a single order's read-only status summary by BigCommerce order ID."""

    data = _client().get(f"/v2/orders/{int(order_id)}")
    return _summarize_order(data)


def get_order_identity(order_id: int) -> dict[str, Any]:
    """Get who placed, received, and shipped on a single read-only order."""

    order = _client().get(f"/v2/orders/{int(order_id)}")
    customer_id = int(order.get("customer_id") or 0)
    customer_lookup = _customer_records_by_id([customer_id]) if customer_id else {}
    billing = order.get("billing_address") or {}
    shipping_addresses = _shipping_addresses_for_order(int(order_id))
    if not shipping_addresses and isinstance(order.get("shipping_addresses"), list):
        shipping_addresses = [
            address for address in order.get("shipping_addresses") if isinstance(address, dict)
        ]

    return {
        "order_id": order.get("id"),
        "date_created": order.get("date_created"),
        "status": order.get("status"),
        "total_inc_tax": _money(order.get("total_inc_tax")),
        "placed_by": {
            "source": "BigCommerce order.customer_id",
            **(
                _customer_record_summary(customer_lookup.get(customer_id))
                or {
                    "id": customer_id or None,
                    "name": _placed_by_label(order, customer_lookup),
                    "email": None,
                    "company": None,
                }
            ),
        },
        "billing_contact": _summarize_address(billing),
        "shipping_addresses": [
            _summarize_address(address) for address in shipping_addresses
        ],
        "recipient": {
            "college_unit": _primary_dimension_value(order, "college_unit"),
            "department_code": _primary_dimension_value(order, "department_code"),
            "account_numbers": _dimension_values(order, "account_number"),
            "recipient_names_or_uins": _dimension_values(order, "recipient"),
        },
        "note": (
            "Placed-by is the BigCommerce customer account attached to the order. "
            "Billing contact, shipping recipient, and checkout recipient fields may be different people."
        ),
    }


def find_orders_by_customer_name(name: str, days: int = 90, limit: int = 20) -> dict[str, Any]:
    """Find recent orders whose placed-by account, billing contact, or order text matches."""

    orders = list_recent_orders(days=days, limit=min(max(limit, 1), 250))["orders"]
    raw_orders = _list_recent_orders_for_analysis(days=days, max_orders=min(max(limit, 1), 250))
    customer_lookup = _customer_lookup_for_orders(raw_orders)
    placed_by_by_id = {
        int(order["id"]): _placed_by_label(order, customer_lookup)
        for order in raw_orders
        if order.get("id")
    }
    terms = _search_terms(name)
    matches = [
        order
        for order in orders
        if terms
        and any(
            _matches_fuzzy_term(
                _normalize_text(
                    " ".join(
                        [
                            placed_by_by_id.get(int(order.get("id") or 0), ""),
                            *(str(order.get(field) or "") for field in [
                                "customer",
                                "company",
                                "email",
                                "customer_message",
                                "staff_notes",
                            ]),
                            *[
                                str(value or "")
                                for value in (order.get("form_fields") or {}).values()
                            ],
                        ]
                    )
                ),
                term,
            )
            for term in terms
        )
    ]
    for order in matches:
        order["placed_by"] = placed_by_by_id.get(int(order.get("id") or 0))
        order["billing_contact"] = order.get("customer")

    return {
        "query": name,
        "expanded_terms": sorted(terms),
        "days": days,
        "count": len(matches),
        "orders": matches[:limit],
    }


def _order_matches_term(order: dict[str, Any], term: str) -> bool:
    return any(
        _matches_fuzzy_term(
            _normalize_text(
                " ".join(
                    [
                        *(str(order.get(field) or "") for field in [
                            "company",
                            "customer",
                            "email",
                            "customer_message",
                            "staff_notes",
                        ]),
                        *[
                            str(value or "")
                            for value in (order.get("form_fields") or {}).values()
                        ],
                    ]
                )
            ),
            search_term,
        )
        for search_term in _search_terms(term)
    )


def list_recent_orders(days: int = 30, limit: int = 50) -> dict[str, Any]:
    """List recent orders. Useful for status, volume, and customer questions."""

    limit = min(max(limit, 1), 250)
    data = _client().get(
        "/v2/orders",
        {
            "min_date_created": _iso_days_ago(days),
            "limit": limit,
            "sort": "date_created:desc",
        },
    )
    orders = [_summarize_order(order) for order in data]
    return {"days": days, "count": len(orders), "orders": orders}


def _list_recent_orders_for_analysis(days: int = 90, max_orders: int = 250) -> list[dict[str, Any]]:
    return _list_orders_for_analysis(min_date_created=_iso_days_ago(days), max_orders=max_orders)


def _list_orders_since_date_for_analysis(
    start_date: str, max_orders: int = 1000
) -> list[dict[str, Any]]:
    return _list_orders_for_analysis(
        min_date_created=_iso_date_start(start_date), max_orders=max_orders
    )


def _list_orders_for_analysis(
    min_date_created: str,
    max_orders: int = 250,
    max_date_created: str | None = None,
) -> list[dict[str, Any]]:
    max_orders = min(max(max_orders, 1), DEFAULT_MAX_ORDER_SCAN)
    orders: list[dict[str, Any]] = []
    page = 1

    while len(orders) < max_orders:
        page_limit = min(250, max_orders - len(orders))
        params = {
            "min_date_created": min_date_created,
            "limit": page_limit,
            "page": page,
            "sort": "date_created:desc",
        }
        if max_date_created:
            params["max_date_created"] = max_date_created
        data = _client().get(
            "/v2/orders",
            params,
        )
        if not data:
            break

        orders.extend(data)
        if len(data) < page_limit:
            break
        page += 1

    return orders


def _summarize_order_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_product_id": product.get("id"),
        "order_id": product.get("order_id"),
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "quantity": int(product.get("quantity") or 0),
        "total_inc_tax": _money(product.get("total_inc_tax")),
        "base_total": _money(product.get("base_total")),
    }


def get_order_products(order_id: int) -> dict[str, Any]:
    """Get read-only line items for a BigCommerce order."""

    data = _client().get(f"/v2/orders/{int(order_id)}/products")
    products = [_summarize_order_product(product) for product in data]
    return {"order_id": order_id, "count": len(products), "products": products}


def get_revenue_summary(
    start_date: str,
    end_date: str | None = None,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    exclude_statuses: list[str] | None = None,
) -> dict[str, Any]:
    """Return store-wide order revenue totals for a date range."""

    excluded = set(exclude_statuses or ["Cancelled", "Declined", "Refunded"])
    max_orders = min(max(max_orders, 1), DEFAULT_MAX_ORDER_SCAN)
    min_date_created = _iso_date_start(start_date)
    max_date_created = _iso_date_end_exclusive(end_date) if end_date else None
    orders = _list_orders_for_analysis(
        min_date_created=min_date_created,
        max_date_created=max_date_created,
        max_orders=max_orders,
    )

    included_orders = [
        order for order in orders if str(order.get("status") or "Unknown") not in excluded
    ]
    excluded_orders = [
        order for order in orders if str(order.get("status") or "Unknown") in excluded
    ]

    total_inc_tax = sum(float(order.get("total_inc_tax") or 0) for order in included_orders)
    subtotal_inc_tax = sum(float(order.get("subtotal_inc_tax") or 0) for order in included_orders)
    subtotal_ex_tax = sum(float(order.get("subtotal_ex_tax") or 0) for order in included_orders)
    tax_total = sum(float(order.get("total_tax") or 0) for order in included_orders)
    shipping_total = sum(float(order.get("shipping_cost_inc_tax") or 0) for order in included_orders)
    refunded_total = sum(float(order.get("refunded_amount") or 0) for order in included_orders)
    excluded_total_inc_tax = sum(float(order.get("total_inc_tax") or 0) for order in excluded_orders)
    all_status_total_inc_tax = sum(float(order.get("total_inc_tax") or 0) for order in orders)

    status_counts = Counter(str(order.get("status") or "Unknown") for order in orders)
    included_status_counts = Counter(str(order.get("status") or "Unknown") for order in included_orders)
    excluded_status_counts = Counter(str(order.get("status") or "Unknown") for order in excluded_orders)
    order_ids = sorted(
        (int(order["id"]) for order in orders if order.get("id")),
        reverse=True,
    )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "min_date_created": min_date_created,
        "max_date_created": max_date_created,
        "orders_analyzed": len(orders),
        "included_order_count": len(included_orders),
        "excluded_order_count": len(excluded_orders),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "metric_basis": "sum of order total_inc_tax for non-cancelled/non-refunded orders created in the date range",
        "excluded_statuses": sorted(excluded),
        "total_revenue_inc_tax": round(total_inc_tax, 2),
        "all_status_total_inc_tax": round(all_status_total_inc_tax, 2),
        "excluded_status_total_inc_tax": round(excluded_total_inc_tax, 2),
        "subtotal_inc_tax": round(subtotal_inc_tax, 2),
        "subtotal_ex_tax": round(subtotal_ex_tax, 2),
        "tax_total": round(tax_total, 2),
        "shipping_total_inc_tax": round(shipping_total, 2),
        "refunded_amount_total": round(refunded_total, 2),
        "status_counts": dict(status_counts.most_common()),
        "included_status_counts": dict(included_status_counts.most_common()),
        "excluded_status_counts": dict(excluded_status_counts.most_common()),
        "order_ids_sample": order_ids[:25],
    }


def get_shipping_spend_by_method(
    method_keyword: str | None = None,
    start_date: str | None = "2000-01-01",
    end_date: str | None = None,
    days: int = 90,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    max_shipping_address_orders: int = DEFAULT_MAX_LINE_ITEM_ORDER_SCAN,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    exclude_order_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Summarize customer-facing shipping charges by method using read-only orders."""

    def matches_keyword(value: Any) -> bool:
        return bool(keyword and keyword in str(value or "").lower())

    def shipment_labels(shipment: dict[str, Any]) -> list[str]:
        labels = []
        for field in [
            "shipping_method",
            "shipping_provider",
            "tracking_carrier",
            "generated_tracking_link",
            "shipping_provider_display_name",
        ]:
            value = shipment.get(field)
            if value:
                labels.append(str(value))
        return labels

    included = set(include_statuses or [])
    excluded = set(exclude_statuses or ["Cancelled", "Declined", "Refunded"])
    excluded_order_ids = {int(order_id) for order_id in exclude_order_ids or []}
    orders = _orders_for_analytics_range(start_date, end_date, days, max_orders)
    included_orders = [
        order
        for order in orders
        if (not included or str(order.get("status") or "Unknown") in included)
        and str(order.get("status") or "Unknown") not in excluded
        and int(order.get("id") or 0) not in excluded_order_ids
    ]
    shipping_orders = [
        order
        for order in included_orders
        if float(order.get("shipping_cost_inc_tax") or 0) != 0
        or float(order.get("base_shipping_cost") or 0) != 0
    ]
    address_scan_limit = max(1, min(max_shipping_address_orders, DEFAULT_MAX_LINE_ITEM_ORDER_SCAN))
    scanned_orders = shipping_orders[:address_scan_limit]
    keyword = (method_keyword or "").strip().lower()

    matched_order_ids: set[int] = set()
    matched_address_count = 0
    matched_shipping_total = 0.0
    method_totals: defaultdict[str, float] = defaultdict(float)
    method_counts: Counter[str] = Counter()
    carrier_totals: defaultdict[str, float] = defaultdict(float)
    carrier_counts: Counter[str] = Counter()

    def fetch_shipping_context(
        order: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        order_id = int(order["id"])
        return order, _shipping_addresses_for_order(order_id), _shipments_for_order(order_id)

    if not keyword:
        matched_order_ids.update(int(order["id"]) for order in shipping_orders if order.get("id"))
        matched_shipping_total = sum(
            float(order.get("shipping_cost_inc_tax") or order.get("base_shipping_cost") or 0)
            for order in shipping_orders
        )

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(scanned_orders)))) as executor:
        future_to_order = {
            executor.submit(fetch_shipping_context, order): order
            for order in scanned_orders
            if order.get("id")
        }
        for future in as_completed(future_to_order):
            order, addresses, shipments = future.result()
            address_cost_total = sum(
                float(address.get("cost_inc_tax") or address.get("base_cost") or address.get("cost_ex_tax") or 0)
                for address in addresses
            )
            customer_shipping_total = float(order.get("shipping_cost_inc_tax") or 0) or address_cost_total
            matched_methods: set[str] = set()
            matched_carriers: set[str] = set()

            for address in addresses:
                method = str(address.get("shipping_method") or "Unknown")
                if keyword and not matches_keyword(method):
                    continue
                matched_address_count += 1
                matched_methods.add(method)
                method_counts[method] += 1

            for shipment in shipments:
                labels = shipment_labels(shipment)
                if keyword and not any(matches_keyword(label) for label in labels):
                    continue
                label = (
                    str(shipment.get("tracking_carrier") or "")
                    or str(shipment.get("shipping_provider") or "")
                    or str(shipment.get("shipping_provider_display_name") or "")
                    or str(shipment.get("shipping_method") or "Unknown carrier")
                )
                matched_carriers.add(label)
                carrier_counts[label] += 1

            order_matched = bool(matched_methods or matched_carriers)
            if not keyword and addresses:
                order_matched = True
                matched_methods.update(str(address.get("shipping_method") or "Unknown") for address in addresses)

            if order_matched:
                matched_order_ids.add(int(order["id"]))
                if keyword:
                    matched_shipping_total += customer_shipping_total
                if matched_methods:
                    method_share = customer_shipping_total / len(matched_methods)
                    for method in matched_methods:
                        method_totals[method] += method_share
                carrier_share = customer_shipping_total / max(1, len(matched_carriers))
                for carrier in matched_carriers:
                    carrier_totals[carrier] += carrier_share

    return {
        "method_keyword": method_keyword,
        "start_date": start_date,
        "end_date": end_date,
        "days": days if not start_date else None,
        "orders_analyzed": len(orders),
        "included_order_count": len(included_orders),
        "shipping_cost_order_count": len(shipping_orders),
        "shipping_address_orders_scanned": len(scanned_orders),
        "shipping_address_scan_limit": address_scan_limit,
        "shipping_address_scan_truncated": len(shipping_orders) > address_scan_limit,
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "included_statuses": sorted(included),
        "excluded_statuses": sorted(excluded),
        "excluded_order_ids": sorted(excluded_order_ids),
        "matched_order_count": len(matched_order_ids),
        "matched_shipping_address_count": matched_address_count,
        "matched_shipping_total_inc_tax": round(matched_shipping_total, 2),
        "metric_basis": (
            "sum of customer-facing order shipping_cost_inc_tax/address shipping costs for orders "
            f"whose shipping address method or shipment carrier/provider matches {method_keyword!r}; "
            "cancelled/declined/refunded orders excluded; not carrier invoice spend"
            if method_keyword
            else "sum of customer-facing order shipping_cost_inc_tax/address shipping costs by shipping method; cancelled/declined/refunded orders excluded; not carrier invoice spend"
        ),
        "top_methods": [
            {
                "shipping_method": method,
                "shipping_total_inc_tax": round(total, 2),
                "address_count": method_counts[method],
            }
            for method, total in sorted(method_totals.items(), key=lambda item: item[1], reverse=True)[:25]
        ],
        "matched_carriers": [
            {
                "carrier_or_provider": carrier,
                "shipping_total_inc_tax": round(total, 2),
                "shipment_count": carrier_counts[carrier],
            }
            for carrier, total in sorted(carrier_totals.items(), key=lambda item: item[1], reverse=True)[:25]
        ],
        "order_ids_sample": sorted(matched_order_ids, reverse=True)[:25],
    }


def get_shipping_charge_total(
    start_date: str | None = "2000-01-01",
    end_date: str | None = None,
    days: int = 90,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    exclude_order_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Return total customer-facing shipping charges without address/shipment fan-out."""

    included = set(include_statuses or [])
    excluded = set(exclude_statuses or ["Cancelled", "Declined", "Refunded"])
    excluded_order_ids = {int(order_id) for order_id in exclude_order_ids or []}
    orders = _orders_for_analytics_range(start_date, end_date, days, max_orders)
    included_orders = [
        order
        for order in orders
        if (not included or str(order.get("status") or "Unknown") in included)
        and str(order.get("status") or "Unknown") not in excluded
        and int(order.get("id") or 0) not in excluded_order_ids
    ]
    shipping_orders = [
        order
        for order in included_orders
        if float(order.get("shipping_cost_inc_tax") or order.get("base_shipping_cost") or 0) != 0
    ]
    total = sum(
        float(order.get("shipping_cost_inc_tax") or order.get("base_shipping_cost") or 0)
        for order in included_orders
    )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days if not start_date else None,
        "orders_analyzed": len(orders),
        "included_order_count": len(included_orders),
        "shipping_cost_order_count": len(shipping_orders),
        "matched_order_count": len(included_orders),
        "matched_shipping_total_inc_tax": round(total, 2),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "included_statuses": sorted(included),
        "excluded_statuses": sorted(excluded),
        "excluded_order_ids": sorted(excluded_order_ids),
        "metric_basis": "sum of customer-facing order shipping_cost_inc_tax for filtered orders; not carrier invoice spend",
    }


def _parse_bc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _order_datetime(order: dict[str, Any]) -> datetime | None:
    return _parse_bc_datetime(order.get("date_created"))


def _order_date_shipped(order: dict[str, Any]) -> datetime | None:
    return _parse_bc_datetime(order.get("date_shipped"))


def _order_date_modified(order: dict[str, Any]) -> datetime | None:
    return _parse_bc_datetime(order.get("date_modified"))


def _date_bucket(order: dict[str, Any], bucket: str) -> str:
    parsed = _order_datetime(order)
    if not parsed:
        return "Unknown"
    if bucket == "day":
        return parsed.strftime("%Y-%m-%d")
    if bucket == "week":
        year, week, _ = parsed.isocalendar()
        return f"{year}-W{week:02d}"
    if bucket == "quarter":
        quarter = ((parsed.month - 1) // 3) + 1
        return f"{parsed.year}-Q{quarter}"
    return parsed.strftime("%Y-%m")


def _age_parts(start: datetime, end: datetime | None = None) -> dict[str, Any]:
    finish = end or datetime.now(timezone.utc)
    seconds = max(0, int((finish - start).total_seconds()))
    return {
        "seconds": seconds,
        "hours": round(seconds / 3600, 2),
        "days": round(seconds / 86400, 2),
    }


def _shipment_datetime(shipment: dict[str, Any]) -> datetime | None:
    for field in ["date_created", "date_modified"]:
        parsed = _parse_bc_datetime(shipment.get(field))
        if parsed:
            return parsed
    return None


def _orders_for_analytics_range(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 90,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
) -> list[dict[str, Any]]:
    min_date_created = _iso_date_start(start_date) if start_date else _iso_days_ago(days)
    max_date_created = _iso_date_end_exclusive(end_date) if end_date else None
    return _list_orders_for_analysis(
        min_date_created=min_date_created,
        max_date_created=max_date_created,
        max_orders=max_orders,
    )


def _matches_optional_terms(label: str, query: str | None) -> bool:
    if not query:
        return True
    terms = _search_terms(query)
    return any(_matches_fuzzy_term(_normalize_text(label), term) for term in terms)


def _product_matches_analytics_filter(
    product: dict[str, Any],
    product_keyword: str | None = None,
    product_group: str | None = None,
    brand: str | None = None,
) -> bool:
    if product_keyword and not _product_matches_keyword(product, product_keyword):
        return False

    if product_group and brand:
        return product_matches_group_brand(product, product_group, brand)

    if product_group:
        return any(
            product_matches_group_brand(product, product_group, group_brand)
            for group_brand in brands_for_group(product_group)
        )

    if brand:
        blob = _normalize_text(
            " ".join(str(product.get(field) or "") for field in ["name", "sku", "product_id"])
        )
        return phrase_matches(blob, brand)

    return True


def _filter_orders_for_analytics(
    orders: list[dict[str, Any]],
    dimension: str | None = None,
    value: str | None = None,
    placed_by: str | None = None,
    billing_contact: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    excluded = set(exclude_statuses or ["Cancelled", "Declined", "Refunded"])
    included = set(include_statuses or [])
    customer_lookup = _customer_lookup_for_orders(orders)
    filtered = []

    for order in orders:
        status = str(order.get("status") or "Unknown")
        if included and status not in included:
            continue
        if status in excluded:
            continue
        if dimension and value and not _order_matches_dimension(order, dimension, value):
            continue
        if placed_by and not _matches_optional_terms(
            _placed_by_label(order, customer_lookup), placed_by
        ):
            continue
        if billing_contact and not _matches_optional_terms(
            _billing_contact_label(order), billing_contact
        ):
            continue
        filtered.append(order)

    return filtered, customer_lookup


def _analytics_order_summary(
    order: dict[str, Any],
    customer_lookup: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "order_id": order.get("id"),
        "date_created": order.get("date_created"),
        "status": order.get("status"),
        "placed_by": _placed_by_label(order, customer_lookup),
        "billing_contact": _billing_contact_label(order),
        "college_unit": _unit_label(order),
        "department_code": _department_code_label(order),
        "account_numbers": _dimension_values(order, "account_number"),
        "recipient_names_or_uins": _dimension_values(order, "recipient"),
        "total_inc_tax": _money(order.get("total_inc_tax")),
        "items_total": int(order.get("items_total") or 0),
    }


def _analytics_base_result(
    orders: list[dict[str, Any]],
    filtered_orders: list[dict[str, Any]],
    start_date: str | None,
    end_date: str | None,
    days: int,
    max_orders: int,
    exclude_statuses: list[str] | None,
) -> dict[str, Any]:
    total_revenue = sum(float(order.get("total_inc_tax") or 0) for order in filtered_orders)
    order_count = len(filtered_orders)
    item_quantity = sum(int(order.get("items_total") or 0) for order in filtered_orders)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days if not start_date else None,
        "orders_analyzed": len(orders),
        "matching_order_count": order_count,
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "excluded_statuses": exclude_statuses or ["Cancelled", "Declined", "Refunded"],
        "total_revenue_inc_tax": round(total_revenue, 2),
        "item_quantity": item_quantity,
        "average_order_value_inc_tax": round(total_revenue / order_count, 2)
        if order_count
        else 0,
        "status_counts": dict(Counter(str(order.get("status") or "Unknown") for order in filtered_orders).most_common()),
    }


def get_order_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 90,
    dimension: str | None = None,
    value: str | None = None,
    product_keyword: str | None = None,
    product_group: str | None = None,
    brand: str | None = None,
    placed_by: str | None = None,
    billing_contact: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    max_line_item_orders: int = DEFAULT_MAX_LINE_ITEM_ORDER_SCAN,
) -> dict[str, Any]:
    """Summarize filtered orders with optional product filters."""

    if dimension and dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    orders = _orders_for_analytics_range(start_date, end_date, days, max_orders)
    filtered_orders, customer_lookup = _filter_orders_for_analytics(
        orders,
        dimension=dimension,
        value=value,
        placed_by=placed_by,
        billing_contact=billing_contact,
        include_statuses=include_statuses,
        exclude_statuses=exclude_statuses,
    )

    product_filter = bool(product_keyword or product_group or brand)
    matching_order_ids: set[int] = set()
    matching_line_quantity = 0
    matching_line_total = 0.0
    top_products: Counter[str] = Counter()
    product_candidate_order_count = len(filtered_orders)

    if product_filter:
        effective_line_item_limit = max(1, min(max_line_item_orders, DEFAULT_MAX_LINE_ITEM_ORDER_SCAN))
        line_item_orders = filtered_orders[:effective_line_item_limit]
        for order, products in _products_for_orders(line_item_orders):
            matched_any = False
            for product in products:
                if not _product_matches_analytics_filter(product, product_keyword, product_group, brand):
                    continue
                quantity = int(product.get("quantity") or 0)
                if quantity <= 0:
                    continue
                matched_any = True
                matching_line_quantity += quantity
                matching_line_total += float(product.get("total_inc_tax") or 0)
                top_products[str(product.get("name") or "Unknown product")] += quantity
            if matched_any:
                matching_order_ids.add(int(order["id"]))

        filtered_orders = [
            order for order in filtered_orders if int(order.get("id") or 0) in matching_order_ids
        ]

    result = _analytics_base_result(
        orders, filtered_orders, start_date, end_date, days, max_orders, exclude_statuses
    )
    result.update(
        {
            "filters": {
                "dimension": dimension,
                "value": value,
                "product_keyword": product_keyword,
                "product_group": product_group,
                "brand": brand,
                "placed_by": placed_by,
                "billing_contact": billing_contact,
                "include_statuses": include_statuses,
                "exclude_statuses": exclude_statuses or ["Cancelled", "Declined", "Refunded"],
            },
            "metric_basis": (
                "order totals for orders matching filters; product_* metrics use matching line items"
                if product_filter
                else "order totals for orders matching filters"
            ),
            "matching_product_quantity": matching_line_quantity if product_filter else None,
            "matching_product_revenue_inc_tax": round(matching_line_total, 2)
            if product_filter
            else None,
            "line_item_orders_scanned": len(line_item_orders) if product_filter else 0,
            "line_item_scan_limit": effective_line_item_limit if product_filter else None,
            "line_item_scan_truncated": product_filter
            and product_candidate_order_count > effective_line_item_limit,
            "top_matching_products": [
                {"name": name, "quantity_sold": quantity}
                for name, quantity in top_products.most_common(10)
            ],
            "order_ids_sample": sorted(
                [int(order["id"]) for order in filtered_orders if order.get("id")],
                reverse=True,
            )[:25],
        }
    )
    return result


def _analytics_group_values(
    order: dict[str, Any],
    group_by: str,
    customer_lookup: dict[int, dict[str, Any]],
) -> list[str]:
    if group_by in {"day", "week", "month", "quarter"}:
        return [_date_bucket(order, group_by)]
    if group_by == "status":
        return [str(order.get("status") or "Unknown")]
    if group_by == "placed_by":
        return [_placed_by_label(order, customer_lookup)]
    if group_by == "billing_contact":
        return [_billing_contact_label(order)]
    if group_by == "college_unit":
        return [_unit_label(order)]
    if group_by == "department_code":
        return [_department_code_label(order)]
    if group_by in {"account_number", "recipient"}:
        values = _dimension_values(order, group_by)
        return values or ["Unknown"]
    return ["Unknown"]


def get_grouped_order_summary(
    group_by: str,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 90,
    dimension: str | None = None,
    value: str | None = None,
    product_keyword: str | None = None,
    product_group: str | None = None,
    brand: str | None = None,
    placed_by: str | None = None,
    billing_contact: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    limit: int = 25,
    sort_by: str = "revenue",
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    max_line_item_orders: int = DEFAULT_MAX_LINE_ITEM_ORDER_SCAN,
) -> dict[str, Any]:
    """Summarize filtered orders grouped by time, status, checkout fields, people, or products."""

    allowed = {
        "day",
        "week",
        "month",
        "quarter",
        "status",
        "college_unit",
        "department_code",
        "account_number",
        "recipient",
        "placed_by",
        "billing_contact",
        "product_name",
        "product_brand",
    }
    if group_by not in allowed:
        raise ValueError(f"Unknown group_by: {group_by}")
    if sort_by not in {"revenue", "orders", "items"}:
        raise ValueError(f"Unknown sort_by: {sort_by}")
    if dimension and dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    orders = _orders_for_analytics_range(start_date, end_date, days, max_orders)
    filtered_orders, customer_lookup = _filter_orders_for_analytics(
        orders,
        dimension=dimension,
        value=value,
        placed_by=placed_by,
        billing_contact=billing_contact,
        include_statuses=include_statuses,
        exclude_statuses=exclude_statuses,
    )

    product_filter = bool(product_keyword or product_group or brand)
    product_grouping = group_by in {"product_name", "product_brand"}
    product_candidate_order_count = len(filtered_orders)
    revenue_by_group: defaultdict[str, float] = defaultdict(float)
    order_ids_by_group: defaultdict[str, set[int]] = defaultdict(set)
    item_quantity_by_group: Counter[str] = Counter()

    if product_filter or product_grouping:
        effective_line_item_limit = max(1, min(max_line_item_orders, DEFAULT_MAX_LINE_ITEM_ORDER_SCAN))
        line_item_orders = filtered_orders[:effective_line_item_limit]
        for order, products in _products_for_orders(line_item_orders):
            order_id = int(order["id"])
            for product in products:
                if not _product_matches_analytics_filter(product, product_keyword, product_group, brand):
                    continue
                quantity = int(product.get("quantity") or 0)
                if quantity <= 0:
                    continue
                if group_by == "product_name":
                    groups = [str(product.get("name") or "Unknown product")]
                elif group_by == "product_brand":
                    groups = [
                        matched_brand
                        for matched_brand in brands_for_group(product_group or "computers")
                        if product_matches_group_brand(product, product_group or "computers", matched_brand)
                    ] or ["Unknown"]
                else:
                    groups = _analytics_group_values(order, group_by, customer_lookup)
                share = 1 / len(groups) if groups else 1
                for group in groups:
                    revenue_by_group[group] += float(product.get("total_inc_tax") or 0) * share
                    item_quantity_by_group[group] += quantity
                    order_ids_by_group[group].add(order_id)
    else:
        for order in filtered_orders:
            groups = _analytics_group_values(order, group_by, customer_lookup)
            share = 1 / len(groups) if groups else 1
            for group in groups:
                revenue_by_group[group] += float(order.get("total_inc_tax") or 0) * share
                item_quantity_by_group[group] += int(order.get("items_total") or 0)
                order_ids_by_group[group].add(int(order["id"]))

    if group_by in {"day", "week", "month", "quarter"}:
        sorted_groups = sorted(revenue_by_group.items(), key=lambda item: item[0])
    elif sort_by == "items":
        sorted_groups = sorted(
            revenue_by_group.items(),
            key=lambda item: (-item_quantity_by_group[item[0]], -item[1], item[0]),
        )
    elif sort_by == "orders":
        sorted_groups = sorted(
            revenue_by_group.items(),
            key=lambda item: (-len(order_ids_by_group[item[0]]), -item[1], item[0]),
        )
    else:
        sorted_groups = sorted(
            revenue_by_group.items(), key=lambda item: (-item[1], item[0])
        )
    selected = sorted_groups[: min(max(limit, 1), 100)]
    total_revenue = sum(revenue_by_group.values())
    total_orders = len({order_id for ids in order_ids_by_group.values() for order_id in ids})
    total_items = sum(item_quantity_by_group.values())

    groups = []
    for group, revenue in selected:
        order_count = len(order_ids_by_group[group])
        item_quantity = item_quantity_by_group[group]
        groups.append(
            {
                "group": group,
                "total_inc_tax": round(revenue, 2),
                "revenue_percentage": round(revenue / total_revenue * 100, 2)
                if total_revenue
                else 0,
                "order_count": order_count,
                "order_percentage": round(order_count / total_orders * 100, 2)
                if total_orders
                else 0,
                "item_quantity": item_quantity,
                "item_quantity_percentage": round(item_quantity / total_items * 100, 2)
                if total_items
                else 0,
                "order_ids_sample": sorted(order_ids_by_group[group], reverse=True)[:10],
            }
        )

    return {
        "group_by": group_by,
        "sort_by": sort_by,
        "start_date": start_date,
        "end_date": end_date,
        "days": days if not start_date else None,
        "orders_analyzed": len(orders),
        "matching_order_count": total_orders,
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "line_item_orders_scanned": len(line_item_orders) if product_filter or product_grouping else 0,
        "line_item_scan_limit": effective_line_item_limit if product_filter or product_grouping else None,
        "line_item_scan_truncated": (product_filter or product_grouping)
        and product_candidate_order_count > effective_line_item_limit,
        "filters": {
            "dimension": dimension,
            "value": value,
            "product_keyword": product_keyword,
            "product_group": product_group,
            "brand": brand,
            "placed_by": placed_by,
            "billing_contact": billing_contact,
            "include_statuses": include_statuses,
            "exclude_statuses": exclude_statuses or ["Cancelled", "Declined", "Refunded"],
        },
        "metric_basis": (
            "matching line-item totals"
            if product_filter or product_grouping
            else "order total_inc_tax"
        ),
        "total_revenue_inc_tax": round(total_revenue, 2),
        "total_item_quantity": total_items,
        "displayed_group_count": len(groups),
        "total_group_count": len(sorted_groups),
        "groups": groups,
    }


def get_ranked_orders(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
    sort_by: str = "total_inc_tax",
    direction: str = "desc",
    limit: int = 10,
    dimension: str | None = None,
    value: str | None = None,
    placed_by: str | None = None,
    billing_contact: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
) -> dict[str, Any]:
    """Return orders ranked by a selected order-level metric for a filtered date range."""

    sort_fields = {"total_inc_tax", "date_created", "order_id", "items_total"}
    if sort_by not in sort_fields:
        raise ValueError(f"sort_by must be one of: {', '.join(sorted(sort_fields))}")
    if direction not in {"asc", "desc"}:
        raise ValueError("direction must be 'asc' or 'desc'")
    if dimension and dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    effective_start_date = start_date or ("2000-01-01" if days is None else None)
    effective_days = days or 90
    orders = _orders_for_analytics_range(
        effective_start_date, end_date, effective_days, max_orders
    )
    filtered_orders, customer_lookup = _filter_orders_for_analytics(
        orders,
        dimension=dimension,
        value=value,
        placed_by=placed_by,
        billing_contact=billing_contact,
        include_statuses=include_statuses,
        exclude_statuses=exclude_statuses,
    )

    def rank_value(order: dict[str, Any]) -> int | float | datetime | None:
        if sort_by == "date_created":
            return _order_datetime(order)
        if sort_by == "order_id":
            try:
                return int(order.get("id") or 0)
            except (TypeError, ValueError):
                return None
        if sort_by == "items_total":
            try:
                return int(order.get("items_total") or 0)
            except (TypeError, ValueError):
                return None
        try:
            return float(order.get("total_inc_tax") or 0)
        except (TypeError, ValueError):
            return None

    ranked_values: list[tuple[int | float | datetime, int, dict[str, Any]]] = []
    missing_value_orders: list[dict[str, Any]] = []
    for order in filtered_orders:
        value_to_rank = rank_value(order)
        if value_to_rank is None:
            missing_value_orders.append(order)
            continue
        try:
            order_id = int(order.get("id") or 0)
        except (TypeError, ValueError):
            order_id = 0
        ranked_values.append((value_to_rank, order_id, order))

    ranked_values.sort(
        key=lambda item: (item[0], item[1]),
        reverse=direction == "desc",
    )
    ranked = [item[2] for item in ranked_values] + missing_value_orders
    selected = ranked[: min(max(limit, 1), 100)]

    return {
        "start_date": effective_start_date,
        "end_date": end_date,
        "days": effective_days if not effective_start_date else None,
        "sort_by": sort_by,
        "direction": direction,
        "orders_analyzed": len(orders),
        "matching_order_count": len(filtered_orders),
        "returned_count": len(selected),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "filters": {
            "dimension": dimension,
            "value": value,
            "placed_by": placed_by,
            "billing_contact": billing_contact,
            "include_statuses": include_statuses,
            "exclude_statuses": exclude_statuses or ["Cancelled", "Declined", "Refunded"],
        },
        "orders": [_analytics_order_summary(order, customer_lookup) for order in selected],
    }


def get_product_sales_leaderboard(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 90,
    product_keyword: str | None = None,
    product_group: str | None = None,
    brand: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
    placed_by: str | None = None,
    billing_contact: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    limit: int = 10,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    max_line_item_orders: int = DEFAULT_MAX_LINE_ITEM_ORDER_SCAN,
) -> dict[str, Any]:
    """Rank matching products by quantity sold and include each product's top source order."""

    if dimension and dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    orders = _orders_for_analytics_range(start_date, end_date, days, max_orders)
    filtered_orders, customer_lookup = _filter_orders_for_analytics(
        orders,
        dimension=dimension,
        value=value,
        placed_by=placed_by,
        billing_contact=billing_contact,
        include_statuses=include_statuses,
        exclude_statuses=exclude_statuses,
    )

    effective_line_item_limit = max(1, min(max_line_item_orders, DEFAULT_MAX_LINE_ITEM_ORDER_SCAN))
    scanned_orders = filtered_orders[:effective_line_item_limit]
    product_candidate_order_count = len(filtered_orders)

    quantity_by_product: Counter[str] = Counter()
    revenue_by_product: defaultdict[str, float] = defaultdict(float)
    order_ids_by_product: defaultdict[str, set[int]] = defaultdict(set)
    sku_by_product: defaultdict[str, Counter[str]] = defaultdict(Counter)
    per_order_by_product: dict[tuple[str, int], dict[str, Any]] = {}
    overall_order_totals: dict[int, dict[str, Any]] = {}

    for order, products in _products_for_orders(scanned_orders):
        order_id = int(order["id"])
        for product in products:
            if not _product_matches_analytics_filter(product, product_keyword, product_group, brand):
                continue
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue

            product_name = str(product.get("name") or "Unknown product")
            line_total = float(product.get("total_inc_tax") or 0)
            quantity_by_product[product_name] += quantity
            revenue_by_product[product_name] += line_total
            order_ids_by_product[product_name].add(order_id)
            sku = str(product.get("sku") or "").strip()
            if sku:
                sku_by_product[product_name][sku] += quantity

            row = per_order_by_product.setdefault(
                (product_name, order_id),
                {"order": order, "quantity": 0, "line_total": 0.0},
            )
            row["quantity"] += quantity
            row["line_total"] += line_total

            overall = overall_order_totals.setdefault(
                order_id,
                {"order": order, "quantity": 0, "line_total": 0.0},
            )
            overall["quantity"] += quantity
            overall["line_total"] += line_total

    def top_order_for_product(product_name: str) -> dict[str, Any] | None:
        rows = [
            row
            for (name, _order_id), row in per_order_by_product.items()
            if name == product_name
        ]
        if not rows:
            return None
        rows.sort(
            key=lambda row: (
                int(row["quantity"]),
                float(row["line_total"]),
                int(row["order"].get("id") or 0),
            ),
            reverse=True,
        )
        top = rows[0]
        summary = _analytics_order_summary(top["order"], customer_lookup)
        summary.update(
            {
                "matching_quantity": top["quantity"],
                "matching_line_total_inc_tax": round(top["line_total"], 2),
            }
        )
        return summary

    sorted_products = sorted(
        quantity_by_product.keys(),
        key=lambda name: (
            -quantity_by_product[name],
            -revenue_by_product[name],
            name,
        ),
    )
    selected_products = sorted_products[: min(max(limit, 1), 50)]
    products = []
    for product_name in selected_products:
        skus = [sku for sku, _quantity in sku_by_product[product_name].most_common(5)]
        products.append(
            {
                "product_name": product_name,
                "skus": skus,
                "quantity_sold": quantity_by_product[product_name],
                "total_inc_tax": round(revenue_by_product[product_name], 2),
                "order_count": len(order_ids_by_product[product_name]),
                "order_ids_sample": sorted(order_ids_by_product[product_name], reverse=True)[:10],
                "top_order": top_order_for_product(product_name),
            }
        )

    overall_top_order = None
    if overall_order_totals:
        top_overall = max(
            overall_order_totals.values(),
            key=lambda row: (
                int(row["quantity"]),
                float(row["line_total"]),
                int(row["order"].get("id") or 0),
            ),
        )
        overall_top_order = _analytics_order_summary(top_overall["order"], customer_lookup)
        overall_top_order.update(
            {
                "matching_quantity": top_overall["quantity"],
                "matching_line_total_inc_tax": round(top_overall["line_total"], 2),
            }
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days if not start_date else None,
        "orders_analyzed": len(orders),
        "matching_order_count": len(
            {order_id for order_ids in order_ids_by_product.values() for order_id in order_ids}
        ),
        "returned_count": len(products),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "line_item_orders_scanned": len(scanned_orders),
        "line_item_scan_limit": effective_line_item_limit,
        "line_item_scan_truncated": product_candidate_order_count > effective_line_item_limit,
        "filters": {
            "dimension": dimension,
            "value": value,
            "product_keyword": product_keyword,
            "product_group": product_group,
            "brand": brand,
            "placed_by": placed_by,
            "billing_contact": billing_contact,
            "include_statuses": include_statuses,
            "exclude_statuses": exclude_statuses or ["Cancelled", "Declined", "Refunded"],
        },
        "products": products,
        "overall_top_order": overall_top_order,
    }


def get_source_orders_for_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 90,
    dimension: str | None = None,
    value: str | None = None,
    product_keyword: str | None = None,
    product_group: str | None = None,
    brand: str | None = None,
    placed_by: str | None = None,
    billing_contact: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
    limit: int = 50,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
    max_line_item_orders: int = DEFAULT_MAX_LINE_ITEM_ORDER_SCAN,
) -> dict[str, Any]:
    """Return auditable source orders and matching line items for a filtered summary."""

    if dimension and dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    orders = _orders_for_analytics_range(start_date, end_date, days, max_orders)
    filtered_orders, customer_lookup = _filter_orders_for_analytics(
        orders,
        dimension=dimension,
        value=value,
        placed_by=placed_by,
        billing_contact=billing_contact,
        include_statuses=include_statuses,
        exclude_statuses=exclude_statuses,
    )
    product_filter = bool(product_keyword or product_group or brand)
    product_candidate_order_count = len(filtered_orders)
    rows = []

    if product_filter:
        effective_line_item_limit = max(1, min(max_line_item_orders, DEFAULT_MAX_LINE_ITEM_ORDER_SCAN))
        line_item_orders = filtered_orders[:effective_line_item_limit]
        for order, products in _products_for_orders(line_item_orders):
            matching_products = [
                product
                for product in products
                if _product_matches_analytics_filter(product, product_keyword, product_group, brand)
            ]
            if not matching_products:
                continue
            summary = _analytics_order_summary(order, customer_lookup)
            summary["matching_products"] = matching_products
            rows.append(summary)
    else:
        rows = [_analytics_order_summary(order, customer_lookup) for order in filtered_orders]

    rows = sorted(rows, key=lambda row: int(row.get("order_id") or 0), reverse=True)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days if not start_date else None,
        "orders_analyzed": len(orders),
        "matching_order_count": len(rows),
        "returned_count": min(len(rows), limit),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "line_item_orders_scanned": len(line_item_orders) if product_filter else 0,
        "line_item_scan_limit": effective_line_item_limit if product_filter else None,
        "line_item_scan_truncated": product_filter and product_candidate_order_count > effective_line_item_limit,
        "filters": {
            "dimension": dimension,
            "value": value,
            "product_keyword": product_keyword,
            "product_group": product_group,
            "brand": brand,
            "placed_by": placed_by,
            "billing_contact": billing_contact,
            "include_statuses": include_statuses,
            "exclude_statuses": exclude_statuses or ["Cancelled", "Declined", "Refunded"],
        },
        "orders": rows[: min(max(limit, 1), 250)],
    }


def get_oldest_unfulfilled_orders(
    days: int = 180,
    limit: int = 10,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
) -> dict[str, Any]:
    """Return oldest currently unfulfilled orders and common items among them."""

    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    now = datetime.now(timezone.utc)
    open_orders = []

    for order in orders:
        status = str(order.get("status") or "Unknown")
        if status not in OPEN_FULFILLMENT_STATUSES:
            continue
        created = _order_datetime(order)
        if not created:
            continue
        age = _age_parts(created, now)
        open_orders.append(
            {
                "order": order,
                "age_seconds": age["seconds"],
                "age_hours": age["hours"],
                "age_days": age["days"],
            }
        )

    open_orders = sorted(open_orders, key=lambda item: (-item["age_seconds"], int(item["order"].get("id") or 0)))
    selected = open_orders[: min(max(limit, 1), 100)]
    product_counter: Counter[str] = Counter()
    product_revenue: defaultdict[str, float] = defaultdict(float)
    selected_order_ids = {int(item["order"]["id"]) for item in selected if item["order"].get("id")}

    for order, products in _products_for_orders([item["order"] for item in selected]):
        for product in products:
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue
            name = str(product.get("name") or "Unknown product")
            product_counter[name] += quantity
            product_revenue[name] += float(product.get("total_inc_tax") or 0)

    return {
        "days": days,
        "orders_analyzed": len(orders),
        "open_fulfillment_statuses": sorted(OPEN_FULFILLMENT_STATUSES),
        "open_order_count": len(open_orders),
        "returned_count": len(selected),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "orders": [
            {
                "order_id": item["order"].get("id"),
                "date_created": item["order"].get("date_created"),
                "status": item["order"].get("status"),
                "age_hours": item["age_hours"],
                "age_days": item["age_days"],
                "placed_by": _placed_by_label(item["order"], customer_lookup),
                "billing_contact": _billing_contact_label(item["order"]),
                "college_unit": _unit_label(item["order"]),
                "department_code": _department_code_label(item["order"]),
                "items_total": int(item["order"].get("items_total") or 0),
                "total_inc_tax": _money(item["order"].get("total_inc_tax")),
            }
            for item in selected
        ],
        "common_items_on_returned_orders": [
            {
                "name": name,
                "quantity": quantity,
                "total_inc_tax": round(product_revenue[name], 2),
            }
            for name, quantity in product_counter.most_common(10)
        ],
        "selected_order_ids": sorted(selected_order_ids, reverse=True),
    }


def _fulfillment_timing_for_order(order: dict[str, Any]) -> dict[str, Any] | None:
    created = _order_datetime(order)
    if not created:
        return None
    status = str(order.get("status") or "Unknown")
    shipments = _shipments_for_order(int(order["id"]))
    date_shipped = _order_date_shipped(order)
    date_modified = _order_date_modified(order)
    shipment_dates = [
        shipment_date
        for shipment in shipments
        if (shipment_date := _shipment_datetime(shipment)) is not None
    ]
    if date_shipped:
        shipment_dates.append(date_shipped)
    first_shipment = min(shipment_dates) if shipment_dates else None
    completed_at = first_shipment
    basis = "order creation to first shipment/date_shipped"
    if not completed_at and status in FULFILLED_STATUSES:
        completed_at = date_modified
        basis = "order creation to date_modified because no shipment timestamp was found"

    if completed_at:
        duration = _age_parts(created, completed_at)
        return {
            "created_at": created,
            "first_shipment_at": first_shipment,
            "fulfilled_at": completed_at,
            "last_modified_at": date_modified,
            "duration": duration,
            "basis": basis,
            "shipments_found": len(shipments),
            "date_shipped_available": bool(date_shipped),
            "status_timeline_available": False,
        }

    if status in OPEN_FULFILLMENT_STATUSES:
        age = _age_parts(created)
        return {
            "created_at": created,
            "first_shipment_at": None,
            "last_modified_at": date_modified,
            "age": age,
            "basis": "order creation to now because order is not fulfilled",
            "shipments_found": len(shipments),
            "date_shipped_available": bool(date_shipped),
            "status_timeline_available": False,
        }

    return None


def get_fulfillment_aging_report(
    days: int = 180,
    limit: int = 10,
    max_orders: int = DEFAULT_MAX_ORDER_SCAN,
) -> dict[str, Any]:
    """Return longest completed fulfillment durations and oldest currently-open orders."""

    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    open_rows = []
    fulfilled_candidates = [
        order for order in orders if str(order.get("status") or "Unknown") in FULFILLED_STATUSES
    ]
    fulfilled_rows = []

    for order in orders:
        status = str(order.get("status") or "Unknown")
        if status not in OPEN_FULFILLMENT_STATUSES:
            continue
        timing = _fulfillment_timing_for_order(order)
        if not timing or "age" not in timing:
            continue
        open_rows.append({"order": order, **timing})

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(fulfilled_candidates)))) as executor:
        future_to_order = {
            executor.submit(_fulfillment_timing_for_order, order): order
            for order in fulfilled_candidates
        }
        for future in as_completed(future_to_order):
            order = future_to_order[future]
            timing = future.result()
            if timing and "duration" in timing:
                fulfilled_rows.append({"order": order, **timing})

    open_rows = sorted(
        open_rows,
        key=lambda item: (-item["age"]["seconds"], int(item["order"].get("id") or 0)),
    )
    fulfilled_rows = sorted(
        fulfilled_rows,
        key=lambda item: (-item["duration"]["seconds"], int(item["order"].get("id") or 0)),
    )

    selected_open = open_rows[: min(max(limit, 1), 100)]
    selected_fulfilled = fulfilled_rows[: min(max(limit, 1), 100)]
    selected_orders = [item["order"] for item in selected_open + selected_fulfilled]
    product_counter: Counter[str] = Counter()
    product_revenue: defaultdict[str, float] = defaultdict(float)
    for order, products in _products_for_orders(selected_orders):
        for product in products:
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue
            name = str(product.get("name") or "Unknown product")
            product_counter[name] += quantity
            product_revenue[name] += float(product.get("total_inc_tax") or 0)

    def summarize_common(item: dict[str, Any], key: str) -> dict[str, Any]:
        order = item["order"]
        timing = item[key]
        return {
            "order_id": order.get("id"),
            "date_created": order.get("date_created"),
            "date_modified": order.get("date_modified"),
            "status": order.get("status"),
            "placed_by": _placed_by_label(order, customer_lookup),
            "billing_contact": _billing_contact_label(order),
            "college_unit": _unit_label(order),
            "department_code": _department_code_label(order),
            "items_total": int(order.get("items_total") or 0),
            "total_inc_tax": _money(order.get("total_inc_tax")),
            "hours": timing["hours"],
            "days": timing["days"],
            "basis": item.get("basis"),
            "shipments_found": item.get("shipments_found"),
            "fulfilled_at": item.get("fulfilled_at").isoformat() if item.get("fulfilled_at") else None,
        }

    return {
        "days": days,
        "orders_analyzed": len(orders),
        "max_orders": max_orders,
        "is_truncated": len(orders) >= max_orders,
        "fulfilled_statuses": sorted(FULFILLED_STATUSES),
        "open_fulfillment_statuses": sorted(OPEN_FULFILLMENT_STATUSES),
        "fulfilled_candidate_count": len(fulfilled_candidates),
        "fulfilled_order_count_with_timing": len(fulfilled_rows),
        "open_order_count": len(open_rows),
        "returned_fulfilled_count": len(selected_fulfilled),
        "returned_open_count": len(selected_open),
        "longest_fulfilled_orders": [
            summarize_common(item, "duration") for item in selected_fulfilled
        ],
        "oldest_open_orders": [
            summarize_common(item, "age") for item in selected_open
        ],
        "common_items_on_returned_orders": [
            {
                "name": name,
                "quantity": quantity,
                "total_inc_tax": round(product_revenue[name], 2),
            }
            for name, quantity in product_counter.most_common(10)
        ],
    }


def get_order_fulfillment_timing(order_id: int) -> dict[str, Any]:
    """Return fulfillment timing for one order based on creation, status, and shipments."""

    order = _client().get(f"/v2/orders/{int(order_id)}")
    created = _order_datetime(order)
    date_shipped = _order_date_shipped(order)
    date_modified = _order_date_modified(order)
    status = str(order.get("status") or "Unknown")
    timing = _fulfillment_timing_for_order(order)

    if not created:
        age = None
        duration = None
        completed_at = None
        first_shipment_at = None
        shipments_found = 0
        basis = "creation date unavailable"
    elif timing and "duration" in timing:
        age = None
        duration = timing["duration"]
        completed_at = timing.get("fulfilled_at")
        first_shipment_at = timing.get("first_shipment_at")
        shipments_found = int(timing.get("shipments_found") or 0)
        basis = str(timing.get("basis"))
    elif timing and "age" in timing:
        age = timing["age"]
        duration = None
        completed_at = None
        first_shipment_at = timing.get("first_shipment_at")
        shipments_found = int(timing.get("shipments_found") or 0)
        basis = str(timing.get("basis"))
    else:
        age = None
        duration = None
        completed_at = None
        first_shipment_at = None
        shipments_found = 0
        basis = "no fulfillment timing available for this status"

    return {
        "order_id": order.get("id"),
        "date_created": order.get("date_created"),
        "date_modified": order.get("date_modified"),
        "date_shipped": order.get("date_shipped"),
        "status": status,
        "is_currently_unfulfilled": status in OPEN_FULFILLMENT_STATUSES,
        "shipments_found": shipments_found,
        "created_at": created.isoformat() if created else None,
        "first_shipment_at": first_shipment_at.isoformat() if first_shipment_at else None,
        "fulfilled_at": completed_at.isoformat() if completed_at else None,
        "last_modified_at": date_modified.isoformat() if date_modified else None,
        "date_shipped_at": date_shipped.isoformat() if date_shipped else None,
        "age_if_unfulfilled": age,
        "fulfillment_duration": duration,
        "timing_basis": basis,
        "status_timeline_available": False,
        "status_timeline_note": (
            "The current read-only BigCommerce REST data exposes order creation, "
            "date_shipped, date_modified, and shipments, but not the admin control "
            "panel status-change timeline. Exact time spent in statuses such as "
            "Awaiting Verification, AggieBuy Approval, or Awaiting Fulfillment is "
            "not available from this tool unless status history is captured separately."
        ),
    }


def _products_for_orders(
    orders: list[dict[str, Any]], max_workers: int = 12
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    valid_orders = [order for order in orders if order.get("id")]
    if not valid_orders:
        return []

    results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    workers = min(max_workers, len(valid_orders))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_order = {
            executor.submit(get_order_products, int(order["id"])): order
            for order in valid_orders
        }
        for future in as_completed(future_to_order):
            order = future_to_order[future]
            result = future.result()
            results.append((order, result["products"]))

    return results


def get_top_products_sold(days: int = 90, limit: int = 10, max_orders: int = 250) -> dict[str, Any]:
    """Count order line items to find the most-sold products by quantity."""

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 50)
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)

    quantity_by_key: Counter[tuple[str | None, str | None, Any]] = Counter()
    orders_by_key: defaultdict[tuple[str | None, str | None, Any], set[int]] = defaultdict(set)
    revenue_by_key: defaultdict[tuple[str | None, str | None, Any], float] = defaultdict(float)

    for order, products in _products_for_orders(orders):
        for product in products:
            key = (product.get("name"), product.get("sku"), product.get("product_id"))
            quantity = int(product.get("quantity") or 0)
            quantity_by_key[key] += quantity
            orders_by_key[key].add(int(order["id"]))
            revenue_by_key[key] += float(product.get("total_inc_tax") or 0)

    products = []
    for (name, sku, product_id), quantity in quantity_by_key.most_common(limit):
        products.append(
            {
                "name": name,
                "sku": sku,
                "product_id": product_id,
                "quantity_sold": quantity,
                "order_count": len(orders_by_key[(name, sku, product_id)]),
                "total_inc_tax": round(revenue_by_key[(name, sku, product_id)], 2),
            }
        )

    return {
        "days": days,
        "orders_analyzed": len(orders),
        "max_orders": max_orders,
        "count": len(products),
        "products": products,
    }


def get_top_products_sold_to(
    customer_or_department: str,
    days: int = 90,
    limit: int = 10,
    max_orders: int = 250,
) -> dict[str, Any]:
    """Find the most-sold products for orders matching a customer/org term."""

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 50)
    search_terms = _search_terms(customer_or_department)
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    matching_orders = [
        order
        for order in orders
        if any(_matches_fuzzy_term(_order_search_blob(order), term) for term in search_terms)
    ]

    quantity_by_key: Counter[tuple[str | None, str | None, Any]] = Counter()
    orders_by_key: defaultdict[tuple[str | None, str | None, Any], set[int]] = defaultdict(set)
    revenue_by_key: defaultdict[tuple[str | None, str | None, Any], float] = defaultdict(float)

    for order, products in _products_for_orders(matching_orders):
        for product in products:
            key = (product.get("name"), product.get("sku"), product.get("product_id"))
            quantity = int(product.get("quantity") or 0)
            quantity_by_key[key] += quantity
            orders_by_key[key].add(int(order["id"]))
            revenue_by_key[key] += float(product.get("total_inc_tax") or 0)

    products = []
    for (name, sku, product_id), quantity in quantity_by_key.most_common(limit):
        products.append(
            {
                "name": name,
                "sku": sku,
                "product_id": product_id,
                "quantity_sold": quantity,
                "order_count": len(orders_by_key[(name, sku, product_id)]),
                "total_inc_tax": round(revenue_by_key[(name, sku, product_id)], 2),
            }
        )

    return {
        "query": customer_or_department,
        "expanded_terms": sorted(search_terms),
        "days": days,
        "orders_analyzed": len(orders),
        "matching_order_count": len(matching_orders),
        "matching_order_ids": [order.get("id") for order in matching_orders[:25]],
        "max_orders": max_orders,
        "count": len(products),
        "products": products,
    }


def get_top_customers_for_product_keyword(
    keyword: str,
    days: int = 365,
    limit: int = 10,
    max_orders: int = 1000,
    group_by: str = "person",
) -> dict[str, Any]:
    """Find customers/orgs that bought the most line items matching a product keyword."""

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 50)
    group_by = group_by if group_by in {"person", "college_unit"} else "person"
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)

    quantity_by_group: Counter[str] = Counter()
    orders_by_group: defaultdict[str, set[int]] = defaultdict(set)
    revenue_by_group: defaultdict[str, float] = defaultdict(float)
    products_by_group: defaultdict[str, Counter[str]] = defaultdict(Counter)
    related_by_group: defaultdict[str, set[str]] = defaultdict(set)

    for order, products in _products_for_orders(orders):
        group = _placed_by_label(order, customer_lookup) if group_by == "person" else _group_label(order, group_by)
        related = _related_label(order, group_by)

        order_matched = False
        for product in products:
            if not _product_matches_keyword(product, keyword):
                continue

            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue
            product_name = str(product.get("name") or "Unknown product")
            quantity_by_group[group] += quantity
            orders_by_group[group].add(int(order["id"]))
            revenue_by_group[group] += float(product.get("total_inc_tax") or 0)
            products_by_group[group][product_name] += quantity
            order_matched = True

        if order_matched and related and related != "Unknown":
            related_by_group[group].add(related)

    ranked_customers = sorted(
        quantity_by_group.items(),
        key=lambda item: (
            -item[1],
            -len(orders_by_group[item[0]]),
            item[0],
        ),
    )

    customers = []
    label_key = "customer" if group_by == "person" else "college_unit"
    related_key = "college_units" if group_by == "person" else "customers"
    for group, quantity in ranked_customers[:limit]:
        top_products = [
            {"name": name, "quantity_sold": qty}
            for name, qty in products_by_group[group].most_common(5)
        ]
        customers.append(
            {
                label_key: group,
                related_key: sorted(related_by_group[group])[:10],
                "quantity_sold": quantity,
                "order_count": len(orders_by_group[group]),
                "order_ids": sorted(orders_by_group[group], reverse=True)[:10],
                "total_inc_tax": round(revenue_by_group[group], 2),
                "top_matching_products": top_products,
            }
        )

    top_quantity = ranked_customers[0][1] if ranked_customers else 0
    tied_top_customers = [group for group, quantity in ranked_customers if quantity == top_quantity]

    return {
        "keyword": keyword,
        "expanded_terms": sorted(_search_terms(keyword)),
        "group_by": group_by,
        "days": days,
        "orders_analyzed": len(orders),
        "max_orders": max_orders,
        "top_quantity_sold": top_quantity,
        "is_tie_for_top": len(tied_top_customers) > 1,
        "tied_top_customers": tied_top_customers[:limit],
        "count": len(customers),
        "customers": customers,
    }


def get_top_customers_for_product_keyword_in_dimension(
    keyword: str,
    dimension: str,
    value: str,
    days: int = 365,
    limit: int = 10,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Find people who bought the most matching products inside a dimension value."""

    if dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 50)
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    matching_orders = [
        order for order in orders if _order_matches_dimension(order, dimension, value)
    ]

    quantity_by_person: Counter[str] = Counter()
    orders_by_person: defaultdict[str, set[int]] = defaultdict(set)
    revenue_by_person: defaultdict[str, float] = defaultdict(float)
    products_by_person: defaultdict[str, Counter[str]] = defaultdict(Counter)
    related_values_by_person: defaultdict[str, set[str]] = defaultdict(set)

    for order, products in _products_for_orders(matching_orders):
        person = _placed_by_label(order, customer_lookup)
        related_values_by_person[person].update(_dimension_values(order, dimension))

        for product in products:
            if not _product_matches_keyword(product, keyword):
                continue
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue
            product_name = str(product.get("name") or "Unknown product")
            quantity_by_person[person] += quantity
            orders_by_person[person].add(int(order["id"]))
            revenue_by_person[person] += float(product.get("total_inc_tax") or 0)
            products_by_person[person][product_name] += quantity

    ranked_people = sorted(
        quantity_by_person.items(),
        key=lambda item: (-item[1], -len(orders_by_person[item[0]]), item[0]),
    )

    customers = []
    for person, quantity in ranked_people[:limit]:
        customers.append(
            {
                "customer": person,
                "dimension_values": sorted(related_values_by_person[person]),
                "quantity_sold": quantity,
                "order_count": len(orders_by_person[person]),
                "order_ids": sorted(orders_by_person[person], reverse=True)[:10],
                "total_inc_tax": round(revenue_by_person[person], 2),
                "top_matching_products": [
                    {"name": name, "quantity_sold": qty}
                    for name, qty in products_by_person[person].most_common(5)
                ],
            }
        )

    top_quantity = ranked_people[0][1] if ranked_people else 0
    tied_top_customers = [
        person for person, quantity in ranked_people if quantity == top_quantity
    ]

    return {
        "keyword": keyword,
        "expanded_product_terms": sorted(_search_terms(keyword)),
        "dimension": dimension,
        "dimension_label": _dimension_label(dimension),
        "source_field": _dimension_field(dimension),
        "dimension_query": value,
        "expanded_dimension_terms": sorted(_dimension_search_terms(dimension, value)),
        "days": days,
        "orders_analyzed": len(orders),
        "matching_order_count": len(matching_orders),
        "max_orders": max_orders,
        "top_quantity_sold": top_quantity,
        "is_tie_for_top": len(tied_top_customers) > 1,
        "tied_top_customers": tied_top_customers[:limit],
        "count": len(customers),
        "customers": customers,
    }


def get_product_keyword_order_lines_for_customer(
    customer: str,
    keyword: str,
    days: int = 365,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Return source order lines for a person/customer and product keyword."""

    days = max(1, min(days, 365))
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    customer_terms = _search_terms(customer)
    matching_orders = [
        order
        for order in orders
        if any(
            _matches_fuzzy_term(_normalize_text(_placed_by_label(order, customer_lookup)), term)
            for term in customer_terms
        )
    ]

    lines = []
    total_quantity = 0
    for order, products in _products_for_orders(matching_orders):
        for product in products:
            if not _product_matches_keyword(product, keyword):
                continue

            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue

            total_quantity += quantity
            lines.append(
                {
                    "order_id": order.get("id"),
                    "date_created": order.get("date_created"),
                    "placed_by": _placed_by_label(order, customer_lookup),
                    "billing_contact": _billing_contact_label(order),
                    "college_unit": _unit_label(order),
                    "product_name": product.get("name"),
                    "sku": product.get("sku"),
                    "quantity": quantity,
                    "total_inc_tax": product.get("total_inc_tax"),
                }
            )

    return {
        "customer_query": customer,
        "keyword": keyword,
        "expanded_customer_terms": sorted(customer_terms),
        "expanded_product_terms": sorted(_search_terms(keyword)),
        "days": days,
        "matching_order_count": len({line["order_id"] for line in lines}),
        "total_quantity": total_quantity,
        "lines": sorted(lines, key=lambda line: (str(line["date_created"]), int(line["order_id"] or 0)), reverse=True),
    }


def get_full_order_contents(
    order_ids: list[int],
    limit: int = 50,
) -> dict[str, Any]:
    """Return every product line on specific orders."""

    selected_order_ids = [int(order_id) for order_id in order_ids[:limit]]
    fetched_orders = [_client().get(f"/v2/orders/{order_id}") for order_id in selected_order_ids]
    customer_lookup = _customer_lookup_for_orders(fetched_orders)
    orders = []
    for order in fetched_orders:
        order_id = int(order["id"])
        products = get_order_products(order_id)["products"]
        orders.append(
            {
                "order": {
                    "id": order.get("id"),
                    "date_created": order.get("date_created"),
                    "status": order.get("status"),
                    "placed_by": _placed_by_label(order, customer_lookup),
                    "billing_contact": _billing_contact_label(order),
                    "college_unit": _unit_label(order),
                    "department_code": _department_code_label(order),
                    "recipient": {
                        "college_unit": _primary_dimension_value(order, "college_unit"),
                        "department_code": _primary_dimension_value(order, "department_code"),
                        "account_numbers": _dimension_values(order, "account_number"),
                        "recipient_names_or_uins": _dimension_values(order, "recipient"),
                    },
                    "shipping_addresses": [
                        _summarize_address(address)
                        for address in _shipping_addresses_for_order(order_id)
                    ],
                    "total_inc_tax": _money(order.get("total_inc_tax")),
                },
                "products": products,
            }
        )

    return {
        "requested_order_ids": selected_order_ids,
        "returned_count": len(orders),
        "orders": orders,
    }


def get_full_order_contents_for_customer_product_in_dimension(
    customer: str,
    keyword: str,
    dimension: str,
    value: str,
    days: int = 365,
    limit: int = 50,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Return all line items for orders matching customer, product keyword, and dimension."""

    if dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 100)
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    customer_terms = _search_terms(customer)
    candidate_orders = [
        order
        for order in orders
        if _order_matches_dimension(order, dimension, value)
        and any(
            _matches_fuzzy_term(_normalize_text(_placed_by_label(order, customer_lookup)), term)
            for term in customer_terms
        )
    ]

    matched_orders = []
    for order, products in _products_for_orders(candidate_orders):
        matching_products = [
            product for product in products if _product_matches_keyword(product, keyword)
        ]
        if not any(int(product.get("quantity") or 0) > 0 for product in matching_products):
            continue

        matched_orders.append(
            {
                "order": {
                    "id": order.get("id"),
                    "date_created": order.get("date_created"),
                    "status": order.get("status"),
                    "placed_by": _placed_by_label(order, customer_lookup),
                    "billing_contact": _billing_contact_label(order),
                    "college_unit": _unit_label(order),
                    "department_code": _department_code_label(order),
                    "total_inc_tax": _money(order.get("total_inc_tax")),
                },
                "matching_products": matching_products,
                "products": products,
            }
        )

    matched_orders = sorted(
        matched_orders,
        key=lambda item: int(item["order"]["id"] or 0),
        reverse=True,
    )

    return {
        "customer_query": customer,
        "keyword": keyword,
        "dimension": dimension,
        "dimension_label": _dimension_label(dimension),
        "dimension_query": value,
        "expanded_customer_terms": sorted(customer_terms),
        "expanded_product_terms": sorted(_search_terms(keyword)),
        "expanded_dimension_terms": sorted(_dimension_search_terms(dimension, value)),
        "days": days,
        "orders_analyzed": len(orders),
        "candidate_order_count": len(candidate_orders),
        "matching_order_count": len(matched_orders),
        "returned_count": min(len(matched_orders), limit),
        "orders": matched_orders[:limit],
    }


def get_full_order_contents_for_placed_by_customer(
    customer: str,
    days: int = 365,
    limit: int = 50,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Return all line items for orders placed by a BigCommerce customer account."""

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 100)
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    customer_terms = _search_terms(customer)

    placed_orders = [
        order
        for order in orders
        if _matches_person_name(_placed_by_label(order, customer_lookup), customer_terms)
    ]
    contact_orders = [
        order
        for order in orders
        if order not in placed_orders
        and _matches_person_name(_billing_contact_label(order), customer_terms)
    ]

    def build_order(order: dict[str, Any]) -> dict[str, Any]:
        products = get_order_products(int(order["id"]))["products"]
        return {
            "order": {
                "id": order.get("id"),
                "date_created": order.get("date_created"),
                "status": order.get("status"),
                "placed_by": _placed_by_label(order, customer_lookup),
                "billing_contact": _billing_contact_label(order),
                "college_unit": _unit_label(order),
                "department_code": _department_code_label(order),
                "total_inc_tax": _money(order.get("total_inc_tax")),
            },
            "products": products,
        }

    placed_result_orders = [build_order(order) for order in placed_orders[:limit]]
    contact_examples = [
        {
            "order_id": order.get("id"),
            "date_created": order.get("date_created"),
            "placed_by": _placed_by_label(order, customer_lookup),
            "billing_contact": _billing_contact_label(order),
            "college_unit": _unit_label(order),
            "total_inc_tax": _money(order.get("total_inc_tax")),
        }
        for order in contact_orders[:10]
    ]

    return {
        "customer_query": customer,
        "expanded_customer_terms": sorted(customer_terms),
        "days": days,
        "orders_analyzed": len(orders),
        "matching_order_count": len(placed_orders),
        "returned_count": len(placed_result_orders),
        "orders": placed_result_orders,
        "billing_contact_only_match_count": len(contact_orders),
        "billing_contact_only_examples": contact_examples,
        "note": (
            "Placed-by uses the BigCommerce order.customer_id customer account. "
            "Billing/shipping contact matches are listed separately because they are not the ordering account."
        ),
    }


def compare_computer_brand_sales_since(
    brands: list[str] | None = None,
    start_date: str = "2026-01-01",
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Compare computer sales quantities across brands since a date."""

    selected_brands = brands or ["Dell", "HP"]
    orders = _list_orders_since_date_for_analysis(start_date=start_date, max_orders=max_orders)

    quantity_by_brand: Counter[str] = Counter()
    revenue_by_brand: defaultdict[str, float] = defaultdict(float)
    orders_by_brand: defaultdict[str, set[int]] = defaultdict(set)
    products_by_brand: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for order, products in _products_for_orders(orders):
        order_id = int(order["id"])
        for product in products:
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue

            for brand in selected_brands:
                if product_matches_group_brand(product, "computers", brand):
                    product_name = str(product.get("name") or "Unknown product")
                    quantity_by_brand[brand] += quantity
                    revenue_by_brand[brand] += float(product.get("total_inc_tax") or 0)
                    orders_by_brand[brand].add(order_id)
                    products_by_brand[brand][product_name] += quantity
                    break

    total_quantity = sum(quantity_by_brand.values())
    results = []
    for brand in selected_brands:
        quantity = quantity_by_brand[brand]
        percentage = round((quantity / total_quantity * 100), 2) if total_quantity else 0
        results.append(
            {
                "brand": brand,
                "quantity_sold": quantity,
                "percentage_of_compared_units": percentage,
                "order_count": len(orders_by_brand[brand]),
                "order_ids_sample": sorted(orders_by_brand[brand], reverse=True)[:20],
                "total_inc_tax": round(revenue_by_brand[brand], 2),
                "top_matching_products": [
                    {"name": name, "quantity_sold": qty}
                    for name, qty in products_by_brand[brand].most_common(10)
                ],
            }
        )

    return {
        "start_date": start_date,
        "brands": selected_brands,
        "orders_analyzed": len(orders),
        "max_orders": max_orders,
        "total_compared_units": total_quantity,
        "classification_group": "computers",
        "classification_terms": {
            brand: brand_terms_for_group("computers", brand)
            for brand in selected_brands
        },
        "results": results,
    }


def get_purchase_breakdown_by_college_unit(
    days: int = 90,
    limit: int = 25,
    max_orders: int = 1000,
    group_by: str = "college_unit",
) -> dict[str, Any]:
    """Break recent purchases down by checkout college/unit or department code."""

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 100)
    group_by = group_by if group_by in {"college_unit", "department_code"} else "college_unit"
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)

    revenue_by_group: defaultdict[str, float] = defaultdict(float)
    orders_by_group: defaultdict[str, set[int]] = defaultdict(set)
    item_quantity_by_group: Counter[str] = Counter()
    people_by_group: defaultdict[str, set[str]] = defaultdict(set)

    for order in orders:
        unit = _group_label(order, group_by)
        order_id = int(order["id"])
        revenue_by_group[unit] += float(order.get("total_inc_tax") or 0)
        orders_by_group[unit].add(order_id)
        people_by_group[unit].add(_person_label(order))

    for order, products in _products_for_orders(orders):
        unit = _group_label(order, group_by)
        for product in products:
            quantity = int(product.get("quantity") or 0)
            if quantity > 0:
                item_quantity_by_group[unit] += quantity

    total_revenue = sum(revenue_by_group.values())
    total_orders = sum(len(order_ids) for order_ids in orders_by_group.values())
    total_items = sum(item_quantity_by_group.values())

    sorted_units = sorted(
        revenue_by_group.items(), key=lambda item: (-item[1], item[0])
    )
    selected_units = sorted_units[:limit]

    units = []
    for unit, revenue in selected_units:
        order_count = len(orders_by_group[unit])
        item_quantity = item_quantity_by_group[unit]
        units.append(
            {
                "group": unit,
                "total_inc_tax": round(revenue, 2),
                "revenue_percentage": round((revenue / total_revenue * 100), 2)
                if total_revenue
                else 0,
                "order_count": order_count,
                "order_percentage": round((order_count / total_orders * 100), 2)
                if total_orders
                else 0,
                "item_quantity": item_quantity,
                "item_quantity_percentage": round((item_quantity / total_items * 100), 2)
                if total_items
                else 0,
                "order_ids_sample": sorted(orders_by_group[unit], reverse=True)[:10],
                "people_sample": sorted(
                    person for person in people_by_group[unit] if person != "Unknown"
                )[:10],
            }
        )

    displayed_units = {unit for unit, _ in selected_units}
    displayed_revenue = sum(revenue_by_group[unit] for unit in displayed_units)
    displayed_orders = sum(len(orders_by_group[unit]) for unit in displayed_units)
    displayed_items = sum(item_quantity_by_group[unit] for unit in displayed_units)

    remaining_units = [unit for unit, _ in sorted_units if unit not in displayed_units]
    remaining_revenue = sum(revenue_by_group[unit] for unit in remaining_units)
    remaining_orders = sum(len(orders_by_group[unit]) for unit in remaining_units)
    remaining_items = sum(item_quantity_by_group[unit] for unit in remaining_units)

    unknown_order_count = len(orders_by_group.get("Unknown", set()))
    return {
        "days": days,
        "group_by": group_by,
        "group_label": "college/unit" if group_by == "college_unit" else "department code",
        "orders_analyzed": len(orders),
        "max_orders": max_orders,
        "total_revenue_inc_tax": round(total_revenue, 2),
        "total_orders": total_orders,
        "total_item_quantity": total_items,
        "unknown_unit_order_count": unknown_order_count,
        "sort_basis": "total_inc_tax",
        "displayed_unit_count": len(units),
        "total_unit_count": len(sorted_units),
        "displayed_totals": {
            "revenue_inc_tax": round(displayed_revenue, 2),
            "revenue_percentage": round((displayed_revenue / total_revenue * 100), 2)
            if total_revenue
            else 0,
            "order_count": displayed_orders,
            "order_percentage": round((displayed_orders / total_orders * 100), 2)
            if total_orders
            else 0,
            "item_quantity": displayed_items,
            "item_quantity_percentage": round((displayed_items / total_items * 100), 2)
            if total_items
            else 0,
        },
        "remaining_totals": {
            "unit_count": len(remaining_units),
            "revenue_inc_tax": round(remaining_revenue, 2),
            "revenue_percentage": round((remaining_revenue / total_revenue * 100), 2)
            if total_revenue
            else 0,
            "order_count": remaining_orders,
            "order_percentage": round((remaining_orders / total_orders * 100), 2)
            if total_orders
            else 0,
            "item_quantity": remaining_items,
            "item_quantity_percentage": round((remaining_items / total_items * 100), 2)
            if total_items
            else 0,
        },
        "count": len(units),
        "units": units,
    }


def get_sales_by_dimension(
    dimension: str = "college_unit",
    days: int = 90,
    limit: int = 25,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Break recent sales down by a configured checkout dimension."""

    if dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    days = max(1, min(days, 365))
    limit = min(max(limit, 1), 100)
    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)

    revenue_by_group: defaultdict[str, float] = defaultdict(float)
    orders_by_group: defaultdict[str, set[int]] = defaultdict(set)
    item_quantity_by_group: Counter[str] = Counter()
    people_by_group: defaultdict[str, set[str]] = defaultdict(set)

    for order in orders:
        order_id = int(order["id"])
        values = _dimension_values(order, dimension)
        share = 1 / len(values)
        for value in values:
            revenue_by_group[value] += float(order.get("total_inc_tax") or 0) * share
            orders_by_group[value].add(order_id)
            people_by_group[value].add(_placed_by_label(order, customer_lookup))

    for order, products in _products_for_orders(orders):
        values = _dimension_values(order, dimension)
        share = 1 / len(values)
        for product in products:
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue
            for value in values:
                item_quantity_by_group[value] += quantity * share

    total_revenue = sum(revenue_by_group.values())
    total_orders = len(orders)
    total_items = sum(item_quantity_by_group.values())
    sorted_groups = sorted(
        revenue_by_group.items(), key=lambda item: (-item[1], item[0])
    )
    selected = sorted_groups[:limit]

    groups = []
    for group, revenue in selected:
        order_count = len(orders_by_group[group])
        item_quantity = item_quantity_by_group[group]
        groups.append(
            {
                "group": group,
                "total_inc_tax": round(revenue, 2),
                "revenue_percentage": round((revenue / total_revenue * 100), 2)
                if total_revenue
                else 0,
                "order_count": order_count,
                "order_percentage": round((order_count / total_orders * 100), 2)
                if total_orders
                else 0,
                "item_quantity": round(item_quantity, 2),
                "item_quantity_percentage": round((item_quantity / total_items * 100), 2)
                if total_items
                else 0,
                "order_ids_sample": sorted(orders_by_group[group], reverse=True)[:10],
                "people_sample": sorted(
                    person for person in people_by_group[group] if person != "Unknown"
                )[:10],
            }
        )

    displayed = {group for group, _ in selected}
    displayed_revenue = sum(revenue_by_group[group] for group in displayed)
    displayed_order_ids = set().union(
        *(orders_by_group[group] for group in displayed)
    ) if displayed else set()
    displayed_items = sum(item_quantity_by_group[group] for group in displayed)
    remaining = [group for group, _ in sorted_groups if group not in displayed]
    remaining_revenue = sum(revenue_by_group[group] for group in remaining)
    remaining_order_ids = set().union(
        *(orders_by_group[group] for group in remaining)
    ) if remaining else set()
    remaining_items = sum(item_quantity_by_group[group] for group in remaining)

    return {
        "days": days,
        "dimension": dimension,
        "dimension_label": _dimension_label(dimension),
        "source_field": _dimension_field(dimension),
        "orders_analyzed": len(orders),
        "max_orders": max_orders,
        "metric_basis": {
            "revenue": "sum of order total_inc_tax",
            "orders": "count of orders with this dimension value",
            "items": "sum of order product quantities",
            "multi_value_note": "Orders with multiple values in this field are split evenly for revenue/item quantities and counted once per value for order count.",
        },
        "total_revenue_inc_tax": round(total_revenue, 2),
        "total_orders": total_orders,
        "total_item_quantity": round(total_items, 2),
        "unknown_order_count": len(orders_by_group.get("Unknown", set())),
        "displayed_group_count": len(groups),
        "total_group_count": len(sorted_groups),
        "displayed_totals": {
            "revenue_inc_tax": round(displayed_revenue, 2),
            "revenue_percentage": round((displayed_revenue / total_revenue * 100), 2)
            if total_revenue
            else 0,
            "order_count": len(displayed_order_ids),
            "order_percentage": round((len(displayed_order_ids) / total_orders * 100), 2)
            if total_orders
            else 0,
            "item_quantity": round(displayed_items, 2),
            "item_quantity_percentage": round((displayed_items / total_items * 100), 2)
            if total_items
            else 0,
        },
        "remaining_totals": {
            "group_count": len(remaining),
            "revenue_inc_tax": round(remaining_revenue, 2),
            "revenue_percentage": round((remaining_revenue / total_revenue * 100), 2)
            if total_revenue
            else 0,
            "order_count": len(remaining_order_ids),
            "order_percentage": round((len(remaining_order_ids) / total_orders * 100), 2)
            if total_orders
            else 0,
            "item_quantity": round(remaining_items, 2),
            "item_quantity_percentage": round((remaining_items / total_items * 100), 2)
            if total_items
            else 0,
        },
        "groups": groups,
    }


def get_orders_for_dimension_value(
    dimension: str,
    value: str,
    days: int = 90,
    limit: int = 50,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Return orders matching a checkout dimension value."""

    if dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    customer_lookup = _customer_lookup_for_orders(orders)
    matches = [
        order for order in orders if _order_matches_dimension(order, dimension, value)
    ]
    summarized = []
    for order in matches[:limit]:
        summary = _summarize_order(order)
        summary["placed_by"] = _placed_by_label(order, customer_lookup)
        summary["billing_contact"] = _billing_contact_label(order)
        summary["college_unit"] = _unit_label(order)
        summary["department_code"] = _department_code_label(order)
        summary["account_numbers"] = _dimension_values(order, "account_number")
        summarized.append(summary)

    return {
        "dimension": dimension,
        "dimension_label": _dimension_label(dimension),
        "source_field": _dimension_field(dimension),
        "query": value,
        "expanded_terms": sorted(_dimension_search_terms(dimension, value)),
        "days": days,
        "orders_analyzed": len(orders),
        "matching_order_count": len(matches),
        "returned_count": len(summarized),
        "orders": summarized,
    }


def get_top_products_for_dimension_value(
    dimension: str,
    value: str,
    days: int = 90,
    limit: int = 10,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Return top products for orders matching a checkout dimension value."""

    if dimension not in dimension_rules():
        raise ValueError(f"Unknown dimension: {dimension}")

    orders = _list_recent_orders_for_analysis(days=days, max_orders=max_orders)
    matches = [
        order for order in orders if _order_matches_dimension(order, dimension, value)
    ]
    quantity_by_key: Counter[tuple[str | None, str | None, Any]] = Counter()
    revenue_by_key: defaultdict[tuple[str | None, str | None, Any], float] = defaultdict(float)
    orders_by_key: defaultdict[tuple[str | None, str | None, Any], set[int]] = defaultdict(set)

    for order, products in _products_for_orders(matches):
        for product in products:
            quantity = int(product.get("quantity") or 0)
            if quantity <= 0:
                continue
            key = (product.get("name"), product.get("sku"), product.get("product_id"))
            quantity_by_key[key] += quantity
            revenue_by_key[key] += float(product.get("total_inc_tax") or 0)
            orders_by_key[key].add(int(order["id"]))

    products = []
    for (name, sku, product_id), quantity in quantity_by_key.most_common(limit):
        products.append(
            {
                "name": name,
                "sku": sku,
                "product_id": product_id,
                "quantity_sold": quantity,
                "order_count": len(orders_by_key[(name, sku, product_id)]),
                "total_inc_tax": round(revenue_by_key[(name, sku, product_id)], 2),
                "order_ids_sample": sorted(orders_by_key[(name, sku, product_id)], reverse=True)[:10],
            }
        )

    return {
        "dimension": dimension,
        "dimension_label": _dimension_label(dimension),
        "source_field": _dimension_field(dimension),
        "query": value,
        "expanded_terms": sorted(_dimension_search_terms(dimension, value)),
        "days": days,
        "orders_analyzed": len(orders),
        "matching_order_count": len(matches),
        "count": len(products),
        "products": products,
    }


def compare_dimension_values(
    dimension: str,
    values: list[str],
    days: int = 90,
    max_orders: int = 1000,
) -> dict[str, Any]:
    """Compare revenue/order/item totals for multiple checkout dimension values."""

    comparisons = []
    for value in values:
        orders_result = get_orders_for_dimension_value(
            dimension=dimension,
            value=value,
            days=days,
            limit=max_orders,
            max_orders=max_orders,
        )
        orders = orders_result["orders"]
        order_ids = {order["id"] for order in orders}
        item_quantity = 0
        for order, products in _products_for_orders(
            [{"id": order_id} for order_id in order_ids]
        ):
            for product in products:
                item_quantity += int(product.get("quantity") or 0)
        revenue = sum(float(order.get("total_inc_tax") or 0) for order in orders)
        comparisons.append(
            {
                "query": value,
                "matching_order_count": len(order_ids),
                "total_inc_tax": round(revenue, 2),
                "item_quantity": item_quantity,
                "order_ids_sample": sorted(order_ids, reverse=True)[:10],
            }
        )

    total_revenue = sum(item["total_inc_tax"] for item in comparisons)
    total_orders = sum(item["matching_order_count"] for item in comparisons)
    total_items = sum(item["item_quantity"] for item in comparisons)
    for item in comparisons:
        item["revenue_percentage"] = round(
            item["total_inc_tax"] / total_revenue * 100, 2
        ) if total_revenue else 0
        item["order_percentage"] = round(
            item["matching_order_count"] / total_orders * 100, 2
        ) if total_orders else 0
        item["item_quantity_percentage"] = round(
            item["item_quantity"] / total_items * 100, 2
        ) if total_items else 0

    return {
        "dimension": dimension,
        "dimension_label": _dimension_label(dimension),
        "source_field": _dimension_field(dimension),
        "days": days,
        "values": values,
        "metric_basis": {
            "revenue": "sum of order total_inc_tax for matched orders",
            "orders": "matched order count",
            "items": "sum of product line item quantities for matched orders",
        },
        "comparisons": comparisons,
    }


def count_orders_for_company(company: str, days: int = 30, limit: int = 250) -> dict[str, Any]:
    """Count recent orders whose billing company/name/message matches a term."""

    orders = list_recent_orders(days=days, limit=limit)["orders"]
    matches = [order for order in orders if _order_matches_term(order, company)]
    total = sum(float(order.get("total_inc_tax") or 0) for order in matches)
    return {
        "query": company,
        "days": days,
        "count": len(matches),
        "total_inc_tax": round(total, 2),
        "orders": matches,
    }


def search_products(keyword: str, limit: int = 20) -> dict[str, Any]:
    """Search catalog products by keyword."""

    data = _client().get(
        "/v3/catalog/products",
        {
            "keyword": keyword,
            "limit": min(max(limit, 1), 250),
            "include": "images,variants,custom_fields",
        },
    )
    products = [_summarize_product(product) for product in data.get("data", [])]
    return {"query": keyword, "count": len(products), "products": products}


def get_product_by_sku(sku: str) -> dict[str, Any]:
    """Get catalog product details by SKU."""

    data = _client().get(
        "/v3/catalog/products",
        {
            "sku": sku,
            "include": "images,variants,custom_fields",
        },
    )
    products = [_summarize_product(product) for product in data.get("data", [])]
    return {"sku": sku, "count": len(products), "products": products}


def get_low_stock_products(threshold: int = 5, limit: int = 250) -> dict[str, Any]:
    """Find products with inventory tracking enabled and inventory at/below threshold."""

    data = _client().get(
        "/v3/catalog/products",
        {"limit": min(max(limit, 1), 250), "include": "images"},
    )
    products = [
        _summarize_product(product)
        for product in data.get("data", [])
        if product.get("inventory_tracking") != "none"
        and int(product.get("inventory_level") or 0) <= threshold
    ]
    return {"threshold": threshold, "count": len(products), "products": products}


def find_products_missing_images(limit: int = 250) -> dict[str, Any]:
    """Find visible products with no product images."""

    data = _client().get(
        "/v3/catalog/products",
        {"limit": min(max(limit, 1), 250), "include": "images"},
    )
    products = [
        _summarize_product(product)
        for product in data.get("data", [])
        if product.get("is_visible") and not product.get("images")
    ]
    return {"count": len(products), "products": products}


READ_ONLY_TOOLS = {
    "get_order": get_order,
    "get_order_identity": get_order_identity,
    "find_orders_by_customer_name": find_orders_by_customer_name,
    "list_recent_orders": list_recent_orders,
    "get_order_products": get_order_products,
    "get_revenue_summary": get_revenue_summary,
    "get_order_summary": get_order_summary,
    "get_grouped_order_summary": get_grouped_order_summary,
    "get_ranked_orders": get_ranked_orders,
    "get_product_sales_leaderboard": get_product_sales_leaderboard,
    "get_source_orders_for_summary": get_source_orders_for_summary,
    "get_shipping_spend_by_method": get_shipping_spend_by_method,
    "get_shipping_charge_total": get_shipping_charge_total,
    "get_fulfillment_aging_report": get_fulfillment_aging_report,
    "get_oldest_unfulfilled_orders": get_oldest_unfulfilled_orders,
    "get_order_fulfillment_timing": get_order_fulfillment_timing,
    "get_top_products_sold": get_top_products_sold,
    "get_top_products_sold_to": get_top_products_sold_to,
    "get_top_customers_for_product_keyword": get_top_customers_for_product_keyword,
    "get_top_customers_for_product_keyword_in_dimension": get_top_customers_for_product_keyword_in_dimension,
    "get_product_keyword_order_lines_for_customer": get_product_keyword_order_lines_for_customer,
    "get_full_order_contents": get_full_order_contents,
    "get_full_order_contents_for_customer_product_in_dimension": get_full_order_contents_for_customer_product_in_dimension,
    "get_full_order_contents_for_placed_by_customer": get_full_order_contents_for_placed_by_customer,
    "compare_computer_brand_sales_since": compare_computer_brand_sales_since,
    "get_purchase_breakdown_by_college_unit": get_purchase_breakdown_by_college_unit,
    "get_sales_by_dimension": get_sales_by_dimension,
    "get_orders_for_dimension_value": get_orders_for_dimension_value,
    "get_top_products_for_dimension_value": get_top_products_for_dimension_value,
    "compare_dimension_values": compare_dimension_values,
    "count_orders_for_company": count_orders_for_company,
    "search_products": search_products,
    "get_product_by_sku": get_product_by_sku,
    "get_low_stock_products": get_low_stock_products,
    "find_products_missing_images": find_products_missing_images,
}
