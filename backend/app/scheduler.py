from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import asyncio
from app.database import get_db_session
from app.services.inflow_service import InflowService
from app.services.inventory_reorder_service import InventoryReorderService
from app.models.inflow_webhook import InflowWebhook, WebhookStatus
from app.config import settings
from app.api.routes.inflow import _run_inflow_sync
import logging
import threading

logger = logging.getLogger(__name__)

_WEBHOOK_HEALTH_STALE_THRESHOLD = timedelta(hours=2)
_WEBHOOK_RECONCILE_INTERVAL_MINUTES = 30
_WEBHOOK_HEALTH_CHECK_INTERVAL_MINUTES = 60
_DEFAULT_INVENTORY_REORDER_REFRESH_TIMES = "7:30,12:00,15:00"
_INFLOW_EVENT_MAPPING = {
    "orderCreated": "salesOrder.created",
    "orderUpdated": "salesOrder.updated",
    "orderStatusChanged": "salesOrder.updated",
}
inventory_reorder_service = InventoryReorderService(settings)


def _normalize_webhook_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


def _map_inflow_events(events: list[str]) -> list[str]:
    mapped = [_INFLOW_EVENT_MAPPING.get(event, event) for event in events]
    return list(dict.fromkeys(mapped))


def _remote_webhook_matches_target(
    item: dict[str, object], target_url: str, desired_events: list[str]
) -> bool:
    remote_url = _normalize_webhook_url(str(item.get("url") or ""))
    if remote_url != target_url:
        return False

    remote_events = item.get("events") or []
    if not isinstance(remote_events, list):
        return False

    return any(str(event) in desired_events for event in remote_events)


def _upsert_local_webhook(
    db: Session,
    webhook_id: str,
    target_url: str,
    events: list[str],
    secret: str | None,
) -> None:
    existing = (
        db.query(InflowWebhook)
        .filter(InflowWebhook.webhook_id == webhook_id)
        .first()
    )

    active_rows = (
        db.query(InflowWebhook)
        .filter(InflowWebhook.status == WebhookStatus.active)
        .all()
    )
    for row in active_rows:
        if row.webhook_id != webhook_id:
            row.status = WebhookStatus.inactive

    if existing is None:
        existing = InflowWebhook(
            webhook_id=webhook_id,
            url=target_url,
            events=events,
            status=WebhookStatus.active,
            secret=secret,
        )
        db.add(existing)
    else:
        existing.url = target_url
        existing.events = events
        existing.status = WebhookStatus.active
        existing.secret = secret


def sync_inflow_orders():
    """Background task to sync orders from Inflow.

    Delegates to the shared implementation in inflow_routes so we
    maintain a single code path for manual and scheduled syncs.
    """
    try:
        _run_inflow_sync()
    except Exception as e:
        logger.error(f"Inflow sync failed: {e}", exc_info=True)


def _get_configured_inventory_reorder_refresh_times() -> str:
    configured_times = getattr(
        settings, "inventory_reorder_scheduled_refresh_times", None
    )
    if configured_times:
        return str(configured_times)

    legacy_hours = getattr(settings, "inventory_reorder_scheduled_refresh_hours", None)
    if legacy_hours:
        return str(legacy_hours)

    return _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES


def _parse_inventory_reorder_refresh_times(raw_value: str | None) -> list[tuple[int, int]]:
    raw = (raw_value or "").strip()
    if not raw:
        raw = _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES

    times: list[tuple[int, int]] = []
    for item in raw.split(","):
        trimmed = item.strip()
        if not trimmed:
            continue

        parts = trimmed.split(":", 1)
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) == 2 else 0
        except ValueError:
            logger.warning(
                "Invalid inventory reorder scheduled refresh time %r; using default %s",
                trimmed,
                _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES,
            )
            return _parse_inventory_reorder_refresh_times(
                _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES
            )

        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            logger.warning(
                "Inventory reorder scheduled refresh time %s is out of range; using default %s",
                trimmed,
                _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES,
            )
            return _parse_inventory_reorder_refresh_times(
                _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES
            )

        candidate = (hour, minute)
        if candidate not in times:
            times.append(candidate)

    return times or _parse_inventory_reorder_refresh_times(
        _DEFAULT_INVENTORY_REORDER_REFRESH_TIMES
    )


