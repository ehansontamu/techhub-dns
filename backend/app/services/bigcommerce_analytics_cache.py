from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from app.database import get_db_session
from app.models.bigcommerce_cache import (
    BigCommerceCustomer,
    BigCommerceOrder,
    BigCommerceOrderAddress,
    BigCommerceOrderCustomField,
    BigCommerceOrderItem,
    BigCommerceSyncRun,
)
from app.services.bigcommerce_chat.bigcommerce_tools import (
    BigCommerceConfigError,
    _client,
    _customer_record_label,
    _customer_records_by_id,
    _dimension_values,
    _parse_bc_datetime,
    _shipping_addresses_for_order,
    _summarize_product,
    _summarize_form_fields,
)


SYNC_MAX_ORDERS_PER_RUN = 5000
SYNC_PAGE_LIMIT = 250
SYNC_INCREMENTAL_LOOKBACK_HOURS = 2
SYNC_STALE_AFTER_MINUTES = 15
SQL_QUERY_TIMEOUT_SECONDS = 20
SQL_MAX_ROWS = 200
SQL_DEFAULT_ROWS = 100
EXCLUDED_SALES_STATUSES = ["Cancelled", "Declined", "Refunded"]

ALLOWED_BC_TABLES = {
    "bc_orders",
    "bc_order_items",
    "bc_customers",
    "bc_order_addresses",
    "bc_order_custom_fields",
    "bc_sync_runs",
}

TABLE_DESCRIPTIONS: dict[str, str] = {
    "bc_orders": "One row per BigCommerce order, with order totals, status, dates, billing contact, placed-by customer, and common checkout dimensions. Sum order totals from this table without joining to line items unless you aggregate one row per order first.",
    "bc_order_items": "One row per BigCommerce order line item, including product name, SKU, quantity, and line totals.",
    "bc_customers": "BigCommerce customer accounts referenced by orders.",
    "bc_order_addresses": "Billing and shipping addresses for orders, including shipping method and address form fields.",
    "bc_order_custom_fields": "Flattened checkout/custom form fields from orders and addresses.",
    "bc_sync_runs": "Local sync run history and freshness/error metadata.",
}

FIELD_HINTS: dict[str, dict[str, str]] = {
    "bc_orders": {
        "id": "BigCommerce order ID. Mention as Order <id> in answers.",
        "date_created": "When the order was submitted.",
        "date_modified": "Last BigCommerce modification timestamp.",
        "status": "BigCommerce order status name.",
        "total_inc_tax": "Order total including tax. For sales totals, sum this from bc_orders without joining bc_order_items, or pre-aggregate to one row per order before summing.",
        "subtotal_inc_tax": "Order subtotal including tax before shipping. This is not a savings/discount amount.",
        "shipping_cost_inc_tax": "Customer shipping charge including tax.",
        "raw_order": "Full raw BigCommerce order JSON. Discount/coupon fields may be present here depending on API payload, such as discount_amount, coupon_discount, or coupons; verify field existence before using them.",
        "items_total": "Total item quantity on the order.",
        "placed_by_name": "BigCommerce customer account name attached to order.customer_id.",
        "college_unit": "Recipient College/Unit checkout field.",
        "department_code": "Department Code checkout field.",
        "account_numbers": "Comma-separated account numbers from checkout fields.",
        "recipients": "Comma-separated recipient UIN/name checkout field values.",
    },
    "bc_order_items": {
        "name": "Line-item product name.",
        "sku": "Line-item SKU.",
        "quantity": "Quantity purchased on this order line.",
        "total_inc_tax": "Line total including tax.",
        "base_total": "Line base total from BigCommerce. This is not a reliable savings metric by itself.",
    },
}


class BigCommerceAnalyticsQueryError(ValueError):
    pass


