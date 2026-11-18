"""Match city/sub-location from order addresses to saved FedEx contacts conservatively."""
import re


def normalized(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def order_city(order):
    return " ".join(str(((order.inflow_data or {}).get("shippingAddress") or {}).get("city") or "").split())


def postal_matches(left, right):
    left = re.sub(r"[\s-]", "", str(left or ""))
    right = re.sub(r"[\s-]", "", str(right or ""))
    if not left or not right:
        return True
    if re.fullmatch(r"\d{5}(?:\d{4})?", left) and re.fullmatch(r"\d{5}(?:\d{4})?", right):
        return left[:5] == right[:5] and (len(left) == 5 or len(right) == 5 or left == right)
    return normalized(left) == normalized(right)


def destination_contact(order, contacts):
    address = (order.inflow_data or {}).get("shippingAddress") or {}
    city = normalized(order_city(order))
    if not city:
        return None
    candidates = [c for c in contacts if normalized(c.recipient.get("city")) == city]
    street = normalized(address.get("address1"))
    unit = normalized(address.get("address2"))
    state = normalized(address.get("stateProvince") or address.get("state"))
    postal = address.get("postalCode")
    def compatible(contact):
        r = contact.recipient
        return (not state or not normalized(r.get("state")) or state == normalized(r.get("state"))) and postal_matches(postal, r.get("postal_code")) and (not street or not normalized(r.get("address1")) or street == normalized(r.get("address1")))
    candidates = [c for c in candidates if compatible(c)]
    if unit:
        exact = [c for c in candidates if normalized(c.recipient.get("address2")) == unit]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None
        tokens = set(unit.split()) - {"suite", "ste", "room", "rm", "floor", "unit", "building", "bldg"}
        named = [c for c in candidates if tokens and tokens.issubset(set(normalized(c.label).split()))]
        return named[0] if len(named) == 1 else None
    # A location identifier can also be embedded in the delivery/street line.
    identifiers = set(re.findall(r"\b\d{3,}\b", normalized(" ".join(str(address.get(field) or "") for field in ("address1", "companyName")) + " " + str(order.delivery_location or ""))))
    named = [c for c in candidates if identifiers.intersection(normalized(c.label).split())]
    if len(named) == 1:
        return named[0]
    if len(candidates) == 1 and not normalized(candidates[0].recipient.get("address2")):
        return candidates[0]
    return None