def _get_inventory_reorder_refresh_timezone():
    timezone_name = (
        settings.inventory_reorder_scheduled_refresh_timezone or "America/Chicago"
    ).strip()
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown inventory reorder scheduled refresh timezone %r; using America/Chicago",
            timezone_name,
        )
        return ZoneInfo("America/Chicago")


def scheduled_inventory_reorder_refresh():
    """Background task to refresh the cached inventory reorder summary."""
    cooldown = inventory_reorder_service.get_refresh_cooldown()
    if cooldown["active"]:
        logger.info(
            "Scheduled inventory reorder refresh skipped because cooldown is active: remaining_seconds=%s",
            cooldown["remaining_seconds"],
        )
        return

    job = inventory_reorder_service.run_refresh_sync(trigger="scheduled")
    if job.get("status") == "done":
        logger.info(
            "Scheduled inventory reorder refresh completed: job_id=%s result_path=%s",
            job.get("job_id"),
            job.get("result_path"),
        )
        return

    if job.get("status") in {"queued", "running"}:
        logger.info(
            "Scheduled inventory reorder refresh skipped because a refresh is already running: job_id=%s",
            job.get("job_id"),
        )
        return

    logger.error(
        "Scheduled inventory reorder refresh failed: job_id=%s error=%s",
        job.get("job_id"),
        job.get("error"),
    )


def _has_active_webhook(db: Session) -> bool:
    """Check if there's an active webhook registered"""
    webhook = (
        db.query(InflowWebhook)
        .filter(InflowWebhook.status == WebhookStatus.active)
        .first()
    )
    return webhook is not None


def _check_webhook_health(db: Session):
    """Check webhook health and alert if no events received recently."""
    webhook = (
        db.query(InflowWebhook)
        .filter(InflowWebhook.status == WebhookStatus.active)
        .first()
    )

    if webhook is None:
        logger.warning("Webhook health check: no active webhook record found")
        return

    issues: list[str] = []
    if webhook.status != WebhookStatus.active:
        issues.append(f"status={webhook.status.value}")

    target_url = _normalize_webhook_url(settings.inflow_webhook_url)
    webhook_url = _normalize_webhook_url(webhook.url)
    if target_url and webhook_url != target_url:
        issues.append("url_mismatch")

    if settings.inflow_webhook_secret and webhook.secret:
        if webhook.secret != settings.inflow_webhook_secret:
            issues.append("secret_mismatch")
    elif settings.inflow_webhook_secret and not webhook.secret:
        issues.append("missing_secret")

    if webhook.last_received_at:
        time_since_last = datetime.utcnow() - webhook.last_received_at
        if time_since_last > _WEBHOOK_HEALTH_STALE_THRESHOLD:
            issues.append(f"stale_after={time_since_last}")
            logger.warning(
                "Webhook health check: no events received in %s. Last received: %s",
                time_since_last,
                webhook.last_received_at,
            )
    else:
        issues.append("never_received")
        logger.warning("Webhook health check: no events received since registration")

    if issues:
        logger.warning(
            "Webhook health check: webhook health degraded (%s) webhook_id=%s url=%s",
            ", ".join(issues),
            webhook.webhook_id,
            webhook.url,
        )
    else:
        logger.info(
            "Webhook health check: healthy webhook_id=%s url=%s",
            webhook.webhook_id,
            webhook.url,
        )


def webhook_health_check():
    """Background task to check webhook health (sync version)"""
    db = get_db_session()
    try:
        _check_webhook_health(db)
    except Exception as e:
        logger.error(f"Webhook health check failed: {e}", exc_info=True)
    finally:
        db.close()