def _utc_naive(value: Any) -> datetime | None:
    parsed = _parse_bc_datetime(value)
    if not parsed:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _money(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    return _int_or_none(value) or 0


def _full_name(record: dict[str, Any]) -> str | None:
    name = " ".join(
        str(record.get(part) or "").strip()
        for part in ["first_name", "last_name"]
        if record.get(part)
    ).strip()
    return name or None


def _normalized_field_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _form_field_map(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, list):
        return {}
    return {
        str(field.get("name")): field.get("value")
        for field in fields
        if isinstance(field, dict) and field.get("name")
    }


def _collect_custom_fields(order: dict[str, Any], addresses: list[tuple[str, int, dict[str, Any]]]) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []

    def collect(source: str, fields: Any) -> None:
        if not isinstance(fields, list):
            return
        for field in fields:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            name = str(field["name"])
            value = field.get("value")
            rows.append(
                {
                    "source": source,
                    "field_name": name,
                    "normalized_name": _normalized_field_name(name),
                    "field_value": None if value is None else str(value),
                }
            )

    collect("order", order.get("form_fields"))
    collect("billing", (order.get("billing_address") or {}).get("form_fields"))
    for address_type, index, address in addresses:
        collect(f"{address_type} {index}", address.get("form_fields"))
    return rows


def _fetch_orders(min_date_created: str | None, min_date_modified: str | None, max_orders: int) -> list[dict[str, Any]]:
    client = _client()
    orders: list[dict[str, Any]] = []
    page = 1
    while len(orders) < max_orders:
        page_limit = min(SYNC_PAGE_LIMIT, max_orders - len(orders))
        params: dict[str, Any] = {
            "limit": page_limit,
            "page": page,
            "sort": "date_modified:desc" if min_date_modified else "date_created:desc",
        }
        if min_date_modified:
            params["min_date_modified"] = min_date_modified
        if min_date_created:
            params["min_date_created"] = min_date_created
        data = client.get("/v2/orders", params)
        if not data:
            break
        orders.extend(order for order in data if isinstance(order, dict))
        if len(data) < page_limit:
            break
        page += 1
    return orders


def _fetch_products(order_id: int) -> list[dict[str, Any]]:
    try:
        data = _client().get(f"/v2/orders/{order_id}/products")
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _last_successful_sync(db: Session) -> BigCommerceSyncRun | None:
    return (
        db.query(BigCommerceSyncRun)
        .filter(BigCommerceSyncRun.status == "completed")
        .order_by(BigCommerceSyncRun.completed_at.desc())
        .first()
    )


def _latest_order_modified_at(db: Session) -> datetime | None:
    return db.query(BigCommerceOrder.date_modified).order_by(BigCommerceOrder.date_modified.desc()).scalar()


def _iso_utc(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _upsert_customers(db: Session, orders: list[dict[str, Any]], synced_at: datetime) -> dict[int, dict[str, Any]]:
    customer_ids = [_int_value(order.get("customer_id")) for order in orders if order.get("customer_id")]
    customers = _customer_records_by_id(customer_ids)
    for customer_id, customer in customers.items():
        full_name = _customer_record_label(customer)
        row = db.get(BigCommerceCustomer, customer_id)
        if row is None:
            row = BigCommerceCustomer(id=customer_id)
            db.add(row)
        row.first_name = customer.get("first_name")
        row.last_name = customer.get("last_name")
        row.full_name = full_name
        row.email = customer.get("email")
        row.company = customer.get("company")
        row.raw_customer = customer
        row.synced_at = synced_at
    return customers


def _upsert_order(
    db: Session,
    order: dict[str, Any],
    customer_lookup: dict[int, dict[str, Any]],
    synced_at: datetime,
) -> int:
    order_id = int(order["id"])
    billing = order.get("billing_address") or {}
    customer_id = _int_or_none(order.get("customer_id"))
    customer = customer_lookup.get(customer_id or 0)
    placed_by_name = _customer_record_label(customer) if customer else _full_name(billing)
    account_numbers = _dimension_values(order, "account_number")
    recipients = _dimension_values(order, "recipient")

    row = db.get(BigCommerceOrder, order_id)
    if row is None:
        row = BigCommerceOrder(id=order_id)
        db.add(row)

    # Some historical BigCommerce orders reference customer IDs that are no
    # longer returned by the customer endpoint. Keep the order analyzable.
    row.customer_id = customer_id if customer else None
    row.date_created = _utc_naive(order.get("date_created"))
    row.date_modified = _utc_naive(order.get("date_modified"))
    row.date_shipped = _utc_naive(order.get("date_shipped"))
    row.status = order.get("status")
    row.status_id = _int_or_none(order.get("status_id"))
    row.total_inc_tax = _money(order.get("total_inc_tax"))
    row.subtotal_inc_tax = _money(order.get("subtotal_inc_tax"))
    row.shipping_cost_inc_tax = _money(order.get("shipping_cost_inc_tax"))
    row.items_total = _int_value(order.get("items_total"))
    row.payment_method = order.get("payment_method")
    row.customer_message = order.get("customer_message")
    row.staff_notes = order.get("staff_notes")
    row.billing_first_name = billing.get("first_name")
    row.billing_last_name = billing.get("last_name")
    row.billing_email = billing.get("email")
    row.billing_company = billing.get("company")
    row.placed_by_name = placed_by_name
    row.placed_by_email = customer.get("email") if customer else billing.get("email")
    row.placed_by_company = customer.get("company") if customer else billing.get("company")
    row.college_unit = (_dimension_values(order, "college_unit") or [None])[0]
    row.department_code = (_dimension_values(order, "department_code") or [None])[0]
    row.account_numbers = ", ".join(value for value in account_numbers if value != "Unknown") or None
    row.recipients = ", ".join(value for value in recipients if value != "Unknown") or None
    row.form_fields = _summarize_form_fields(order)
    row.raw_order = order
    row.synced_at = synced_at
    return order_id


def _replace_order_items(db: Session, order_id: int, products: list[dict[str, Any]], synced_at: datetime) -> int:
    db.query(BigCommerceOrderItem).filter(BigCommerceOrderItem.order_id == order_id).delete()
    for index, product in enumerate(products, start=1):
        line_item_id = _int_or_none(product.get("id")) or _int_or_none(product.get("order_product_id"))
        db.add(
            BigCommerceOrderItem(
                id=f"{order_id}:{line_item_id or index}",
                bigcommerce_line_item_id=line_item_id,
                order_id=order_id,
                product_id=_int_or_none(product.get("product_id")),
                variant_id=_int_or_none(product.get("variant_id")),
                name=product.get("name"),
                sku=product.get("sku"),
                quantity=_int_value(product.get("quantity")),
                total_inc_tax=_money(product.get("total_inc_tax")),
                base_total=_money(product.get("base_total")),
                raw_product=product,
                synced_at=synced_at,
            )
        )
    return len(products)


def _address_tuple(address_type: str, source_index: int, address: dict[str, Any]) -> tuple[str, int, dict[str, Any]]:
    return address_type, source_index, address


def _replace_addresses_and_fields(
    db: Session,
    order_id: int,
    order: dict[str, Any],
    synced_at: datetime,
) -> tuple[int, int]:
    db.query(BigCommerceOrderAddress).filter(BigCommerceOrderAddress.order_id == order_id).delete()
    db.query(BigCommerceOrderCustomField).filter(BigCommerceOrderCustomField.order_id == order_id).delete()

    address_rows: list[tuple[str, int, dict[str, Any]]] = []
    billing = order.get("billing_address") or {}
    if billing:
        address_rows.append(_address_tuple("billing", 0, billing))
    for index, address in enumerate(_shipping_addresses_for_order(order_id), start=1):
        if isinstance(address, dict):
            address_rows.append(_address_tuple("shipping", index, address))

    for address_type, source_index, address in address_rows:
        full_name = _full_name(address)
        db.add(
            BigCommerceOrderAddress(
                id=str(uuid.uuid4()),
                order_id=order_id,
                address_type=address_type,
                source_index=source_index,
                bigcommerce_address_id=_int_or_none(address.get("id")),
                first_name=address.get("first_name"),
                last_name=address.get("last_name"),
                full_name=full_name,
                email=address.get("email"),
                company=address.get("company"),
                street_1=address.get("street_1"),
                street_2=address.get("street_2"),
                city=address.get("city"),
                state=address.get("state") or address.get("state_iso2"),
                zip=address.get("zip"),
                country=address.get("country"),
                phone=address.get("phone"),
                shipping_method=address.get("shipping_method"),
                form_fields=_form_field_map(address.get("form_fields")),
                raw_address=address,
                synced_at=synced_at,
            )
        )

    custom_fields = _collect_custom_fields(order, address_rows)
    for field in custom_fields:
        db.add(
            BigCommerceOrderCustomField(
                id=str(uuid.uuid4()),
                order_id=order_id,
                source=field["source"] or "unknown",
                field_name=field["field_name"] or "",
                normalized_name=field["normalized_name"] or "",
                field_value=field["field_value"],
                synced_at=synced_at,
            )
        )

    return len(address_rows), len(custom_fields)


def sync_bigcommerce_analytics_cache(
    full_backfill: bool = False,
    max_orders: int = SYNC_MAX_ORDERS_PER_RUN,
) -> dict[str, Any]:
    """Pull BigCommerce orders into local analytics tables."""

    db = get_db_session()
    sync_run = BigCommerceSyncRun(
        mode="full_backfill" if full_backfill else "incremental",
        status="running",
        started_at=datetime.utcnow(),
        sync_metadata={"max_orders": max_orders},
    )
    db.add(sync_run)
    db.commit()

    try:
        max_orders = min(max(max_orders, 1), SYNC_MAX_ORDERS_PER_RUN)
        min_date_created: str | None = "2000-01-01T00:00:00Z" if full_backfill else None
        min_date_modified: str | None = None

        if not full_backfill:
            latest = _latest_order_modified_at(db)
            if latest:
                min_date_modified = _iso_utc(latest - timedelta(hours=SYNC_INCREMENTAL_LOOKBACK_HOURS))
            else:
                min_date_created = "2000-01-01T00:00:00Z"

        orders = _fetch_orders(min_date_created, min_date_modified, max_orders)
        synced_at = datetime.utcnow()
        customer_lookup = _upsert_customers(db, orders, synced_at)

        line_items_upserted = 0
        addresses_upserted = 0
        custom_fields_upserted = 0
        for order in orders:
            if not order.get("id"):
                continue
            order_id = _upsert_order(db, order, customer_lookup, synced_at)
            line_items_upserted += _replace_order_items(db, order_id, _fetch_products(order_id), synced_at)
            address_count, field_count = _replace_addresses_and_fields(db, order_id, order, synced_at)
            addresses_upserted += address_count
            custom_fields_upserted += field_count

        modified_values = [_utc_naive(order.get("date_modified")) for order in orders]
        modified_values = [value for value in modified_values if value is not None]

        sync_run.status = "completed"
        sync_run.completed_at = datetime.utcnow()
        sync_run.min_date_modified = min(modified_values) if modified_values else None
        sync_run.max_date_modified = max(modified_values) if modified_values else None
        sync_run.orders_scanned = len(orders)
        sync_run.orders_upserted = len(orders)
        sync_run.line_items_upserted = line_items_upserted
        sync_run.addresses_upserted = addresses_upserted
        sync_run.customers_upserted = len(customer_lookup)
        sync_run.sync_metadata = {
            "max_orders": max_orders,
            "custom_fields_upserted": custom_fields_upserted,
            "min_date_created": min_date_created,
            "min_date_modified": min_date_modified,
        }
        db.commit()
        return _sync_run_summary(sync_run)
    except BigCommerceConfigError:
        db.rollback()
        sync_run.status = "skipped"
        sync_run.completed_at = datetime.utcnow()
        sync_run.error = "BC_STORE_HASH and BC_ACCESS_TOKEN are not configured."
        db.add(sync_run)
        db.commit()
        return _sync_run_summary(sync_run)
    except Exception as exc:
        db.rollback()
        sync_run.status = "failed"
        sync_run.completed_at = datetime.utcnow()
        sync_run.error = str(exc)
        db.add(sync_run)
        db.commit()
        raise
    finally:
        db.close()


def _sync_run_summary(sync_run: BigCommerceSyncRun | None) -> dict[str, Any] | None:
    if sync_run is None:
        return None
    return {
        "id": sync_run.id,
        "mode": sync_run.mode,
        "status": sync_run.status,
        "started_at": sync_run.started_at.isoformat() if sync_run.started_at else None,
        "completed_at": sync_run.completed_at.isoformat() if sync_run.completed_at else None,
        "orders_scanned": sync_run.orders_scanned,
        "orders_upserted": sync_run.orders_upserted,
        "line_items_upserted": sync_run.line_items_upserted,
        "addresses_upserted": sync_run.addresses_upserted,
        "customers_upserted": sync_run.customers_upserted,
        "error": sync_run.error,
        "metadata": sync_run.sync_metadata,
    }


def get_bigcommerce_cache_status() -> dict[str, Any]:
    db = get_db_session()
    try:
        last_sync = _last_successful_sync(db)
        latest_order = db.query(BigCommerceOrder.date_modified).order_by(BigCommerceOrder.date_modified.desc()).first()
        order_count = db.query(BigCommerceOrder).count()
        item_count = db.query(BigCommerceOrderItem).count()
        now = datetime.utcnow()
        is_stale = True
        if last_sync and last_sync.completed_at:
            is_stale = now - last_sync.completed_at > timedelta(minutes=SYNC_STALE_AFTER_MINUTES)
        return {
            "last_successful_sync": _sync_run_summary(last_sync),
            "order_count": order_count,
            "line_item_count": item_count,
            "latest_order_modified_at": latest_order[0].isoformat() if latest_order and latest_order[0] else None,
            "is_stale": is_stale,
            "stale_after_minutes": SYNC_STALE_AFTER_MINUTES,
        }
    finally:
        db.close()


def get_bigcommerce_analytics_schema() -> dict[str, Any]:
    db = get_db_session()
    try:
        inspector = inspect(db.bind)
        tables = []
        for table_name in sorted(ALLOWED_BC_TABLES):
            columns = []
            for column in inspector.get_columns(table_name):
                column_name = column["name"]
                columns.append(
                    {
                        "name": column_name,
                        "type": str(column["type"]),
                        "nullable": bool(column.get("nullable")),
                        "hint": FIELD_HINTS.get(table_name, {}).get(column_name),
                    }
                )
            tables.append(
                {
                    "name": table_name,
                    "description": TABLE_DESCRIPTIONS.get(table_name, ""),
                    "columns": columns,
                }
            )
        return {
            "tables": tables,
            "rules": [
                "Only SELECT queries are allowed.",
                f"Queries are limited to {SQL_MAX_ROWS} rows.",
                "Use bc_orders.id as the BigCommerce order ID and display it as Order <id>.",
                "Cancelled, Declined, and Refunded statuses should usually be excluded from sales analytics unless the user asks otherwise.",
            ],
            "cache_status": get_bigcommerce_cache_status(),
        }
    finally:
        db.close()


def _strip_sql_comments(sql: str) -> str:
    without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n\r]*", " ", without_block_comments)


def _referenced_tables(sql: str) -> set[str]:
    tables: set[str] = set()
    for match in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)(?!\s*\()", sql, re.IGNORECASE):
        name = match.group(1).split(".")[-1].lower()
        if name not in {"select"}:
            tables.add(name)
    return tables


def _validate_readonly_sql(sql: str) -> str:
    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        raise BigCommerceAnalyticsQueryError("SQL query is empty.")
    if ";" in cleaned.rstrip(";"):
        raise BigCommerceAnalyticsQueryError("Only one SQL statement is allowed.")
    cleaned = cleaned.rstrip(";").strip()
    if not re.match(r"^select\b", cleaned, re.IGNORECASE):
        raise BigCommerceAnalyticsQueryError("Only SELECT queries are allowed.")
    forbidden = (
        r"\b(insert|update|delete|drop|alter|create|truncate|replace|merge|grant|"
        r"revoke|call|execute|load|set|attach|detach|pragma)\b|into\s+outfile"
    )
    if re.search(forbidden, cleaned, re.IGNORECASE):
        raise BigCommerceAnalyticsQueryError("Only read-only SELECT queries are allowed.")
    if re.search(r"\b(information_schema|mysql|performance_schema|sys|sqlite_master)\b", cleaned, re.IGNORECASE):
        raise BigCommerceAnalyticsQueryError("System tables are not available.")
    referenced = _referenced_tables(cleaned)
    disallowed = sorted(table for table in referenced if table not in ALLOWED_BC_TABLES)
    if disallowed:
        raise BigCommerceAnalyticsQueryError(
            "Only BigCommerce analytics cache tables may be queried: "
            + ", ".join(sorted(ALLOWED_BC_TABLES))
        )
    if not referenced:
        raise BigCommerceAnalyticsQueryError("Query must read from a BigCommerce analytics table.")
    return cleaned


def _coerce_cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def run_bigcommerce_readonly_query(sql: str, limit: int = SQL_DEFAULT_ROWS) -> dict[str, Any]:
    cleaned = _validate_readonly_sql(sql)
    limit = min(max(int(limit or SQL_DEFAULT_ROWS), 1), SQL_MAX_ROWS)
    limited_sql = f"SELECT * FROM ({cleaned}) AS bc_readonly_query LIMIT :limit"

    db = get_db_session()
    try:
        db.execute(text(f"SET STATEMENT max_statement_time={SQL_QUERY_TIMEOUT_SECONDS} FOR SELECT 1"))
    except Exception:
        db.rollback()
    try:
        result = db.execute(text(limited_sql), {"limit": limit})
        rows = [dict(row._mapping) for row in result.fetchall()]
        columns = list(result.keys())
        coerced_rows = [
            {key: _coerce_cell(value) for key, value in row.items()}
            for row in rows
        ]
        return {
            "columns": columns,
            "rows": coerced_rows,
            "row_count": len(coerced_rows),
            "limit": limit,
            "truncated": len(coerced_rows) >= limit,
            "cache_status": get_bigcommerce_cache_status(),
        }
    except Exception as exc:
        raise BigCommerceAnalyticsQueryError(str(exc)) from exc
    finally:
        db.close()


def get_order_financial_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    include_statuses: list[str] | None = None,
    exclude_statuses: list[str] | None = None,
) -> dict[str, Any]:
    """Summarize order-grain sales and explicit BigCommerce discount fields."""

    exclude_statuses = exclude_statuses or EXCLUDED_SALES_STATUSES
    db = get_db_session()
    try:
        query = db.query(
            func.count(BigCommerceOrder.id).label("order_count"),
            func.coalesce(func.sum(BigCommerceOrder.total_inc_tax), 0).label("total_sales_inc_tax"),
            func.coalesce(func.sum(BigCommerceOrder.subtotal_inc_tax), 0).label("subtotal_inc_tax"),
            func.coalesce(func.sum(BigCommerceOrder.shipping_cost_inc_tax), 0).label("shipping_inc_tax"),
            func.coalesce(func.sum(BigCommerceOrder.items_total), 0).label("item_quantity"),
            func.sum(func.cast(func.json_unquote(func.json_extract(BigCommerceOrder.raw_order, "$.discount_amount")), BigCommerceOrder.total_inc_tax.type)).label("discount_amount"),
            func.sum(func.cast(func.json_unquote(func.json_extract(BigCommerceOrder.raw_order, "$.coupon_discount")), BigCommerceOrder.total_inc_tax.type)).label("coupon_discount"),
            func.count(func.json_extract(BigCommerceOrder.raw_order, "$.discount_amount")).label("discount_field_count"),
            func.count(func.json_extract(BigCommerceOrder.raw_order, "$.coupon_discount")).label("coupon_discount_field_count"),
        )
        if include_statuses:
            query = query.filter(BigCommerceOrder.status.in_(include_statuses))
        elif exclude_statuses:
            query = query.filter(BigCommerceOrder.status.notin_(exclude_statuses))
        if start_date:
            query = query.filter(BigCommerceOrder.date_created >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.filter(BigCommerceOrder.date_created < datetime.fromisoformat(end_date))

        row = query.one()
        discount_field_count = int(row.discount_field_count or 0)
        coupon_discount_field_count = int(row.coupon_discount_field_count or 0)
        has_savings_fields = discount_field_count > 0 or coupon_discount_field_count > 0
        discount_amount = float(row.discount_amount or 0) if has_savings_fields else None
        coupon_discount = float(row.coupon_discount or 0) if has_savings_fields else None
        known_savings_total = (
            round(float(discount_amount or 0) + float(coupon_discount or 0), 2)
            if has_savings_fields
            else None
        )
        return {
            "start_date": start_date,
            "end_date": end_date,
            "include_statuses": include_statuses,
            "exclude_statuses": exclude_statuses if not include_statuses else None,
            "order_count": int(row.order_count or 0),
            "item_quantity": int(row.item_quantity or 0),
            "total_sales_inc_tax": _coerce_cell(row.total_sales_inc_tax or 0),
            "subtotal_inc_tax": _coerce_cell(row.subtotal_inc_tax or 0),
            "shipping_inc_tax": _coerce_cell(row.shipping_inc_tax or 0),
            "discount_amount": round(discount_amount, 2) if discount_amount is not None else None,
            "coupon_discount": round(coupon_discount, 2) if coupon_discount is not None else None,
            "known_savings_total": known_savings_total,
            "savings_available": has_savings_fields,
            "discount_field_count": discount_field_count,
            "coupon_discount_field_count": coupon_discount_field_count,
            "savings_basis": (
                "Sum of explicit BigCommerce raw_order discount_amount and coupon_discount fields when present. "
                "If those fields are absent, the cache does not contain a reliable savings metric."
            ),
            "cache_status": get_bigcommerce_cache_status(),
        }
    finally:
        db.close()


def _catalog_search_text(product: dict[str, Any]) -> str:
    values: list[Any] = [
        product.get("name"),
        product.get("sku"),
        product.get("description"),
        product.get("mpn"),
        product.get("upc"),
        product.get("gtin"),
    ]
    for field in product.get("custom_fields") or []:
        if isinstance(field, dict):
            values.extend([field.get("name"), field.get("value")])
    for variant in product.get("variants") or []:
        if isinstance(variant, dict):
            values.append(variant.get("sku"))
            for option in variant.get("option_values") or []:
                if isinstance(option, dict):
                    values.extend([option.get("label"), option.get("option_display_name")])
    return " ".join(str(value or "") for value in values).lower()


def _fetch_catalog_candidates(
    keyword: str,
    required_terms: list[str],
    max_catalog_products: int,
) -> list[dict[str, Any]]:
    keyword_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+.-]{2,}", keyword or "")
        if token.lower() not in {"cpu", "with", "and", "the", "for", "computer", "computers"}
    ]
    search_terms = list(
        dict.fromkeys(
            term.strip()
            for term in [keyword, *keyword_tokens, *required_terms]
            if term and term.strip()
        )
    )
    if not search_terms:
        return []

    client = _client()
    candidates_by_id: dict[int, dict[str, Any]] = {}
    per_search_limit = min(max(max_catalog_products, 1), 250)
    for term in search_terms:
        data = client.get(
            "/v3/catalog/products",
            {
                "keyword": term,
                "limit": per_search_limit,
                "include": "images,variants,custom_fields",
            },
        )
        for product in data.get("data", []):
            product_id = product.get("id")
            if product_id is not None:
                candidates_by_id[int(product_id)] = product

    normalized_required = [term.lower() for term in required_terms if term.strip()]
    if normalized_required:
        return [
            product
            for product in candidates_by_id.values()
            if all(term in _catalog_search_text(product) for term in normalized_required)
        ]
    return list(candidates_by_id.values())


