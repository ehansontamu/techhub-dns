"""Import the authoritative compatibility editor seed JSON.

Run from backend/ after applying Alembic migrations:
    python scripts/import_compatibility_editor.py C:/path/CompatibilityStaging.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import get_db_session
from app.services.compatibility_editor_service import import_payload


DEFAULT_SEED = BACKEND_ROOT / "seeds" / "compatibility_initial.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import compatibility data into the collaborative editor database."
    )
    parser.add_argument("json_path", nargs="?", type=Path, default=DEFAULT_SEED)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an already initialized dataset (destructive).",
    )
    parser.add_argument("--actor", default="compatibility-seed-import")
    args = parser.parse_args()

    source_path = args.json_path.expanduser().resolve()
    raw = source_path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        parser.error(f"Invalid UTF-8 JSON: {exc}")

    db = get_db_session()
    try:
        document = import_payload(
            db,
            payload,
            actor=args.actor,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            replace=args.replace,
        )
    finally:
        db.close()

    data = document["data"]
    print(
        "Imported compatibility editor revision "
        f"{document['revision']}: {len(data['computers'])} computers, "
        f"{len(data['docks'])} docks."
    )
    print("WebDAV publication is pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