def reconcile_inflow_webhook_state() -> None:
    """Reconcile the configured Inflow webhook against remote and local state."""
    if not settings.inflow_webhook_url:
        logger.warning(
            "Webhook reconciliation skipped: INFLOW_WEBHOOK_URL not configured"
        )
        return

    if not settings.inflow_webhook_events:
        logger.warning("Webhook reconciliation skipped: no events configured")
        return

    target_url = _normalize_webhook_url(settings.inflow_webhook_url)
    desired_events = _map_inflow_events(settings.inflow_webhook_events)

    db = get_db_session()
    local_active_webhook = None
    try:
        local_active_webhook = (
            db.query(InflowWebhook)
            .filter(
                InflowWebhook.status == WebhookStatus.active,
                InflowWebhook.url == target_url,
            )
            .first()
        )
    finally:
        db.close()

    async def reconcile():
        service = InflowService()
        remote_webhooks = []
        try:
            remote_webhooks = await service.list_webhooks()
        except Exception as exc:
            logger.warning("Could not list remote webhooks: %s", exc)
            if local_active_webhook is None:
                return None
            return {
                "webHookSubscriptionId": local_active_webhook.webhook_id,
                "url": target_url,
            }

        matching_remote = [
            item
            for item in remote_webhooks
            if _remote_webhook_matches_target(item, target_url, desired_events)
        ]

        if len(matching_remote) > 1:
            logger.warning(
                "Webhook reconciliation: found %s matching remote subscriptions for %s; deleting duplicates",
                len(matching_remote),
                target_url,
            )
            keep = matching_remote[0]
            for duplicate in matching_remote[1:]:
                duplicate_id = (
                    duplicate.get("webHookSubscriptionId")
                    or duplicate.get("id")
                    or duplicate.get("webhookId")
                )
                if duplicate_id:
                    await service.delete_webhook(str(duplicate_id))
            matching_remote = [keep]

        secret_matches = bool(
            local_active_webhook
            and settings.inflow_webhook_secret
            and local_active_webhook.secret == settings.inflow_webhook_secret
        )

        if matching_remote and secret_matches:
            remote_webhook_id = (
                matching_remote[0].get("webHookSubscriptionId")
                or matching_remote[0].get("id")
                or matching_remote[0].get("webhookId")
            )
            logger.info(
                "Webhook already reconciled for %s (local ID: %s, remote ID: %s)",
                target_url,
                local_active_webhook.webhook_id if local_active_webhook else "none",
                remote_webhook_id or "unknown",
            )
            return {
                "webHookSubscriptionId": remote_webhook_id,
                "url": target_url,
            }

        if matching_remote and not secret_matches:
            logger.warning(
                "Webhook secret drift detected for %s; re-registering target webhook",
                target_url,
            )
            for remote in matching_remote:
                remote_id = (
                    remote.get("webHookSubscriptionId")
                    or remote.get("id")
                    or remote.get("webhookId")
                )
                if remote_id:
                    await service.delete_webhook(str(remote_id))

        if local_active_webhook and not matching_remote:
            logger.warning(
                "Local webhook record exists for %s but no matching remote subscription was found; re-registering",
                target_url,
            )
        elif not local_active_webhook:
            logger.info("Webhook reconciliation registering target webhook: %s", target_url)

        return await service.register_webhook(target_url, settings.inflow_webhook_events)

    try:
        result = asyncio.run(reconcile())
        if not result:
            return

        webhook_id = result.get("webHookSubscriptionId") or result.get("id")
        if not webhook_id:
            logger.error("Webhook reconciliation did not return an ID: %s", result)
            return

        db = get_db_session()
        try:
            _upsert_local_webhook(
                db,
                str(webhook_id),
                target_url,
                settings.inflow_webhook_events,
                result.get("secret") or settings.inflow_webhook_secret,
            )
            db.commit()
            logger.info("Webhook reconciliation succeeded: %s", webhook_id)
        except Exception as exc:
            db.rollback()
            logger.error("Failed to save reconciled webhook to database: %s", exc)
        finally:
            db.close()
    except Exception as exc:
        logger.error("Webhook reconciliation failed: %s", exc)


