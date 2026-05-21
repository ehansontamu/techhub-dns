from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests
import socketio


LOGGER = logging.getLogger("picklist_print_agent")


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


class Config:
    def __init__(self) -> None:
        base_dir = Path(__file__).resolve().parent
        load_env_file(base_dir / ".env")

        self.api_base_url = os.environ["API_BASE_URL"].rstrip("/")
        self.agent_token = os.environ["AGENT_TOKEN"]
        self.printer_name = os.environ["PRINTER_NAME"]
        self.sumatra_pdf_path = os.environ.get("SUMATRA_PDF_PATH", "SumatraPDF.exe")
        self.poll_seconds = int(os.environ.get("POLL_SECONDS", "15"))
        self.error_retry_seconds = int(os.environ.get("ERROR_RETRY_SECONDS", "5"))
        self.print_timeout_seconds = int(os.environ.get("PRINT_TIMEOUT_SECONDS", "120"))
        self.spool_dir = Path(os.environ.get("SPOOL_DIR", str(base_dir / "jobs")))


class PicklistPrintAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.config.agent_token}"}
        )
        self.socket = socketio.Client(reconnection=True)
        self.wake_event = threading.Event()
        self.stop_event = threading.Event()
        self.config.spool_dir.mkdir(parents=True, exist_ok=True)
        self._register_socket_handlers()

    def _register_socket_handlers(self) -> None:
        @self.socket.event
        def connect() -> None:
            LOGGER.info("Connected to websocket")
            self.socket.emit("join", {"room": "print_jobs"})
            self.wake_event.set()

        @self.socket.event
        def disconnect() -> None:
            LOGGER.warning("Websocket disconnected")

        @self.socket.on("print_job_available")
        def on_print_job_available(_payload: dict[str, Any]) -> None:
            LOGGER.info("Received print job wake-up event")
            self.wake_event.set()

    def connect_socket(self) -> None:
        socket_base = self.config.api_base_url.rstrip("/")
        self.socket.connect(socket_base, socketio_path="socket.io")

    def claim_next_job(self) -> Optional[dict[str, Any]]:
        response = self.session.post(
            f"{self.config.api_base_url}/api/system/print-agent/claim-next",
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("job")

    def download_job_file(self, job: dict[str, Any]) -> Path:
        download_url = urljoin(self.config.api_base_url, job["download_url"])
        destination = self.config.spool_dir / f"{job['id']}.pdf"
        response = self.session.get(download_url, timeout=60)
        response.raise_for_status()
        destination.write_bytes(response.content)
        return destination

    def report_complete(self, job_id: str) -> None:
        response = self.session.post(
            f"{self.config.api_base_url}/api/system/print-agent/jobs/{job_id}/complete",
            timeout=30,
        )
        response.raise_for_status()

    def report_failure(self, job_id: str, error_message: str) -> None:
        response = self.session.post(
            f"{self.config.api_base_url}/api/system/print-agent/jobs/{job_id}/fail",
            json={"error": error_message},
            timeout=30,
        )
        response.raise_for_status()

    def print_pdf(self, pdf_path: Path) -> None:
        command = [
            self.config.sumatra_pdf_path,
            "-print-to",
            self.config.printer_name,
            "-silent",
            str(pdf_path),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.config.print_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or result.stdout.strip() or "Silent print failed"
            )

    def _pause_after_error(self) -> None:
        retry_seconds = max(self.config.error_retry_seconds, 1)
        self.stop_event.wait(timeout=retry_seconds)

    def process_available_jobs(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.claim_next_job()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed to claim the next print job")
                self._pause_after_error()
                return

            if not job:
                return

            pdf_path: Optional[Path] = None
            try:
                LOGGER.info("Printing job %s for order %s", job["id"], job["order_id"])
                pdf_path = self.download_job_file(job)
                self.print_pdf(pdf_path)
                self.report_complete(job["id"])
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Print job %s failed", job.get("id"))
                try:
                    self.report_failure(job["id"], str(exc))
                except Exception:  # noqa: BLE001
                    LOGGER.exception(
                        "Failed to report print job failure for %s", job.get("id")
                    )
            finally:
                if pdf_path and pdf_path.exists():
                    pdf_path.unlink(missing_ok=True)

    def run(self) -> None:
        try:
            self.connect_socket()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Websocket unavailable, continuing with polling fallback: %s", exc
            )

        self.wake_event.set()
        while not self.stop_event.is_set():
            try:
                if self.wake_event.is_set():
                    self.wake_event.clear()
                    self.process_available_jobs()

                self.wake_event.wait(timeout=self.config.poll_seconds)
                self.wake_event.set()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Agent loop crashed unexpectedly; continuing")
                self.wake_event.set()
                self._pause_after_error()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = Config()
    while True:
        try:
            agent = PicklistPrintAgent(config)
            agent.run()
            return
        except KeyboardInterrupt:
            LOGGER.info("Print agent stopped manually")
            return
        except Exception:  # noqa: BLE001
            LOGGER.exception("Print agent crashed unexpectedly; restarting")
            time.sleep(max(config.error_retry_seconds, 1))


if __name__ == "__main__":
    main()
