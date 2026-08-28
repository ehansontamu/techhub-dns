import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

INVENTORY_FIELDS = [
    "quantityOnHand",
    "quantityOnOrder",
    "quantityOnPurchaseOrder",
    "quantityOnWorkOrder",
    "quantityOnTransferOrder",
    "quantityReserved",
    "quantityReservedForSales",
    "quantityReservedForManufacturing",
    "quantityReservedForTransfers",
    "quantityReservedForBuilds",
    "quantityAvailable",
    "rawQuantityAvailable",
    "quantityPicked",
    "quantityInTransit",
    "quantityBuildable",
]

JobStatus = dict[str, Any]
ProgressCallback = Callable[[str, Optional[float]], None]


class InventoryReorderConfigError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_recipient_emails(value: Any) -> list[str]:
    """Return a normalized, de-duplicated list from JSON, CSV, or a sequence."""
    raw_values: list[Any]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                raw_values = parsed
            else:
                raw_values = stripped.split(",")
        else:
            raw_values = stripped.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        return []

    return list(
        dict.fromkeys(
            email.strip().lower()
            for email in raw_values
            if isinstance(email, str) and email.strip()
        )
    )


def _load_effective_inventory_reorder_recipients(env_value: Any) -> list[str]:
    """Merge pinned environment recipients with admin-managed database recipients."""
    env_recipients = _parse_recipient_emails(env_value)
    try:
        from app.database import get_db_session
        from app.services.system_setting_service import (
            SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS,
            SystemSettingService,
        )

        db = get_db_session()
        try:
            db_value = SystemSettingService.get_setting(
                db, SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "Could not load admin-managed inventory reorder recipients; using environment recipients: %s",
            exc,
        )
        return env_recipients

    return list(
        dict.fromkeys(
            [
                *env_recipients,
                *_parse_recipient_emails(db_value),
            ]
        )
    )


def _is_fulfilled_order_detail(detail: Any) -> bool:
    if not isinstance(detail, dict):
        return False
    status = str(detail.get("status") or "").strip().lower()
    return status == "fulfilled" or status.startswith("fulfilled ")