def start_scheduler():
    """Start the APScheduler for periodic Inflow sync"""
    scheduler = BackgroundScheduler()
    poll_interval = None

    # Check if polling sync is enabled
    if settings.inflow_polling_sync_enabled:
        # Check webhook status to determine polling frequency
        db = get_db_session()
        try:
            has_webhook = _has_active_webhook(db)

            if has_webhook and settings.inflow_webhook_enabled:
                # Webhooks active: poll less frequently (backup/catch-up)
                poll_interval = 30
                logger.info(
                    "Webhooks enabled - using reduced polling frequency as backup"
                )
            else:
                # No webhooks: use normal polling frequency
                poll_interval = 5
                logger.info("Webhooks not enabled - using normal polling frequency")
        finally:
            db.close()

        # Sync job (backup polling)
        scheduler.add_job(
            sync_inflow_orders,
            trigger=IntervalTrigger(minutes=poll_interval),
            id="inflow_sync",
            name="Sync orders from Inflow (backup polling)",
            replace_existing=True,
            next_run_time=datetime.now(),
        )
    else:
        logger.info("Inflow polling sync is disabled via INFLOW_POLLING_SYNC_ENABLED")

    if settings.inflow_webhook_enabled:
        scheduler.add_job(
            reconcile_inflow_webhook_state,
            trigger=IntervalTrigger(minutes=_WEBHOOK_RECONCILE_INTERVAL_MINUTES),
            id="inflow_webhook_reconcile",
            name="Reconcile Inflow webhook registration",
            replace_existing=True,
            next_run_time=datetime.now() + timedelta(minutes=_WEBHOOK_RECONCILE_INTERVAL_MINUTES),
        )
        scheduler.add_job(
            webhook_health_check,
            trigger=IntervalTrigger(minutes=_WEBHOOK_HEALTH_CHECK_INTERVAL_MINUTES),
            id="webhook_health_check",
            name="Webhook health check",
            replace_existing=True,
            next_run_time=datetime.now() + timedelta(minutes=_WEBHOOK_HEALTH_CHECK_INTERVAL_MINUTES),
        )

    if settings.inventory_reorder_scheduled_refresh_enabled:
        refresh_times = _parse_inventory_reorder_refresh_times(
            _get_configured_inventory_reorder_refresh_times()
        )
        refresh_timezone = _get_inventory_reorder_refresh_timezone()
        for hour, minute in refresh_times:
            scheduler.add_job(
                scheduled_inventory_reorder_refresh,
                trigger=CronTrigger(
                    hour=hour,
                    minute=minute,
                    timezone=refresh_timezone,
                ),
                id=f"inventory_reorder_refresh_{hour:02d}_{minute:02d}",
                name=f"Refresh inventory reorder summary ({hour:02d}:{minute:02d})",
                replace_existing=True,
            )
        logger.info(
            "Inventory reorder scheduled refresh enabled at time(s) %s timezone=%s",
            ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in refresh_times),
            settings.inventory_reorder_scheduled_refresh_timezone,
        )
    else:
        logger.info(
            "Inventory reorder scheduled refresh disabled via INVENTORY_REORDER_SCHEDULED_REFRESH_ENABLED"
        )

    scheduler.start()
    if settings.inflow_polling_sync_enabled and poll_interval:
        logger.info(
            f"Scheduler started - Inflow polling sync enabled (every {poll_interval} minutes)"
        )
    else:
        logger.info("Scheduler started - Inflow polling sync disabled")
    return scheduler


def auto_register_inflow_webhook() -> None:
    """Register the configured Inflow webhook when the scheduler starts."""
    reconcile_inflow_webhook_state()
