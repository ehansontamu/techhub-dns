"""Resolve shipping labels from product identity, never HTML specification bodies."""

from dataclasses import dataclass
from html import unescape
import re

_MARKUP = re.compile(r"<\s*(?:/?[a-zA-Z][^>]*|!--.*?--)>", re.DOTALL)


def plain_label(value, limit=500):
    if not isinstance(value, str):
        return ""
    # Inflow descriptions can contain either literal or entity-encoded HTML.
    text = unescape(unescape(value))
    text = " ".join(text.split())
    return text if text and len(text) <= limit and not _MARKUP.search(text) else ""


@dataclass(frozen=True)
class ProductIdentity:
    name: str
    sku: str
    name_priority: int


def product_identity(product_id: str, line: dict | None = None, saved=None) -> ProductIdentity:
    line = line or {}
    product = line.get("product")
    product = product if isinstance(product, dict) else {}
    sku = (plain_label(getattr(saved, "sku", None), 255)
           or plain_label(product.get("sku"), 255) or plain_label(line.get("sku"), 255))
    candidates = [
        (getattr(saved, "name", None), 3),
        (product.get("name"), 2),
        (line.get("productName"), 2),
        (line.get("name"), 2),
        (line.get("description"), 1),
    ]
    for value, priority in candidates:
        name = plain_label(value, 160 if priority == 1 else 500)
        # Older saves may contain only a SKU/ID fallback; allow real names to replace it.
        if name and name not in {sku, product_id}:
            return ProductIdentity(name, sku, priority)
    return ProductIdentity(sku or product_id, sku, 0)
