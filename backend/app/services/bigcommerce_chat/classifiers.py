from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).with_name("classification_rules.json")
BUSINESS_RULES_PATH = Path(__file__).with_name("business_rules.json")


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_text(value: str) -> str:
    normalized = value.lower().replace("&", " and ")
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in normalized).split()
    )


def product_blob(product: dict[str, Any]) -> str:
    return normalize_text(
        " ".join(
            str(product.get(field) or "")
            for field in ["name", "sku", "product_id"]
        )
    )


def phrase_matches(blob: str, phrase: str) -> bool:
    normalized = normalize_text(phrase)
    if not normalized:
        return False

    blob_tokens = set(blob.split())
    tokens = [
        token
        for token in normalized.split()
        if len(token) > 1 and token not in {"and", "the"}
    ]
    if len(tokens) == 1:
        return tokens[0] in blob_tokens
    if normalized in blob:
        return True
    return bool(tokens and all(token in blob_tokens for token in tokens))


def product_matches_group_brand(
    product: dict[str, Any],
    group_name: str,
    brand: str,
) -> bool:
    rules = load_rules()
    group = rules.get("product_groups", {}).get(group_name)
    if not group:
        return False

    blob = product_blob(product)
    if any(phrase_matches(blob, phrase) for phrase in group.get("exclude_any", [])):
        return False

    brand_terms = group.get("brands", {}).get(brand, [])
    return any(phrase_matches(blob, phrase) for phrase in brand_terms)


def brand_terms_for_group(group_name: str, brand: str) -> list[str]:
    rules = load_rules()
    group = rules.get("product_groups", {}).get(group_name, {})
    return list(group.get("brands", {}).get(brand, []))


def brands_for_group(group_name: str) -> list[str]:
    rules = load_rules()
    group = rules.get("product_groups", {}).get(group_name, {})
    return list(group.get("brands", {}).keys())


def organization_aliases() -> dict[str, list[str]]:
    return {
        key: list(value)
        for key, value in load_rules().get("organization_aliases", {}).items()
    }


@lru_cache(maxsize=1)
def load_business_rules() -> dict[str, Any]:
    with BUSINESS_RULES_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def dimension_rules() -> dict[str, Any]:
    return dict(load_business_rules().get("dimensions", {}))
