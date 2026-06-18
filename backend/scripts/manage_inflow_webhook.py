"""
Manage Inflow webhook subscriptions (list/register/delete/reset).

Usage:
    cd backend
    .venv\\Scripts\\activate  # Windows
    # or
    source .venv/bin/activate  # Linux/Mac

    # List remote webhooks
    python scripts/manage_inflow_webhook.py list

    # List local DB webhooks
    python scripts/manage_inflow_webhook.py list --local

    # Register (optional cleanup by URL)
    python scripts/manage_inflow_webhook.py register --url https://your-app.com/api/inflow/webhook \\
        --events orderCreated,orderUpdated --cleanup-url

    # Delete by URL
    python scripts/manage_inflow_webhook.py delete --url https://your-app.com/api/inflow/webhook

    # One-step reset: delete existing by URL, then register new
    python scripts/manage_inflow_webhook.py reset --url https://your-app.com/api/inflow/webhook \\
        --events orderCreated,orderUpdated
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.database import SessionLocal
from app.models.inflow_webhook import InflowWebhook, WebhookStatus
from app.services.inflow_service import InflowService


def normalize_url(value: Optional[str]) -> str:
    return (value or "").strip().rstrip("/")


def parse_events(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def extract_webhook_id(item: Dict[str, Any]) -> Optional[str]:
    for key in ("webHookSubscriptionId", "id", "webHookId", "webhookId"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def format_events(events: Any) -> str:
    if isinstance(events, list):
        return ", ".join(str(event) for event in events)
    return str(events or "")


async def list_remote_webhooks(service: InflowService) -> List[Dict[str, Any]]:
    return await service.list_webhooks()


def list_local_webhooks() -> List[InflowWebhook]:
    db = SessionLocal()
    try:
        return db.query(InflowWebhook).order_by(InflowWebhook.created_at.desc()).all()
    finally:
        db.close()


async def delete_remote_by_url(service: InflowService, url: str) -> List[str]:
    normalized = normalize_url(url)
    deleted: List[str] = []
    webhooks = await list_remote_webhooks(service)
    for item in webhooks:
        remote_url = normalize_url(item.get("url"))
        if remote_url == normalized:
            webhook_id = extract_webhook_id(item)
            if webhook_id:
                await service.delete_webhook(webhook_id)
                deleted.append(webhook_id)
    return deleted


async def delete_remote_by_id(service: InflowService, webhook_id: str) -> List[str]:
    await service.delete_webhook(webhook_id)
    return [webhook_id]


def deactivate_local_by_id(webhook_id: str) -> int:
    db = SessionLocal()
    try:
        updated = db.query(InflowWebhook).filter(
            InflowWebhook.webhook_id == webhook_id
        ).update({"status": WebhookStatus.inactive})
        db.commit()
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def deactivate_local_by_url(url: str) -> int:
    normalized = normalize_url(url)
    db = SessionLocal()
    try:
        webhooks = db.query(InflowWebhook).all()
        updated = 0
        for webhook in webhooks:
            if normalize_url(webhook.url) == normalized:
                webhook.status = WebhookStatus.inactive
                updated += 1
        db.commit()
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def upsert_local_webhook(
    webhook_id: str,
    url: str,
    events: List[str],
    secret: Optional[str],
    keep_existing: bool
) -> None:
    db = SessionLocal()
    try:
        if not keep_existing:
            db.query(InflowWebhook).filter(
                InflowWebhook.status == WebhookStatus.active
            ).update({"status": WebhookStatus.inactive})

        webhook = db.query(InflowWebhook).filter(
            InflowWebhook.webhook_id == webhook_id
        ).first()

        if webhook:
            webhook.url = url
            webhook.events = events
            webhook.status = WebhookStatus.active
            if secret:
                webhook.secret = secret
        else:
            webhook = InflowWebhook(
                webhook_id=webhook_id,
                url=url,
                events=events,
                status=WebhookStatus.active,
                secret=secret
            )
            db.add(webhook)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def handle_list(args: argparse.Namespace) -> None:
    if args.local:
        webhooks = list_local_webhooks()
        if not webhooks:
            print("No local webhooks found.")
            return
        print("Local webhooks:")
        for webhook in webhooks:
            print(f"- {webhook.webhook_id} [{webhook.status.value}]")
            print(f"  URL: {webhook.url}")
            print(f"  Events: {format_events(webhook.events)}")
        return

    service = InflowService()
    webhooks = await list_remote_webhooks(service)
    if not webhooks:
        print("No remote webhooks found.")
        return
    print("Remote webhooks:")
    for item in webhooks:
        webhook_id = extract_webhook_id(item) or "(missing id)"
        url = item.get("url", "")
        events = format_events(item.get("events"))
        print(f"- {webhook_id}")
        print(f"  URL: {url}")
        if events:
            print(f"  Events: {events}")


async def handle_delete(args: argparse.Namespace) -> None:
    service = InflowService()
    deleted: List[str] = []

    if args.id:
        deleted = await delete_remote_by_id(service, args.id)
        if args.local:
            deactivate_local_by_id(args.id)
    elif args.url:
        deleted = await delete_remote_by_url(service, args.url)
        if args.local:
            deactivate_local_by_url(args.url)

    if deleted:
        print(f"Deleted remote webhooks: {', '.join(deleted)}")
    else:
        print("No remote webhooks deleted.")


async def handle_register(args: argparse.Namespace) -> None:
    if not args.url:
        raise SystemExit("Webhook URL is required. Use --url or set INFLOW_WEBHOOK_URL.")

    events = parse_events(args.events)
    if not events:
        raise SystemExit("At least one event is required. Use --events or set INFLOW_WEBHOOK_EVENTS.")

    service = InflowService()
    if args.cleanup_url:
        await delete_remote_by_url(service, args.url)

    result = await service.register_webhook(args.url, events)
    webhook_id = (
        result.get("webHookSubscriptionId")
        or result.get("id")
    )
    if not webhook_id:
        raise RuntimeError(f"Webhook registration did not return an ID: {result}")

    secret = result.get("secret") or settings.inflow_webhook_secret
    upsert_local_webhook(
        webhook_id=webhook_id,
        url=args.url,
        events=events,
        secret=secret,
        keep_existing=args.keep_existing
    )

    print("Webhook registered successfully.")
    print(f"  Webhook ID: {webhook_id}")
    print(f"  URL: {args.url}")
    print(f"  Events: {', '.join(events)}")
    if secret:
        print(f"  Secret: {secret}")
    else:
        print("  Secret: (not returned by Inflow)")


async def handle_reset(args: argparse.Namespace) -> None:
    if not args.url:
        raise SystemExit("Webhook URL is required. Use --url or set INFLOW_WEBHOOK_URL.")

    events = parse_events(args.events)
    if not events:
        raise SystemExit("At least one event is required. Use --events or set INFLOW_WEBHOOK_EVENTS.")

    service = InflowService()
    deleted = await delete_remote_by_url(service, args.url)
    if deleted:
        print(f"Deleted remote webhooks: {', '.join(deleted)}")

    result = await service.register_webhook(args.url, events)
    webhook_id = (
        result.get("webHookSubscriptionId")
        or result.get("id")
    )
    if not webhook_id:
        raise RuntimeError(f"Webhook registration did not return an ID: {result}")

    secret = result.get("secret") or settings.inflow_webhook_secret
    upsert_local_webhook(
        webhook_id=webhook_id,
        url=args.url,
        events=events,
        secret=secret,
        keep_existing=False
    )

    print("Webhook reset complete.")
    print(f"  Webhook ID: {webhook_id}")
    print(f"  URL: {args.url}")
    print(f"  Events: {', '.join(events)}")
    if secret:
        print(f"  Secret: {secret}")
    else:
        print("  Secret: (not returned by Inflow)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Inflow webhooks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List webhooks.")
    list_parser.add_argument("--local", action="store_true", help="List local DB webhooks.")

    delete_parser = subparsers.add_parser("delete", help="Delete webhooks.")
    group = delete_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="Delete a webhook by ID.")
    group.add_argument("--url", help="Delete webhooks matching a URL.")
    delete_parser.add_argument("--local", action="store_true", help="Also deactivate local DB webhooks.")

    register_parser = subparsers.add_parser("register", help="Register a webhook.")
    register_parser.add_argument(
        "--url",
        default=settings.inflow_webhook_url,
        help="Public webhook URL (defaults to INFLOW_WEBHOOK_URL)."
    )
    register_parser.add_argument(
        "--events",
        default=",".join(settings.inflow_webhook_events),
        help="Comma-separated events (defaults to INFLOW_WEBHOOK_EVENTS)."
    )
    register_parser.add_argument(
        "--cleanup-url",
        action="store_true",
        help="Delete remote webhooks that match the URL before registering."
    )
    register_parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Keep existing local webhooks active."
    )

    reset_parser = subparsers.add_parser("reset", help="Delete by URL then register new.")
    reset_parser.add_argument(
        "--url",
        default=settings.inflow_webhook_url,
        help="Public webhook URL (defaults to INFLOW_WEBHOOK_URL)."
    )
    reset_parser.add_argument(
        "--events",
        default=",".join(settings.inflow_webhook_events),
        help="Comma-separated events (defaults to INFLOW_WEBHOOK_EVENTS)."
    )

    return parser


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "list":
        await handle_list(args)
    elif args.command == "delete":
        await handle_delete(args)
    elif args.command == "register":
        await handle_register(args)
    elif args.command == "reset":
        await handle_reset(args)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