def get_catalog_filtered_product_sales(
    keyword: str,
    required_terms: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
    max_catalog_products: int = 250,
) -> dict[str, Any]:
    """Aggregate local sales for live catalog products matching product/spec terms."""

    if isinstance(required_terms, str):
        required_terms = [term.strip() for term in required_terms.split(",") if term.strip()]
    required_terms = required_terms or []
    catalog_products = _fetch_catalog_candidates(keyword, required_terms, max_catalog_products)
    catalog_summaries = [_summarize_product(product) for product in catalog_products]
    product_ids = {
        int(product["id"])
        for product in catalog_summaries
        if product.get("id") is not None
    }
    skus = {
        str(product["sku"]).strip()
        for product in catalog_summaries
        if product.get("sku")
    }

    db = get_db_session()
    try:
        query = (
            db.query(
                BigCommerceOrderItem.product_id.label("product_id"),
                BigCommerceOrderItem.sku.label("sku"),
                BigCommerceOrderItem.name.label("name"),
                func.sum(BigCommerceOrderItem.quantity).label("quantity_sold"),
                func.sum(BigCommerceOrderItem.total_inc_tax).label("revenue_inc_tax"),
                func.count(func.distinct(BigCommerceOrderItem.order_id)).label("order_count"),
                func.min(BigCommerceOrder.date_created).label("first_order_date"),
                func.max(BigCommerceOrder.date_created).label("last_order_date"),
            )
            .join(BigCommerceOrder, BigCommerceOrder.id == BigCommerceOrderItem.order_id)
            .filter(BigCommerceOrder.status.notin_(["Cancelled", "Declined", "Refunded"]))
        )
        match_filters = []
        if product_ids:
            match_filters.append(BigCommerceOrderItem.product_id.in_(product_ids))
        if skus:
            match_filters.append(BigCommerceOrderItem.sku.in_(skus))
        if not match_filters:
            rows = []
        else:
            query = query.filter(or_(*match_filters))
            if start_date:
                query = query.filter(BigCommerceOrder.date_created >= datetime.fromisoformat(start_date))
            if end_date:
                query = query.filter(BigCommerceOrder.date_created < datetime.fromisoformat(end_date))
            rows = (
                query.group_by(
                    BigCommerceOrderItem.product_id,
                    BigCommerceOrderItem.sku,
                    BigCommerceOrderItem.name,
                )
                .order_by(func.sum(BigCommerceOrderItem.quantity).desc())
                .limit(min(max(int(limit or 20), 1), 100))
                .all()
            )

        by_id = {int(product["id"]): product for product in catalog_summaries if product.get("id") is not None}
        by_sku = {str(product["sku"]).strip(): product for product in catalog_summaries if product.get("sku")}
        products = []
        for row in rows:
            catalog_product = None
            if row.product_id is not None:
                catalog_product = by_id.get(int(row.product_id))
            if catalog_product is None and row.sku:
                catalog_product = by_sku.get(str(row.sku).strip())
            products.append(
                {
                    "product_id": row.product_id,
                    "sku": row.sku,
                    "name": row.name,
                    "quantity_sold": int(row.quantity_sold or 0),
                    "revenue_inc_tax": _coerce_cell(row.revenue_inc_tax or 0),
                    "order_count": int(row.order_count or 0),
                    "first_order_date": row.first_order_date.isoformat() if row.first_order_date else None,
                    "last_order_date": row.last_order_date.isoformat() if row.last_order_date else None,
                    "catalog_product": catalog_product,
                }
            )

        sold_product_ids = {item["product_id"] for item in products}
        sold_skus = {item["sku"] for item in products}
        result = {
            "keyword": keyword,
            "required_terms": required_terms,
            "start_date": start_date,
            "end_date": end_date,
            "catalog_match_count": len(catalog_summaries),
            "returned_count": len(products),
            "total_quantity_sold": sum(item["quantity_sold"] for item in products),
            "total_revenue_inc_tax": round(sum(float(item["revenue_inc_tax"] or 0) for item in products), 2),
            "products": products,
            "catalog_matches_without_sales": [
                product
                for product in catalog_summaries
                if product.get("id") not in sold_product_ids
                and product.get("sku") not in sold_skus
            ][:10],
            "cache_status": get_bigcommerce_cache_status(),
        }
        return result
    finally:
        db.close()
