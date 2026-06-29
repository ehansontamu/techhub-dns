from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests
import socketio


LOGGER = logging.getLogger("picklist_print_agent_native")


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
        self.poll_seconds = int(os.environ.get("POLL_SECONDS", "15"))
        self.error_retry_seconds = int(os.environ.get("ERROR_RETRY_SECONDS", "5"))
        self.print_timeout_seconds = int(os.environ.get("PRINT_TIMEOUT_SECONDS", "120"))
        self.spool_dir = Path(os.environ.get("SPOOL_DIR", str(base_dir / "jobs")))
        self.render_dpi = int(os.environ.get("NATIVE_RENDER_DPI", "600"))
        self.scale_mode = (
            os.environ.get("NATIVE_PRINT_SCALE_MODE", "actual").strip().lower()
        )


class NativePdfPrinter:
    def __init__(
        self,
        *,
        printer_name: str,
        render_dpi: int,
        scale_mode: str,
    ) -> None:
        self.printer_name = printer_name
        self.render_dpi = max(render_dpi, 72)
        self.scale_mode = (
            scale_mode if scale_mode in {"fit", "actual"} else "actual"
        )

    def _load_dependencies(self) -> dict[str, Any]:
        if os.name != "nt":
            raise RuntimeError("Native print agent requires Windows")

        missing: list[str] = []
        modules: dict[str, Any] = {}
        dependency_map = {
            "fitz": "PyMuPDF",
            "win32con": "pywin32",
            "win32print": "pywin32",
            "win32ui": "pywin32",
            "PIL.Image": "Pillow",
            "PIL.ImageWin": "Pillow",
        }

        for module_name, package_name in dependency_map.items():
            try:
                modules[module_name] = importlib.import_module(module_name)
            except ImportError:
                missing.append(package_name)

        if missing:
            unique_missing = ", ".join(sorted(set(missing)))
            raise RuntimeError(
                "Missing native print dependencies. Install: "
                f"{unique_missing}"
            )

        return modules

    @staticmethod
    def _target_rect(
        image_size: tuple[int, int],
        page_size_points: tuple[float, float],
        printable_size: tuple[int, int],
        offsets: tuple[int, int],
        physical_page_size: tuple[int, int],
        printer_dpi: tuple[int, int],
        *,
        scale_mode: str,
    ) -> tuple[int, int, int, int]:
        image_width, image_height = image_size
        page_width_points, page_height_points = page_size_points
        printable_width, printable_height = printable_size
        offset_x, offset_y = offsets
        physical_width, physical_height = physical_page_size
        printer_dpi_x, printer_dpi_y = printer_dpi

        if image_width <= 0 or image_height <= 0:
            raise RuntimeError("Rendered page has invalid dimensions")

        if scale_mode == "actual":
            draw_width = max(1, int(round((page_width_points / 72.0) * printer_dpi_x)))
            draw_height = max(
                1, int(round((page_height_points / 72.0) * printer_dpi_y))
            )
            # Preserve the PDF's true physical size, but center it on the sheet
            # instead of pinning it to the printable origin. This produces more
            # balanced margins while still avoiding fit-to-print shrinkage.
            left = int(round((physical_width - draw_width) / 2.0))
            top = int(round((physical_height - draw_height) / 2.0))
        else:
            scale = min(printable_width / image_width, printable_height / image_height)
            draw_width = max(1, int(image_width * scale))
            draw_height = max(1, int(image_height * scale))
            left = offset_x + max(0, (printable_width - draw_width) // 2)
            top = offset_y + max(0, (printable_height - draw_height) // 2)

        return left, top, draw_width, draw_height

    def print_pdf(self, pdf_path: Path) -> None:
        modules = self._load_dependencies()
        fitz = modules["fitz"]
        win32con = modules["win32con"]
        win32print = modules["win32print"]
        win32ui = modules["win32ui"]
        pil_image = modules["PIL.Image"]
        image_win = modules["PIL.ImageWin"]

        LOGGER.info("Printing %s to %s using native Windows APIs", pdf_path, self.printer_name)

        printer_handle = None
        printer_dc = None
        document = None
        doc_started = False

        try:
            printer_handle = win32print.OpenPrinter(self.printer_name)
            printer_dc = win32ui.CreateDC()
            printer_dc.CreatePrinterDC(self.printer_name)

            printable_width = printer_dc.GetDeviceCaps(win32con.HORZRES)
            printable_height = printer_dc.GetDeviceCaps(win32con.VERTRES)
            offset_x = printer_dc.GetDeviceCaps(win32con.PHYSICALOFFSETX)
            offset_y = printer_dc.GetDeviceCaps(win32con.PHYSICALOFFSETY)
            physical_width = printer_dc.GetDeviceCaps(win32con.PHYSICALWIDTH)
            physical_height = printer_dc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
            dpi_x = printer_dc.GetDeviceCaps(win32con.LOGPIXELSX)
            dpi_y = printer_dc.GetDeviceCaps(win32con.LOGPIXELSY)

            document = fitz.open(str(pdf_path))
            if document.page_count == 0:
                raise RuntimeError("PDF has no pages to print")

            printer_dc.StartDoc(pdf_path.name)
            doc_started = True

            zoom = self.render_dpi / 72
            matrix = fitz.Matrix(zoom, zoom)

            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                page_rect = page.rect
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = pil_image.frombytes(
                    "RGB",
                    (pixmap.width, pixmap.height),
                    pixmap.samples,
                )

                left, top, draw_width, draw_height = self._target_rect(
                    image.size,
                    (page_rect.width, page_rect.height),
                    (printable_width, printable_height),
                    (offset_x, offset_y),
                    (physical_width, physical_height),
                    (dpi_x, dpi_y),
                    scale_mode=self.scale_mode,
                )

                printer_dc.StartPage()
                try:
                    dib = image_win.Dib(image)
                    dib.draw(
                        printer_dc.GetHandleOutput(),
                        (left, top, left + draw_width, top + draw_height),
                    )
                finally:
                    printer_dc.EndPage()

            printer_dc.EndDoc()
            doc_started = False
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Native print failed: {exc}") from exc
        finally:
            if document is not None:
                document.close()
            if printer_dc is not None:
                if doc_started:
                    try:
                        printer_dc.AbortDoc()
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Failed to abort native print document")
                printer_dc.DeleteDC()
            if printer_handle is not None:
                win32print.ClosePrinter(printer_handle)


class NativePicklistPrintAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.config.agent_token}"}
        )
        self.socket = socketio.Client(reconnection=True)
        self.wake_event = threading.Event()
        self.stop_event = threading.Event()
        self.printer = NativePdfPrinter(
            printer_name=self.config.printer_name,
            render_dpi=self.config.render_dpi,
            scale_mode=self.config.scale_mode,
        )
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
        self.printer.print_pdf(pdf_path)

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
            agent = NativePicklistPrintAgent(config)
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
