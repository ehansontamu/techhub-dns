"""Manual catalog scans with shared on-disk status and last successful results."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO
from uuid import uuid4

from app.services.product_checker_comparison import compare_products
from app.utils.exceptions import DNSApiError, ExternalServiceError

if TYPE_CHECKING:
    import requests


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _release_lock(handle: BinaryIO) -> None:
    # Closing the handle also releases the OS lock if a worker exits unexpectedly.
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


class ProductCheckerService:
    def __init__(self, settings: Any, data_dir: Path | None = None):
        self.settings = settings
        self.data_dir = data_dir or Path(__file__).resolve().parents[2] / "storage" / "product_checker"

    def config_status(self) -> dict:
        # Reuse the integrations already configured for the app's inventory tool.
        required = {
            "inFlow API key": self.settings.inflow_api_key,
            "inFlow company": self.settings.inflow_company_id,
            "BigCommerce API token": self.settings.inventory_reorder_bigcommerce_token,
            "BigCommerce store": self.settings.inventory_reorder_bigcommerce_store_id,
        }
        missing = [name for name, value in required.items() if not (value or "").strip()]
        return {"configured": not missing, "missing": missing}

    def _claim_lock(self) -> BinaryIO | None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        handle = (self.data_dir / "scan.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (11, 13, 35, 36):  # EAGAIN/EACCES/EDEADLK (platform-specific)
                return None
            raise
        return handle

    def _read(self, filename: str) -> dict | None:
        try:
            with (self.data_dir / filename).open(encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return None

    def _write(self, filename: str, payload: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.data_dir / f".{filename}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(self.data_dir / filename)
        finally:
            temporary.unlink(missing_ok=True)

    def status(self, *, include_report: bool = False) -> dict:
        job = self._read("job.json")
        if job and job["status"] == "running":
            handle = self._claim_lock()
            if handle is not None:
                try:
                    # Read again under the lock: the scan may have just finished.
                    job = self._read("job.json")
                    if job and job["status"] == "running":
                        job.update(status="failed", finished_at=_now(),
                                   error="The scan was interrupted. Run the checker again.")
                        self._write("job.json", job)
                finally:
                    _release_lock(handle)
        payload = {"config": self.config_status(), "job": job}
        if include_report:
            payload["report"] = self._read("report.json")
        return payload

    def start(self, actor: str) -> tuple[dict, bool]:
        if not self.config_status()["configured"]:
            raise DNSApiError("PRODUCT_CHECKER_NOT_CONFIGURED", "The app's inFlow and BigCommerce connections must be configured.", 503)
        handle = self._claim_lock()
        if handle is None:
            return self.status(), False
        job = {
            "id": uuid4().hex, "status": "running", "started_at": _now(),
            "finished_at": None, "started_by": actor, "progress": 0,
            "message": "Starting product check…", "error": None,
        }
        try:
            self._write("job.json", job)
            worker = threading.Thread(target=self._run, args=(dict(job), handle), daemon=True)
            worker.start()
        except Exception:
            _release_lock(handle)
            raise
        return {"config": self.config_status(), "job": dict(job)}, True

    def _run(self, job: dict, handle: BinaryIO) -> None:
        try:
            def progress(message: str, percentage: int) -> None:
                job.update(message=message, progress=percentage)
                self._write("job.json", job)

            report = self._scan(progress)
            report.update(completed_at=_now(), started_by=job["started_by"], job_id=job["id"])
            # A failed scan never replaces the last complete comparison.
            self._write("report.json", report)
            job.update(status="completed", finished_at=report["completed_at"], progress=100,
                       message="Product check complete.")
        except Exception as exc:
            logger.error("Product checker scan %s failed (%s)", job["id"], type(exc).__name__)
            message = exc.message if isinstance(exc, ExternalServiceError) else "Product check failed. Check the server logs and try again."
            job.update(status="failed", finished_at=_now(), error=message, message="Product check failed.")
        finally:
            try:
                self._write("job.json", job)
            finally:
                _release_lock(handle)

    def _get(self, session: requests.Session, url: str, headers: dict, params: dict, source: str) -> Any:
        import requests

        try:
            response = session.get(url, headers=headers, params=params, timeout=(10, 60))
            if response.status_code == 204:
                return [] if source == "inFlow" else {"data": []}
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            # Never expose upstream response bodies, URLs or authentication headers.
            raise ExternalServiceError(source, "catalog fetch", "The catalog could not be loaded. Verify the connection and catalog read permissions, then retry.") from None

    def _bigcommerce_pages(self, session: requests.Session, path: str, params: dict, progress, percentage: int) -> list:
        url = f"{self.settings.inventory_reorder_bigcommerce_base_url.rstrip('/')}/{self.settings.inventory_reorder_bigcommerce_store_id}/v3/catalog/{path}"
        headers = {"X-Auth-Token": self.settings.inventory_reorder_bigcommerce_token, "Accept": "application/json"}
        rows, page = [], 1
        while True:
            progress(f"BigCommerce: {path}, page {page}…", percentage)
            payload = self._get(session, url, headers, {**params, "limit": 250, "page": page}, "BigCommerce")
            batch = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
                raise ExternalServiceError("BigCommerce", "catalog fetch", "Unexpected catalog response.")
            rows.extend(batch)
            if len(batch) < 250:
                return rows
            page += 1
            time.sleep(max(0, self.settings.inventory_reorder_request_delay_seconds))

    def _scan(self, progress) -> dict:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        with requests.Session() as session:
            session.mount("https://", HTTPAdapter(max_retries=Retry(
                total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
            )))
            products = self._bigcommerce_pages(session, "products", {"is_visible": "true"}, progress, 5)
            variants = {}
            for number, product in enumerate(products):
                variants[product["id"]] = self._bigcommerce_pages(
                    session, f"products/{product['id']}/variants", {}, progress,
                    10 + int(55 * number / max(len(products), 1)),
                )
                time.sleep(max(0, self.settings.inventory_reorder_request_delay_seconds))

            inflow_products = []
            headers = {"Authorization": f"Bearer {self.settings.inflow_api_key}", "Accept": "application/json;version=2024-03-12"}
            url = f"{self.settings.inflow_api_url.rstrip('/')}/{self.settings.inflow_company_id}/products"
            skip = 0
            while True:
                progress(f"inFlow: fetching products ({len(inflow_products)} loaded)…", 75)
                # Deliberately omit isActive: V10 closeout checks include inactive products.
                batch = self._get(session, url, headers, {"include": "prices", "count": 100, "skip": skip}, "inFlow")
                if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
                    raise ExternalServiceError("inFlow", "catalog fetch", "Unexpected catalog response.")
                inflow_products.extend(batch)
                if len(batch) < 100:
                    break
                skip += 100
                time.sleep(max(0, self.settings.inventory_reorder_request_delay_seconds))
            progress("Comparing product catalogs…", 95)
            return compare_products(products, variants, inflow_products)
