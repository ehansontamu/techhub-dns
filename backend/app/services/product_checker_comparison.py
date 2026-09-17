"""Read-only catalog checks from BC_inFlow_checker_V10 (no desktop side effects)."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any


PRICING_SCHEME_ID = "45712549-61e6-47e1-84de-56baa8f52b9c"
EXCLUDED_CATEGORIES = {
    "6635bec6-4912-4dc6-8759-d7ef4d90f389",  # Internal
    "b0c33215-0c8b-4dd9-bf3b-e20184450a89",  # Category Needed
    "6b6f97f8-4053-4ee3-93c7-65705632b0f2",  # Testing
}
DESKTOP_CATEGORIES = {
    "e9efa219-a9b7-49b2-a61a-1820760f9dea",
    "df81fb27-ad68-4311-b01d-ef5214cf1aa2",
    "d0998e9d-6d70-47d7-9101-cd79036fb809",
}
LAPTOP_CATEGORIES = {
    "734ac0f4-bc67-4ca6-ae42-533411f1d811",
    "3a4accf6-aadc-4d94-96c6-e0ba1f238693",
    "1c17cb19-777e-4337-976a-e553d0e76ddf",
    "c050effa-86db-4497-80e0-e8546d862b0d",
    "415054a7-1b0f-40cf-b9d0-4015073452a8",
}
DISCONTINUED_CATEGORIES = {49, 50, 51, 52}
REPORT_KEYS = (
    "missing_in_bigcommerce", "missing_in_inflow", "mismatched_fields",
    "wrong_BPN", "whitespace_skus", "discontinued_in_inflow",
    "missing_custom_info", "closeout_y_and_bc_inventory_zero", "bc_inconsistencies",
    "variants_missing_sku",
)


def _text(value: Any) -> str:
    return str(value or "")


def _price(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    try:
        result = Decimal(str(value))
        if result.is_finite():
            return result
    except InvalidOperation:
        pass
    raise ValueError("Catalog contains an invalid price; the comparison was not completed.")


def _choose_price(*values: Any) -> str:
    for value in values:
        if value not in (None, "", 0):
            try:
                return str(_price(value))
            except ValueError:
                continue
    return "0"


def _inventory(value: Any) -> int | None:
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return None


def build_bigcommerce_index(products: list[dict], variants: dict) -> tuple[dict, list]:
    """Match by trimmed, case-sensitive variant SKU, with the V10 product fallback."""
    index = {}
    missing_skus = []
    for product in products:
        product_id = product["id"]
        usable_variants = []
        for variant in variants.get(product_id, []):
            if _text(variant.get("sku")).strip():
                usable_variants.append(variant)
            else:
                missing_skus.append({
                    "name": _text(product.get("name")), "sku": "",
                    "product_id": product_id, "variant_id": variant.get("id"),
                    "details": [f"Variant {variant.get('id')} has no SKU (product {product_id})."],
                })

        is_product_level = not usable_variants
        for item in usable_variants or [product]:
            raw_sku = _text(item.get("sku"))
            sku = raw_sku.strip()
            if not sku:
                continue
            index[sku] = {
                "sku": sku, "raw_sku": raw_sku,
                "name": _text(product.get("name")),
                "page_title": _text(product.get("page_title")),
                "bin_picking_number": _text(product.get("bin_picking_number")),
                "categories": product.get("categories") or [],
                "effective_price": _choose_price(
                    item.get("sale_price"), item.get("price"),
                    product.get("sale_price"), product.get("price"),
                ),
                "product_id": product_id,
                "variant_id": product.get("base_variant_id") if is_product_level else item.get("id"),
                "is_product_level": is_product_level,
                "inventory_level": item.get("inventory_level"),
                "inventory_tracking": product.get("inventory_tracking"),
                "product_inventory_level": product.get("inventory_level"),
            }
    return index, missing_skus


def compare_products(products: list[dict], variants: dict, inflow_products: list[dict]) -> dict:
    bc, missing_skus = build_bigcommerce_index(products, variants)
    eligible = [p for p in inflow_products if p.get("categoryId") not in EXCLUDED_CATEGORIES]
    inflow = {_text(p.get("sku")).strip(): p for p in eligible if _text(p.get("sku")).strip()}
    reports = {key: [] for key in REPORT_KEYS}
    reports["variants_missing_sku"] = missing_skus

    def add(key: str, product: dict, *details: str, **extra: Any) -> None:
        reports[key].append({
            "name": _text(product.get("name")), "sku": _text(product.get("sku")),
            "details": list(details), **extra,
        })

    for source, catalog in (("inFlow", inflow), ("BigCommerce", bc)):
        for product in catalog.values():
            raw = _text(product.get("raw_sku", product.get("sku")))
            if raw != raw.strip():
                add("whitespace_skus", product, f"{source} SKU has leading or trailing whitespace.",
                    sku=raw, source=source)

    for sku in sorted(inflow.keys() - bc.keys()):
        product = inflow[sku]
        if product.get("isActive", True):
            add("missing_in_bigcommerce", product, "Active in inFlow; absent from visible BigCommerce products.")

    for sku in sorted(bc.keys() - inflow.keys()):
        product = bc[sku]
        discontinued = bool(DISCONTINUED_CATEGORIES.intersection(product["categories"]))
        add("discontinued_in_inflow" if discontinued else "missing_in_inflow", product,
            "Visible in BigCommerce; absent from eligible inFlow products."
            + (" BigCommerce category is discontinued." if discontinued else ""))

    for sku in sorted(inflow.keys() & bc.keys()):
        product, item = inflow[sku], bc[sku]
        active = product.get("isActive", True)
        fields = product.get("customFields") or {}
        tracking = _text(item.get("inventory_tracking")).strip().lower()
        if _text(fields.get("custom1")).strip().upper() == "Y" and tracking in {"product", "variant"}:
            inventory = _inventory(item.get("inventory_level"))
            if inventory is None:
                inventory = _inventory(item.get("product_inventory_level"))
            if inventory == 0:
                add("closeout_y_and_bc_inventory_zero", product,
                    f"Closeout Y; inFlow {'active' if active else 'inactive'}; BigCommerce {tracking} inventory: 0.",
                    inflow_active=active, inventory_tracking=tracking, inventory_level=0)
        if not active:
            continue

        category = product.get("categoryId")
        inflow_bpn = _text(fields.get("custom2"))
        bc_bpn = item["bin_picking_number"]
        discontinued = bool(DISCONTINUED_CATEGORIES.intersection(item["categories"]))
        if not discontinued:
            if category in DESKTOP_CATEGORIES | LAPTOP_CATEGORIES:
                for number in range(3, 11):
                    field = f"custom{number}"
                    if not fields.get(field):
                        add("missing_custom_info", product, f"Missing inFlow field: {field}.", field=field)
                        break

            prices = product.get("prices") or []
            inflow_price = next((p.get("unitPrice") for p in prices if p.get("pricingSchemeId") == PRICING_SCHEME_ID), None)
            mismatches, details = [], []
            if bc_bpn != inflow_bpn:
                mismatches.append("BPN mismatch")
                details.append(f"BPN: inFlow {inflow_bpn or '(empty)'} / BigCommerce {bc_bpn or '(empty)'}")
            if _price(item["effective_price"]) != _price(inflow_price):
                mismatches.append("Price mismatch")
                details.append(f"Price: inFlow {_price(inflow_price)} / BigCommerce {item['effective_price']}")
            bc_name, inflow_name = item["name"], _text(product.get("name"))
            normalized_bc = re.sub(r"\s+", " ", bc_name.strip())
            normalized_inflow = re.sub(r"\s+", " ", inflow_name.strip())
            if bc_name != inflow_name and normalized_bc == normalized_inflow:
                mismatches.append("Name whitespace mismatch")
                details.append(f"Name whitespace: inFlow {inflow_name!r} / BigCommerce {bc_name!r}")
            if item["is_product_level"] and normalized_bc != normalized_inflow:
                mismatches.append("Name mismatch")
                details.append(f"Name: inFlow {inflow_name!r} / BigCommerce {bc_name!r}")
            if mismatches:
                add("mismatched_fields", product, *details, mismatches=mismatches)

        expected_bpn = "43211507" if category in DESKTOP_CATEGORIES else "43211508" if category in LAPTOP_CATEGORIES else None
        if expected_bpn and (inflow_bpn != expected_bpn or bc_bpn != expected_bpn):
            add("wrong_BPN", product,
                f"Expected {expected_bpn}; inFlow {inflow_bpn or '(empty)'} / BigCommerce {bc_bpn or '(empty)'}. "
                "BPN is the commodity code.")

    seen = set()
    for item in bc.values():
        if item["product_id"] in seen:
            continue
        seen.add(item["product_id"])
        name, title = item["name"], item["page_title"]
        issue = None
        if not title:
            issue = "Missing page_title"
        elif name != title:
            issue = "Title whitespace mismatch" if name.strip() == title.strip() else "Title case mismatch" if name.casefold() == title.casefold() else "Title mismatch"
        if issue:
            add("bc_inconsistencies", item, f"{issue}: page title {title!r}.", page_title=title, issue=issue)

    return {
        "reports": reports,
        "summary": {
            "bigcommerce_products": len(products), "bigcommerce_skus": len(bc),
            "inflow_products": len(inflow_products), "inflow_eligible": len(eligible),
            "inflow_excluded": len(inflow_products) - len(eligible),
            "inflow_inactive": sum(not p.get("isActive", True) for p in inflow_products),
            "matched_skus": len(bc.keys() & inflow.keys()),
            "total_findings": sum(len(rows) for rows in reports.values()),
        },
    }