def _has_ten_plus_bigcommerce_order(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    orders = row.get("orders")
    if not isinstance(orders, dict):
        return False
    bigcommerce_orders = orders.get("bigCommerce", [])
    return isinstance(bigcommerce_orders, list) and any(
        isinstance(order, dict) and _to_int(order.get("quantity"), 0) >= 10
        for order in bigcommerce_orders
    )


def compute_inventory_reorder_rows(
    summary: list[dict[str, Any]], *, show_all: bool = False
) -> list[dict[str, Any]]:
    source_rows = summary if show_all else [
        row for row in summary if _to_int(row.get("reorderQty"), 0) > 0
    ]

    pinned: list[dict[str, Any]] = []
    unpinned: list[dict[str, Any]] = []

    for item in source_rows:
        available = _to_int(item.get("quantityAvailable"), 0)
        status9 = _to_int(item.get("bigCommerceStatus9"), 0)
        final_qty = available - status9
        on_order = _to_int(item.get("quantityOnOrder"), 0)
        combined = final_qty + on_order
        reorder_point = _to_int(item.get("reorderPoint"), 0)
        reorder_qty = _to_int(item.get("reorderQty"), 0)
        needs_reorder = combined <= reorder_point

        orders = item.get("orders")
        normalized_orders = orders
        if isinstance(orders, dict):
            inflow_orders = orders.get("inflow", [])
            normalized_orders = {
                **orders,
                "inflow": [
                    detail
                    for detail in inflow_orders
                    if not _is_fulfilled_order_detail(detail)
                ] if isinstance(inflow_orders, list) else [],
            }

        row = {
            **item,
            "orders": normalized_orders,
            "available": available,
            "status9": status9,
            "finalQty": final_qty,
            "onOrder": on_order,
            "combined": combined,
            "reorderPoint": reorder_point,
            "reorderQty": reorder_qty,
            "needsReorder": needs_reorder,
            "critical": needs_reorder and final_qty < 0,
        }

        (pinned if needs_reorder else unpinned).append(row)

    unpinned.sort(key=lambda row: (str(row.get("name") or "").lower(), str(row.get("sku") or "")))
    return pinned + unpinned


class InventoryReorderService:
    def __init__(
        self,
        settings: Any,
        recipient_email_resolver: Optional[Callable[[Any], list[str]]] = None,
    ):
        self._settings = settings
        self._recipient_email_resolver = (
            recipient_email_resolver or _load_effective_inventory_reorder_recipients
        )
        self._lock = threading.Lock()
        self._jobs: dict[str, JobStatus] = {}
        self._latest_job_id: Optional[str] = None

    def get_config_status(self) -> dict[str, Any]:
        missing: list[str] = []
        if not (self._settings.inflow_api_key or "").strip():
            missing.append("INFLOW_API_KEY")
        if not (self._settings.inventory_reorder_bigcommerce_token or "").strip():
            missing.append(
                "BC_ACCESS_TOKEN (or INVENTORY_REORDER_BIGCOMMERCE_TOKEN/BIGCOMMERCE_TOKEN/BC_TOKEN)"
            )
        if not (self._settings.inflow_company_id or "").strip():
            missing.append("INFLOW_COMPANY_ID")
        if not (self._settings.inventory_reorder_location_id or "").strip():
            missing.append("INVENTORY_REORDER_LOCATION_ID")
        if not (self._settings.inventory_reorder_bigcommerce_store_id or "").strip():
            missing.append("BC_STORE_HASH (or INVENTORY_REORDER_BIGCOMMERCE_STORE_ID/BC_STORE_ID)")

        return {
            "configured": not missing,
            "missing": missing,
            "scheduled_refresh": {
                "enabled": bool(
                    getattr(
                        self._settings,
                        "inventory_reorder_scheduled_refresh_enabled",
                        True,
                    )
                ),
                "times": str(
                    getattr(
                        self._settings,
                        "inventory_reorder_scheduled_refresh_times",
                        "7:30,12:00,15:00",
                    )
                    or "7:30,12:00,15:00"
                ),
                "timezone": str(
                    getattr(
                        self._settings,
                        "inventory_reorder_scheduled_refresh_timezone",
                        "America/Chicago",
                    )
                    or "America/Chicago"
                ),
            },
        }

    def get_refresh_cooldown(self) -> dict[str, Any]:
        cooldown_seconds = max(
            int(getattr(self._settings, "inventory_reorder_refresh_cooldown_seconds", 180) or 0),
            0,
        )
        if cooldown_seconds <= 0:
            return {
                "active": False,
                "cooldown_seconds": 0,
                "remaining_seconds": 0,
                "ends_at": None,
            }

        latest_started_at: Optional[datetime] = None
        with self._lock:
            for job in self._jobs.values():
                started_at = _parse_utc_iso(job.get("started_at"))
                if started_at and (latest_started_at is None or started_at > latest_started_at):
                    latest_started_at = started_at

        metadata_job = self._read_latest_metadata_job()
        metadata_started_at = _parse_utc_iso(metadata_job.get("started_at") if metadata_job else None)
        if metadata_started_at and (
            latest_started_at is None or metadata_started_at > latest_started_at
        ):
            latest_started_at = metadata_started_at

        if latest_started_at is None:
            return {
                "active": False,
                "cooldown_seconds": cooldown_seconds,
                "remaining_seconds": 0,
                "ends_at": None,
            }

        ends_at = latest_started_at + timedelta(seconds=cooldown_seconds)
        remaining = max(int((ends_at - datetime.now(timezone.utc)).total_seconds()), 0)
        return {
            "active": remaining > 0,
            "cooldown_seconds": cooldown_seconds,
            "remaining_seconds": remaining,
            "ends_at": ends_at.isoformat().replace("+00:00", "Z"),
        }

    def start_refresh(self) -> tuple[JobStatus, bool]:
        existing = self._get_running_job()
        if existing:
            return existing, False

        job_id = uuid4().hex
        job: JobStatus = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0.0,
            "message": "Queued",
            "error": None,
            "started_at": None,
            "finished_at": None,
            "result_path": None,
            "trigger": "manual",
        }
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
        thread.start()
        return self.get_job(job_id) or job, True

    def run_refresh_sync(self, *, trigger: str = "scheduled") -> JobStatus:
        existing = self._get_running_job()
        if existing:
            return existing

        job_id = uuid4().hex
        job: JobStatus = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0.0,
            "message": "Queued",
            "error": None,
            "started_at": None,
            "finished_at": None,
            "result_path": None,
            "trigger": trigger,
        }
        with self._lock:
            self._jobs[job_id] = job

        self._run_job(job_id)
        return self.get_job(job_id) or job

    def get_job(self, job_id: str) -> Optional[JobStatus]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def latest_job(self) -> Optional[JobStatus]:
        metadata_job = self._read_latest_metadata_job()
        with self._lock:
            memory_job: Optional[JobStatus] = None
            if self._latest_job_id and self._latest_job_id in self._jobs:
                memory_job = dict(self._jobs[self._latest_job_id])
            else:
                done_jobs = [
                    job for job in self._jobs.values()
                    if job.get("status") == "done" and job.get("result_path")
                ]
                if done_jobs:
                    done_jobs.sort(key=lambda job: str(job.get("finished_at") or ""), reverse=True)
                    memory_job = dict(done_jobs[0])

        if not memory_job:
            return metadata_job
        if not metadata_job:
            return memory_job

        memory_finished_at = _parse_utc_iso(memory_job.get("finished_at"))
        metadata_finished_at = _parse_utc_iso(metadata_job.get("finished_at"))
        if metadata_finished_at and (
            memory_finished_at is None or metadata_finished_at > memory_finished_at
        ):
            return metadata_job
        return memory_job

    def get_latest_summary(self, *, show_all: bool = False) -> dict[str, Any]:
        summary_path = self._stable_summary_path()
        if not summary_path.exists():
            latest = self.latest_job()
            result_path = latest.get("result_path") if latest else None
            if isinstance(result_path, str):
                candidate = Path(result_path)
                if candidate.exists():
                    summary_path = candidate

        if not summary_path.exists():
            return {
                "rows": [],
                "summary": self._build_row_summary([]),
                "latest_job": self.latest_job(),
                "has_data": False,
                "config": self.get_config_status(),
                "cooldown": self.get_refresh_cooldown(),
            }

        with summary_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, list):
            raise ValueError("Stored inventory summary must be a JSON array.")

        rows = compute_inventory_reorder_rows(payload, show_all=show_all)
        all_rows = rows if show_all else compute_inventory_reorder_rows(payload, show_all=True)
        row_summary = self._build_row_summary(rows)
        row_summary["ten_plus_bc_order_items"] = sum(
            1 for row in all_rows if _has_ten_plus_bigcommerce_order(row)
        )
        return {
            "rows": rows,
            "summary": row_summary,
            "latest_job": self.latest_job(),
            "has_data": True,
            "config": self.get_config_status(),
            "cooldown": self.get_refresh_cooldown(),
        }

    def latest_summary_path(self) -> Optional[Path]:
        summary_path = self._stable_summary_path()
        if summary_path.exists():
            # Flask resolves relative send_file paths from app.root_path, while
            # inventory summaries are stored relative to the backend runtime.
            # Return an absolute path so download callers always target the
            # same file that the summary reader found.
            return summary_path.resolve()

        latest = self.latest_job()
        result_path = latest.get("result_path") if latest else None
        if isinstance(result_path, str):
            candidate = Path(result_path)
            if candidate.exists():
                return candidate.resolve()

        return None

    def _run_job(self, job_id: str) -> None:
        self._update_job(
            job_id,
            status="running",
            started_at=_utc_now_iso(),
            message="Starting inventory refresh...",
            progress=0.01,
        )
        self._write_latest_metadata_from_job(job_id)
        try:
            result_path = self._refresh_inventory(job_id)
        except Exception as exc:
            logger.exception("Inventory reorder refresh failed")
            self._update_job(
                job_id,
                status="error",
                finished_at=_utc_now_iso(),
                error=str(exc),
                message="Refresh failed",
                progress=1.0,
            )
            self._write_latest_metadata_from_job(job_id)
            return

        self._update_job(
            job_id,
            status="done",
            finished_at=_utc_now_iso(),
            result_path=str(result_path),
            message="Refresh complete",
            progress=1.0,
        )
        with self._lock:
            self._latest_job_id = job_id
        self._write_latest_metadata_from_job(job_id)

    def _refresh_inventory(self, job_id: str) -> Path:
        self._ensure_configured()

        def progress(message: str, pct: Optional[float] = None) -> None:
            logger.info("Inventory reorder refresh %s: %s", job_id, message)
            self._update_job(job_id, message=message, progress=pct)

        inflow_headers = {
            "Authorization": f"Bearer {self._settings.inflow_api_key}",
            "Accept": "application/json;version=2024-03-12",
            "Content-Type": "application/json",
        }
        bigcommerce_headers = {
            "X-Auth-Token": self._settings.inventory_reorder_bigcommerce_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        progress("Fetching InFlow products...", 0.08)
        products = self._fetch_all_products(inflow_headers, progress=progress)

        progress(f"Fetched {len(products)} products. Building summary payload...", 0.25)
        summary_payload = [
            {
                "productId": product["productId"],
                "locationId": self._settings.inventory_reorder_location_id,
            }
            for product in products
            if product.get("productId")
        ]

        progress("Fetching InFlow product summaries...", 0.30)
        summaries = self._fetch_all_summaries(summary_payload, inflow_headers, progress=progress)
        summary_by_product_id = {
            summary.get("productId"): summary
            for summary in summaries
            if summary.get("productId")
        }

        progress("Fetching BigCommerce status 9 order details...", 0.72)
        (
            bigcommerce_counts,
            bigcommerce_order_details,
            status9_alert_orders,
        ) = self._fetch_bigcommerce_status9_demand(
            bigcommerce_headers, progress=progress
        )

        progress("Fetching recently created BigCommerce orders...", 0.79)
        alert_orders = self._merge_bigcommerce_orders(
            status9_alert_orders,
            self._fetch_recent_bigcommerce_orders(bigcommerce_headers, progress=progress),
        )

        progress("Fetching active InFlow sales order details...", 0.82)
        inflow_order_details = self._fetch_inflow_active_order_details(
            inflow_headers, progress=progress
        )

        progress("Building reorder summary...", 0.92)
        merged = [
            {**product, "summary": summary_by_product_id.get(product.get("productId"), {})}
            for product in products
        ]
        simple_summary = self._build_simple_summary(
            merged,
            bigcommerce_counts,
            bigcommerce_order_details=bigcommerce_order_details,
            inflow_order_details=inflow_order_details,
        )

        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_path = data_dir / f"inventory_summary_simple_{timestamp}.json"
        self._write_json(result_path, simple_summary)
        self._write_json(self._stable_summary_path(), simple_summary)
        self._notify_new_high_quantity_bigcommerce_orders(alert_orders)
        progress(f"Wrote {result_path.name}", 0.99)
        return result_path

    def _fetch_all_products(
        self, headers: dict[str, str], *, progress: ProgressCallback
    ) -> list[dict[str, Any]]:
        import requests

        products: list[dict[str, Any]] = []
        skip = 0
        count = 100
        while True:
            progress(f"InFlow: fetching products (skip={skip})...", None)
            response = requests.get(
                f"{self._inflow_base_url()}/{self._settings.inflow_company_id}/products",
                headers=headers,
                params={
                    "count": count,
                    "skip": skip,
                    "include": "inventoryLines,reorderSettings",
                    "filter[isActive]": "true",
                },
                timeout=90,
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise ValueError("InFlow products response must be an array.")
            if not batch:
                return products
            products.extend(batch)
            skip += count
            time.sleep(self._request_delay())

    def _fetch_all_summaries(
        self,
        payload: list[dict[str, str]],
        headers: dict[str, str],
        *,
        progress: ProgressCallback,
    ) -> list[dict[str, Any]]:
        import requests

        summaries: list[dict[str, Any]] = []
        batch_size = max(int(self._settings.inventory_reorder_batch_size or 100), 1)
        total = len(payload)
        for index in range(0, total, batch_size):
            batch = payload[index:index + batch_size]
            pct = 0.30 + ((index / total) * 0.35 if total else 0)
            progress(f"InFlow: fetching product summaries ({index}/{total})...", pct)
            response = requests.post(
                f"{self._inflow_base_url()}/{self._settings.inflow_company_id}/products/summary",
                headers=headers,
                json=batch,
                timeout=120,
            )
            response.raise_for_status()
            response_payload = response.json()
            if not isinstance(response_payload, list):
                raise ValueError("InFlow summaries response must be an array.")
            summaries.extend(response_payload)
            time.sleep(self._request_delay())
        return summaries

    def _fetch_bigcommerce_status9_counts(
        self, headers: dict[str, str], *, progress: ProgressCallback
    ) -> dict[str, int]:
        counts, _details, _orders = self._fetch_bigcommerce_status9_demand(
            headers,
            progress=progress,
        )
        return counts

    def _fetch_bigcommerce_status9_demand(
        self, headers: dict[str, str], *, progress: ProgressCallback
    ) -> tuple[
        dict[str, int],
        dict[str, list[dict[str, Any]]],
        list[dict[str, Any]],
    ]:
        import requests

        counts: defaultdict[str, int] = defaultdict(int)
        details_by_order: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        orders_by_id: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            progress(f"BigCommerce: fetching status 9 orders (page {page})...", None)
            response = requests.get(
                f"{self._bigcommerce_base_url()}/{self._settings.inventory_reorder_bigcommerce_store_id}/v2/orders",
                headers=headers,
                params={"status_id": 9, "limit": 50, "page": page},
                timeout=60,
            )
            if response.status_code != 200:
                logger.warning(
                    "BigCommerce orders fetch returned %s: %s",
                    response.status_code,
                    response.text[:300],
                )
                break

            orders = response.json()
            if not isinstance(orders, list):
                raise ValueError("BigCommerce orders response must be an array.")
            if not orders:
                break

            for order in orders:
                order_id = order.get("id") if isinstance(order, dict) else None
                if not order_id:
                    continue
                order_key = str(order_id)
                order_products = self._fetch_bigcommerce_order_products(order_id, headers)
                orders_by_id[order_key] = {"id": order_key, "products": order_products}
                for product in order_products:
                    name = str(product.get("name") or "").upper()
                    if not name:
                        continue
                    quantity = _to_int(product.get("quantity"), 0)
                    counts[name] += quantity
                    detail = details_by_order[name].setdefault(
                        order_key,
                        {
                            "orderId": order_key,
                            "orderNumber": str(order.get("id") or order_id),
                            "quantity": 0,
                            "status": "Aggiebuy Approval (Status 9)",
                        },
                    )
                    detail["quantity"] += quantity

            page += 1
            time.sleep(self._request_delay())

        details = {
            product_name: list(order_map.values())
            for product_name, order_map in details_by_order.items()
        }
        for order_details in details.values():
            order_details.sort(
                key=lambda detail: (-_to_int(detail.get("quantity"), 0), detail["orderNumber"])
            )

        return dict(counts), details, list(orders_by_id.values())

    def _fetch_inflow_active_order_details(
        self, headers: dict[str, str], *, progress: ProgressCallback
    ) -> dict[str, list[dict[str, Any]]]:
        import requests

        details_by_order: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        skip = 0
        count = 100
        while True:
            progress(f"InFlow: fetching active sales orders (skip={skip})...", None)
            response = requests.get(
                f"{self._inflow_base_url()}/{self._settings.inflow_company_id}/sales-orders",
                headers=headers,
                params={
                    "include": "lines.product,lines",
                    "filter[isActive]": "true",
                    "filter[inventoryStatus][]": ["unfulfilled", "started"],
                    "count": count,
                    "skip": skip,
                    "sort": "orderDate",
                    "sortDesc": "true",
                },
                timeout=90,
            )
            response.raise_for_status()
            payload = response.json()
            orders = payload.get("items", []) if isinstance(payload, dict) else payload
            if not isinstance(orders, list):
                raise ValueError("InFlow sales orders response must be an array.")
            if not orders:
                break

            for order in orders:
                if not isinstance(order, dict):
                    continue
                inventory_status = str(order.get("inventoryStatus") or "").strip()
                if inventory_status.lower() == "fulfilled" or inventory_status.lower().startswith("fulfilled "):
                    continue
                order_id = str(order.get("salesOrderId") or order.get("id") or "")
                order_number = str(order.get("orderNumber") or order_id or "Unknown")
                status = inventory_status or "Active"
                for line in order.get("lines", []) or []:
                    if not isinstance(line, dict):
                        continue
                    product = line.get("product") if isinstance(line.get("product"), dict) else {}
                    product_id = str(line.get("productId") or product.get("productId") or "")
                    if not product_id:
                        continue
                    quantity_data = line.get("quantity")
                    quantity = _to_int(
                        quantity_data.get("standardQuantity")
                        if isinstance(quantity_data, dict)
                        else quantity_data,
                        0,
                    )
                    order_key = order_id or order_number
                    detail = details_by_order[product_id].setdefault(
                        order_key,
                        {
                            "orderId": order_id,
                            "orderNumber": order_number,
                            "quantity": 0,
                            "status": status,
                        },
                    )
                    detail["quantity"] += quantity

            skip += count
            time.sleep(self._request_delay())

        details = {
            product_id: list(order_map.values())
            for product_id, order_map in details_by_order.items()
        }
        for order_details in details.values():
            order_details.sort(
                key=lambda detail: (-_to_int(detail.get("quantity"), 0), detail["orderNumber"])
            )
        return details

    def _fetch_bigcommerce_order_products(
        self, order_id: Any, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        import requests

        response = requests.get(
            f"{self._bigcommerce_base_url()}/{self._settings.inventory_reorder_bigcommerce_store_id}/v2/orders/{order_id}/products",
            headers=headers,
            timeout=60,
        )
        if response.status_code != 200:
            logger.warning(
                "BigCommerce order products fetch returned %s for order %s",
                response.status_code,
                order_id,
            )
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [product for product in payload if isinstance(product, dict)]

    def _fetch_recent_bigcommerce_orders(
        self, headers: dict[str, str], *, progress: ProgressCallback
    ) -> list[dict[str, Any]]:
        """Fetch new orders even after they have advanced past status 9."""
        import requests

        lookback_days = max(
            _to_int(
                getattr(self._settings, "inventory_reorder_bigcommerce_order_lookback_days", 7),
                7,
            ),
            1,
        )
        min_date_created = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat().replace("+00:00", "Z")
        recent_orders: list[dict[str, Any]] = []
        page = 1
        while True:
            progress(f"BigCommerce: fetching recent orders (page {page})...", None)
            response = requests.get(
                f"{self._bigcommerce_base_url()}/{self._settings.inventory_reorder_bigcommerce_store_id}/v2/orders",
                headers=headers,
                params={"min_date_created": min_date_created, "limit": 50, "page": page},
                timeout=60,
            )
            if response.status_code != 200:
                logger.warning(
                    "BigCommerce recent orders fetch returned %s: %s",
                    response.status_code,
                    response.text[:300],
                )
                break
            orders = response.json()
            if not isinstance(orders, list):
                raise ValueError("BigCommerce recent orders response must be an array.")
            if not orders:
                break
            for order in orders:
                order_id = order.get("id") if isinstance(order, dict) else None
                if order_id:
                    recent_orders.append(
                        {
                            "id": str(order_id),
                            "products": self._fetch_bigcommerce_order_products(order_id, headers),
                        }
                    )
            page += 1
            time.sleep(self._request_delay())
        return recent_orders

    @staticmethod
    def _merge_bigcommerce_orders(*order_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
        orders_by_id: dict[str, dict[str, Any]] = {}
        for orders in order_lists:
            for order in orders:
                order_id = str(order.get("id") or "").strip()
                if order_id and order_id not in orders_by_id:
                    orders_by_id[order_id] = order
        return list(orders_by_id.values())

    def _build_simple_summary(
        self,
        merged_products: list[dict[str, Any]],
        bigcommerce_counts: dict[str, int],
        *,
        bigcommerce_order_details: Optional[dict[str, list[dict[str, Any]]]] = None,
        inflow_order_details: Optional[dict[str, list[dict[str, Any]]]] = None,
    ) -> list[dict[str, Any]]:
        summary: list[dict[str, Any]] = []
        bigcommerce_order_details = bigcommerce_order_details or {}
        inflow_order_details = inflow_order_details or {}
        for product in merged_products:
            product_summary = product.get("summary", {}) or {}
            name = str(product.get("name") or "")
            product_id = str(product.get("productId") or "")
            entry: dict[str, Any] = {
                "name": name,
                "sku": str(product.get("sku") or ""),
            }

            for field in INVENTORY_FIELDS:
                entry[field] = str(product_summary.get(field, "0.00000"))

            entry["quantityAvailable"] = str(_to_int(entry.get("quantityAvailable"), 0))
            entry["bigCommerceStatus9"] = str(int(bigcommerce_counts.get(name.upper(), 0)))
            entry["reorderPoint"] = "0"
            entry["reorderQty"] = "0"
            entry["orders"] = {
                "bigCommerce": bigcommerce_order_details.get(name.upper(), []),
                "inflow": inflow_order_details.get(product_id, []),
            }

            reorder_settings = product.get("reorderSettings", []) or []
            for reorder_setting in reorder_settings:
                if not isinstance(reorder_setting, dict):
                    continue
                if reorder_setting.get("enableReordering"):
                    entry["reorderPoint"] = str(_to_int(reorder_setting.get("reorderPoint"), 0))
                    entry["reorderQty"] = str(_to_int(reorder_setting.get("reorderQuantity"), 0))
                    break

            summary.append(entry)

        return summary

    def _get_running_job(self) -> Optional[JobStatus]:
        with self._lock:
            for job in self._jobs.values():
                if job.get("status") in {"queued", "running"}:
                    return dict(job)
        return None

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if "progress" in updates and updates["progress"] is None:
                current = float(job.get("progress") or 0.0)
                updates["progress"] = min(0.99, current + 0.01)
            job.update(updates)

    def _build_row_summary(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(rows),
            "needs_reorder": sum(1 for row in rows if row.get("needsReorder")),
            "critical": sum(1 for row in rows if row.get("critical")),
            "ten_plus_bc_order_items": sum(
                1 for row in rows if _has_ten_plus_bigcommerce_order(row)
            ),
        }

    def _ensure_configured(self) -> None:
        status = self.get_config_status()
        if not status["configured"]:
            raise InventoryReorderConfigError(
                "Inventory reorder tool is not configured. Missing: "
                + ", ".join(status["missing"])
            )

    def _inflow_base_url(self) -> str:
        return self._settings.inflow_api_url.rstrip("/")

    def _bigcommerce_base_url(self) -> str:
        return self._settings.inventory_reorder_bigcommerce_base_url.rstrip("/")

    def _request_delay(self) -> float:
        return max(float(self._settings.inventory_reorder_request_delay_seconds or 0), 0.0)

    def _data_dir(self) -> Path:
        return Path(getattr(self._settings, "storage_root", "storage")) / "inventory-reorder"

    def _stable_summary_path(self) -> Path:
        return self._data_dir() / "inventory_summary_simple.json"

    def _metadata_path(self) -> Path:
        return self._data_dir() / "inventory_summary_metadata.json"

    def _bigcommerce_notification_state_path(self) -> Path:
        return self._data_dir() / "bigcommerce_order_notification_state.json"

    def _bigcommerce_order_alert_marker_path(self, order_id: str) -> Path:
        safe_order_id = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in order_id
        )
        return self._data_dir() / "bigcommerce-order-alerts" / f"{safe_order_id}.json"

    def _claim_bigcommerce_order_alert(self, order_id: str) -> bool:
        """Atomically reserve an order alert across scheduler/web processes."""
        marker_path = self._bigcommerce_order_alert_marker_path(order_id)
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with marker_path.open("x", encoding="utf-8") as handle:
                json.dump({"order_id": order_id, "claimed_at": _utc_now_iso()}, handle)
                handle.write("\n")
            return True
        except FileExistsError:
            return False
        except OSError as exc:
            logger.error("Failed to claim BigCommerce order %s alert: %s", order_id, exc)
            return False

    def _notify_new_high_quantity_bigcommerce_orders(
        self, orders: list[dict[str, Any]]
    ) -> None:
        if not getattr(self._settings, "inventory_reorder_teams_notifications_enabled", False):
            return
        recipient_emails = self._recipient_email_resolver(
            getattr(self._settings, "inventory_reorder_teams_recipient_email", "")
        )
        if not recipient_emails:
            logger.warning(
                "Inventory reorder Teams alerts are enabled but no recipient emails are configured"
            )
            return

        current_order_ids = {
            str(order.get("id") or "").strip() for order in orders if order.get("id")
        }
        if not current_order_ids:
            return
        state = self._read_bigcommerce_notification_state()
        if not state.get("initialized"):
            self._write_bigcommerce_notification_state(
                {"initialized": True, "seen_order_ids": sorted(current_order_ids), "notified_order_ids": []}
            )
            logger.info("Initialized BigCommerce order alert baseline with %s order(s)", len(current_order_ids))
            return

        seen_order_ids = set(state.get("seen_order_ids", []))
        notified_order_ids = set(state.get("notified_order_ids", []))
        minimum_quantity = max(
            _to_int(
                getattr(self._settings, "inventory_reorder_teams_minimum_order_quantity", 10),
                10,
            ),
            1,
        )
        recipient_name = str(
            getattr(self._settings, "inventory_reorder_teams_recipient_name", "Inventory Team")
            or "Inventory Team"
        ).strip()

        for order in orders:
            order_id = str(order.get("id") or "").strip()
            if not order_id or order_id in seen_order_ids:
                continue
            products = order.get("products") or []
            total_quantity = sum(_to_int(product.get("quantity"), 0) for product in products)
            if total_quantity < minimum_quantity or order_id in notified_order_ids:
                continue
            item_lines = [
                f"{_to_int(product.get('quantity'), 0)} x {str(product.get('name') or 'Item').strip()}"
                for product in products
                if _to_int(product.get("quantity"), 0) > 0
            ]
            if not self._claim_bigcommerce_order_alert(order_id):
                logger.info(
                    "Skipping BigCommerce order %s alert because another refresh already claimed it",
                    order_id,
                )
                continue
            notified_order_ids.add(order_id)
            self._write_bigcommerce_notification_state(
                {
                    "initialized": True,
                    "seen_order_ids": sorted(seen_order_ids | current_order_ids),
                    "notified_order_ids": sorted(notified_order_ids),
                }
            )
            for recipient_email in recipient_emails:
                if self._send_bigcommerce_order_alert(
                    recipient_email=recipient_email,
                    recipient_name=recipient_name,
                    bigcommerce_order_id=order_id,
                    order_items=item_lines,
                    total_quantity=total_quantity,
                ):
                    logger.info(
                        "Queued Teams alert for BigCommerce order %s (%s units) to %s",
                        order_id,
                        total_quantity,
                        recipient_email,
                    )
                else:
                    logger.error(
                        "Failed to queue Teams alert for BigCommerce order %s to %s; it will not be retried to avoid duplicates",
                        order_id,
                        recipient_email,
                    )
        self._write_bigcommerce_notification_state(
            {
                "initialized": True,
                "seen_order_ids": sorted(seen_order_ids | current_order_ids),
                "notified_order_ids": sorted(notified_order_ids),
            }
        )

    @staticmethod
    def _send_bigcommerce_order_alert(**kwargs: Any) -> bool:
        from app.services.teams_recipient_service import teams_recipient_service

        return teams_recipient_service.send_inventory_reorder_notification(**kwargs)

    def _read_bigcommerce_notification_state(self) -> dict[str, Any]:
        path = self._bigcommerce_notification_state_path()
        if not path.exists():
            return {"initialized": False, "seen_order_ids": [], "notified_order_ids": []}
        try:
            with path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
            if not isinstance(state, dict):
                raise ValueError("state must be an object")
            return {
                "initialized": bool(state.get("initialized")),
                "seen_order_ids": [str(value) for value in state.get("seen_order_ids", [])],
                "notified_order_ids": [str(value) for value in state.get("notified_order_ids", [])],
            }
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to read BigCommerce order alert state: %s", exc)
            return {"initialized": False, "seen_order_ids": [], "notified_order_ids": []}

    def _write_bigcommerce_notification_state(self, state: dict[str, Any]) -> None:
        path = self._bigcommerce_notification_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        try:
            self._write_json(temporary_path, state)
            temporary_path.replace(path)
        except OSError as exc:
            logger.error("Failed to persist BigCommerce order alert state: %s", exc)

    def _write_json(self, path: Path, payload: Any) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

    def _read_latest_metadata_job(self) -> Optional[JobStatus]:
        metadata_path = self._metadata_path()
        if not metadata_path.exists():
            return None
        try:
            with metadata_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read inventory reorder metadata: %s", exc)
            return None

        job = payload.get("latest_job") if isinstance(payload, dict) else None
        return dict(job) if isinstance(job, dict) else None

    def _write_latest_metadata_from_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return

        metadata_path = self._metadata_path()
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "latest_job": job,
            "updated_at": _utc_now_iso(),
        }
        try:
            self._write_json(metadata_path, payload)
        except OSError as exc:
            logger.warning("Failed to write inventory reorder metadata: %s", exc)
