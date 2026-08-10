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

        row = {
            **item,
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
    def __init__(self, settings: Any):
        self._settings = settings
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
        return {
            "rows": rows,
            "summary": self._build_row_summary(rows),
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

        progress("Fetching BigCommerce status 9 counts...", 0.78)
        bigcommerce_counts = self._fetch_bigcommerce_status9_counts(
            bigcommerce_headers, progress=progress
        )

        progress("Building reorder summary...", 0.92)
        merged = [
            {**product, "summary": summary_by_product_id.get(product.get("productId"), {})}
            for product in products
        ]
        simple_summary = self._build_simple_summary(merged, bigcommerce_counts)

        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_path = data_dir / f"inventory_summary_simple_{timestamp}.json"
        self._write_json(result_path, simple_summary)
        self._write_json(self._stable_summary_path(), simple_summary)
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
        import requests

        counts: defaultdict[str, int] = defaultdict(int)
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
                for product in self._fetch_bigcommerce_order_products(order_id, headers):
                    name = str(product.get("name") or "").upper()
                    counts[name] += _to_int(product.get("quantity"), 0)

            page += 1
            time.sleep(self._request_delay())

        return dict(counts)

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

    def _build_simple_summary(
        self, merged_products: list[dict[str, Any]], bigcommerce_counts: dict[str, int]
    ) -> list[dict[str, str]]:
        summary: list[dict[str, str]] = []
        for product in merged_products:
            product_summary = product.get("summary", {}) or {}
            name = str(product.get("name") or "")
            entry: dict[str, str] = {
                "name": name,
                "sku": str(product.get("sku") or ""),
            }

            for field in INVENTORY_FIELDS:
                entry[field] = str(product_summary.get(field, "0.00000"))

            entry["quantityAvailable"] = str(_to_int(entry.get("quantityAvailable"), 0))
            entry["bigCommerceStatus9"] = str(int(bigcommerce_counts.get(name.upper(), 0)))
            entry["reorderPoint"] = "0"
            entry["reorderQty"] = "0"

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
