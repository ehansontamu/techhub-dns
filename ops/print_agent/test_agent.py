import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys
import types


if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")

    class _StubSession:
        def __init__(self) -> None:
            self.headers = {}

    requests_stub.Session = _StubSession
    sys.modules["requests"] = requests_stub


if "socketio" not in sys.modules:
    socketio_stub = types.ModuleType("socketio")

    class _StubClient:
        def __init__(self, *args, **kwargs) -> None:
            self.handlers = {}

        def event(self, fn):
            self.handlers[fn.__name__] = fn
            return fn

        def on(self, name):
            def decorator(fn):
                self.handlers[name] = fn
                return fn

            return decorator

        def emit(self, *args, **kwargs) -> None:
            return None

        def connect(self, *args, **kwargs) -> None:
            return None

    socketio_stub.Client = _StubClient
    sys.modules["socketio"] = socketio_stub

from agent import PicklistPrintAgent


def _make_config():
    temp_dir = tempfile.TemporaryDirectory()
    config = SimpleNamespace(
        agent_token="test-token",
        api_base_url="http://example.test",
        printer_name="Test Printer",
        sumatra_pdf_path="SumatraPDF.exe",
        poll_seconds=1,
        error_retry_seconds=0,
        print_timeout_seconds=1,
        spool_dir=Path(temp_dir.name),
    )
    return temp_dir, config


class PicklistPrintAgentTests(unittest.TestCase):
    def test_process_available_jobs_survives_claim_error(self) -> None:
        temp_dir, config = _make_config()
        try:
            agent = PicklistPrintAgent(config)
            pause_calls = 0

            def fail_claim():
                raise RuntimeError("claim failed")

            def pause_after_error() -> None:
                nonlocal pause_calls
                pause_calls += 1
                agent.stop_event.set()

            agent.claim_next_job = fail_claim  # type: ignore[method-assign]
            agent._pause_after_error = pause_after_error  # type: ignore[method-assign]

            agent.process_available_jobs()

            self.assertEqual(pause_calls, 1)
        finally:
            temp_dir.cleanup()

    def test_run_survives_processing_error(self) -> None:
        temp_dir, config = _make_config()
        try:
            agent = PicklistPrintAgent(config)
            process_calls = 0
            pause_calls = 0

            def skip_socket() -> None:
                return None

            def fail_once() -> None:
                nonlocal process_calls
                process_calls += 1
                if process_calls == 1:
                    raise RuntimeError("loop failure")
                agent.stop_event.set()

            def pause_after_error() -> None:
                nonlocal pause_calls
                pause_calls += 1

            agent.connect_socket = skip_socket  # type: ignore[method-assign]
            agent.process_available_jobs = fail_once  # type: ignore[method-assign]
            agent._pause_after_error = pause_after_error  # type: ignore[method-assign]

            agent.run()

            self.assertEqual(process_calls, 2)
            self.assertEqual(pause_calls, 1)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
