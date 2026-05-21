from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.routes.auth import _sanitize_relay_state


def test_sanitize_relay_state_allows_internal_paths() -> None:
    assert _sanitize_relay_state("/orders/123?tab=notes#section", "techhub.pythonanywhere.com") == "/orders/123?tab=notes#section"
    assert _sanitize_relay_state("/", "techhub.pythonanywhere.com") == "/"


def test_sanitize_relay_state_blocks_login_loops() -> None:
    assert _sanitize_relay_state("/login", "techhub.pythonanywhere.com") == "/"
    assert _sanitize_relay_state("/login?next=/orders", "techhub.pythonanywhere.com") == "/"
    assert _sanitize_relay_state("/login#top", "techhub.pythonanywhere.com") == "/"


def test_sanitize_relay_state_blocks_external_urls() -> None:
    assert _sanitize_relay_state("https://evil.example.com/orders", "techhub.pythonanywhere.com") == "/"
    assert _sanitize_relay_state("http://techhub.pythonanywhere.com/orders", "techhub.pythonanywhere.com") == "/"


def test_sanitize_relay_state_allows_same_host_absolute_urls() -> None:
    assert _sanitize_relay_state("https://techhub.pythonanywhere.com/orders?x=1", "techhub.pythonanywhere.com") == "/orders?x=1"


if __name__ == "__main__":
    test_sanitize_relay_state_allows_internal_paths()
    print("[PASS] internal paths preserved")
    test_sanitize_relay_state_blocks_login_loops()
    print("[PASS] login redirects are normalized")
    test_sanitize_relay_state_blocks_external_urls()
    print("[PASS] external relay states are blocked")
    test_sanitize_relay_state_allows_same_host_absolute_urls()
    print("[PASS] same-host absolute relay states are normalized")
    print("[SUCCESS] auth relay state sanitization regressions passed")
