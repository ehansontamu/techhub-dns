from app.utils.broadcast_dedup import broadcast_dedup
from sqlalchemy.exc import IntegrityError
from flask import Blueprint, request, jsonify, abort
from sqlalchemy.orm import Session
from datetime import datetime
import logging
import uuid
import json

from app.database import get_db
from app.services.inflow_service import InflowService
from app.services.order_service import OrderService
from app.services.background_tasks import BackgroundTaskService
from app.api.routes.orders import _broadcast_orders_sync
from app.schemas.inflow import (
    InflowSyncResponse,
    InflowSyncStatusResponse,
    WebhookRegisterRequest,
    WebhookResponse,
    WebhookListResponse,
)
from app.models.inflow_webhook import InflowWebhook, WebhookStatus
from app.config import settings
from app.api.auth_middleware import require_admin
from app.utils.exceptions import DNSApiError
from app.utils.timezone import to_utc_iso_z

logger = logging.getLogger(__name__)

bp = Blueprint("inflow", __name__)
bp.strict_slashes = False


def _webhook_json(status: str, message: str, http_status: int, **extra):
    payload = {"status": status, "message": message}
    payload.update(extra)
    return jsonify(payload), http_status


def _run_inflow_sync():
    """Background task: sync recent picked orders from Inflow with batch commits."""
    from app.models.order import Order

    inflow_service = InflowService()

    with get_db() as db:
        order_service = OrderService(db)

        inflow_orders = inflow_service.sync_recent_started_orders_sync(
            max_pages=3, per_page=100, target_matches=100
        )

        orders_created = 0
        orders_updated = 0

        for i, inflow_order in enumerate(inflow_orders):
            try:
                order_number = inflow_order.get("orderNumber")
                existing = (
                    db.query(Order)
                    .filter(Order.inflow_order_id == order_number)
                    .first()
                )

                order = order_service.create_order_from_inflow(inflow_order)
                if not existing:
                    orders_created += 1
                else:
                    orders_updated += 1

                # Batch commit every 20 orders to avoid losing progress on crash
                if (i + 1) % 20 == 0:
                    db.commit()
            except ValueError as e:
                continue
            except Exception as e:
                db.rollback()
                logger.error(
                    f"Error processing order {inflow_order.get('orderNumber')}: {e}",
                    exc_info=True,
                )
                continue

        # Final commit for remaining orders
        db.commit()

    # Broadcast order updates via SocketIO
    broadcast_dedup.request_broadcast(_broadcast_orders_sync)

    logger.info(
        f"Background Inflow sync completed: {orders_created} created, {orders_updated} updated"
    )


@bp.route("/sync", methods=["POST"])
@require_admin
def sync_orders():
    """Manually trigger Inflow sync (runs in background, returns immediately)."""
    BackgroundTaskService.run_async(
        _run_inflow_sync,
        task_name="inflow_manual_sync",
    )
    return (
        jsonify(
            {
                "status": "accepted",
                "message": "Inflow sync started in background",
            }
        ),
        202,
    )


@bp.route("/sync-status", methods=["GET"])
@require_admin
def get_sync_status():
    """Get Inflow sync status"""
    from app.models.order import Order

    with get_db() as db:
        total_orders = db.query(Order).count()

        response = InflowSyncStatusResponse(
            last_sync_at=None, total_orders=total_orders, sync_enabled=True
        )
        return jsonify(response.model_dump())


@bp.route("/webhook", methods=["POST"])
@bp.route("/webhook/order-update", methods=["POST"])
def inflow_webhook():
    """
    Receive webhook notifications from Inflow.
    This endpoint processes order events in real-time.
    """
    try:
        body = request.get_data()

        signature = (
            request.headers.get("x-inflow-hmac-sha256")
            or request.headers.get("X-Inflow-Signature")
            or request.headers.get("X-Webhook-Signature")
        )

        with get_db() as db:
            secrets: list[str] = [
                str(w.secret)
                for w in db.query(InflowWebhook)
                .filter(InflowWebhook.status == WebhookStatus.active)
                .all()
                if w.secret
            ]
            if (
                settings.inflow_webhook_secret
                and settings.inflow_webhook_secret not in secrets
            ):
                secrets.append(settings.inflow_webhook_secret)

            if signature and secrets:
                logger.info(
                    "Verifying webhook signature: secrets_count=%s", len(secrets)
                )
                inflow_service = InflowService()
                if not any(
                    inflow_service.verify_webhook_signature(body, signature, secret)
                    for secret in secrets
                ):
                    logger.warning("Webhook signature verification failed")
                    return jsonify(
                        {
                            "status": "unauthorized",
                            "message": "Invalid webhook signature",
                        }
                    ), 401
            elif secrets and not signature:
                logger.warning("Webhook signature missing")
                return jsonify(
                    {"status": "unauthorized", "message": "Missing webhook signature"}
                ), 401

            payload = json.loads(body.decode("utf-8"))

            event_type = (
                payload.get("event")
                or payload.get("type")
                or payload.get("EventType")
                or payload.get("eventType")
            )
            # Early extraction for structured logging (full extraction below)
            _early_order_number = (
                payload.get("orderNumber")
                or payload.get("order_number")
                or payload.get("OrderNumber")
            )
            logger.info("Webhook payload received: event=%s order_number=%s", event_type, _early_order_number)

            order_data = (
                payload.get("data")
                or payload.get("order")
                or payload.get("Order")
                or payload.get("salesOrder")
                or payload.get("SalesOrder")
                or payload
            )

            order_number = (
                order_data.get("orderNumber")
                or order_data.get("order_number")
                or order_data.get("OrderNumber")
                or order_data.get("orderId")
                or order_data.get("order_id")
                or order_data.get("OrderId")
                or payload.get("orderNumber")
                or payload.get("order_number")
                or payload.get("OrderNumber")
            )

            sales_order_id = None
            if not order_number:
                sales_order_id = (
                    order_data.get("salesOrderId")
                    or order_data.get("sales_order_id")
                    or order_data.get("SalesOrderId")
                    or order_data.get("id")
                    or payload.get("salesOrderId")
                    or payload.get("id")
                )

                if sales_order_id:
                    logger.info(
                        f"Found salesOrderId: {sales_order_id}, will attempt to fetch order details"
                    )

            if not order_number and not sales_order_id:
                logger.warning(
                    f"Webhook received without order number. Payload keys: {list(payload.keys())}"
                )
                return _webhook_json(
                    "ignored", "No order number or salesOrderId in payload", 400
                )

            if sales_order_id and not order_number:
                logger.info(f"Using salesOrderId {sales_order_id} to fetch order")

            inflow_service = InflowService()
            order_service = OrderService(db)

            inflow_order = None
            if order_number:
                inflow_order = inflow_service.get_order_by_number_sync(order_number)
            if not inflow_order and sales_order_id:
                logger.info(
                    f"Attempting to fetch order using salesOrderId: {sales_order_id}"
                )
                inflow_order = inflow_service.get_order_by_id_sync(str(sales_order_id))

            if not inflow_order:
                identifier = order_number or sales_order_id
                logger.warning(f"Order {identifier} not found in Inflow")
                return _webhook_json("not_found", f"Order {identifier} not found", 404)

            if not order_number and inflow_order:
                order_number = inflow_order.get("orderNumber")
                if order_number:
                    logger.info(
                        f"Extracted orderNumber {order_number} from fetched order"
                    )

            if not inflow_service.is_started_and_picked(inflow_order):
                identifier = order_number or sales_order_id or "unknown"
                logger.info(
                    f"Order {identifier} skipped (not started or no pickLines)"
                )
                return jsonify(
                    {
                        "status": "skipped",
                        "message": "Order is not eligible for sync",
                    }
                )

            try:
                order = order_service.create_order_from_inflow(inflow_order)

                webhook = (
                    db.query(InflowWebhook)
                    .filter(InflowWebhook.status == WebhookStatus.active)
                    .first()
                )

                if webhook:
                    webhook.last_received_at = datetime.utcnow()
                    webhook.failure_count = 0
                    db.commit()

                # Broadcast order update via SocketIO
                broadcast_dedup.request_broadcast(_broadcast_orders_sync)

                logger.info(f"Order {order_number} processed successfully via webhook")
                return jsonify({"status": "processed", "order_id": str(order.id)})

            except IntegrityError:
                db.rollback()
                from app.models.order import Order
                logger.info(
                    "Webhook IntegrityError for order %s — likely duplicate, attempting fetch",
                    order_number,
                )
                existing = (
                    db.query(Order).filter(Order.inflow_order_id == order_number).first()
                )
                if existing:
                    return jsonify({"status": "processed", "order_id": str(existing.id)})
                return _webhook_json("error", "Duplicate order conflict", 409)
            except DNSApiError as e:
                logger.warning(
                    "Webhook rejected order %s with %s: %s",
                    order_number,
                    e.code,
                    e.message,
                )

                webhook = (
                    db.query(InflowWebhook)
                    .filter(InflowWebhook.status == WebhookStatus.active)
                    .first()
                )

                if webhook:
                    webhook.failure_count += 1
                    if webhook.failure_count >= 10:
                        webhook.status = WebhookStatus.failed
                    db.commit()

                return _webhook_json("error", e.message, e.status_code, code=e.code)
            except ValueError as e:
                logger.warning(
                    f"Webhook received invalid data for order {order_number}: {e}"
                )
                return _webhook_json("error", str(e), 400)
            except Exception as e:
                logger.error(
                    f"Error processing order {order_number}: {e}", exc_info=True
                )

                webhook = (
                    db.query(InflowWebhook)
                    .filter(InflowWebhook.status == WebhookStatus.active)
                    .first()
                )

                if webhook:
                    webhook.failure_count += 1
                    if webhook.failure_count >= 10:
                        webhook.status = WebhookStatus.failed
                    db.commit()

                logger.error("Webhook processing failed: %s", e, exc_info=True)
                return _webhook_json("error", "Internal server error", 500)

    except json.JSONDecodeError as e:
        logger.warning(f"Webhook payload was not valid JSON: {e}")
        return _webhook_json("error", "Invalid webhook payload", 400)

    except Exception as e:
        logger.error("Webhook processing failed: %s", e, exc_info=True)
        return _webhook_json("error", "Internal server error", 500)


@bp.route("/webhooks/register", methods=["POST"])
@require_admin
def register_webhook():
    """Register a new webhook with Inflow"""
    try:
        data = request.get_json()
        req = WebhookRegisterRequest(**data)
        inflow_service = InflowService()

        # Clean up remote subscriptions
        try:
            existing_remote = inflow_service.list_webhooks_sync()
            normalized_url = req.url.rstrip("/")
            for item in existing_remote:
                remote_url = (item.get("url") or "").rstrip("/")
                if remote_url == normalized_url:
                    remote_id = (
                        item.get("webHookSubscriptionId")
                        or item.get("id")
                        or item.get("webHookId")
                        or item.get("webhookId")
                    )
                    if remote_id:
                        inflow_service.delete_webhook_sync(str(remote_id))
        except Exception as e:
            logger.warning(f"Failed to clean up remote webhooks for {req.url}: {e}")

        result = inflow_service.register_webhook_sync(req.url, req.events)

        with get_db() as db:
            db.query(InflowWebhook).filter(
                InflowWebhook.status == WebhookStatus.active
            ).update({"status": WebhookStatus.inactive})

            webhook_id = (
                result.get("webHookSubscriptionId")
                or result.get("id")
                or str(uuid.uuid4())
            )
            webhook = InflowWebhook(
                webhook_id=webhook_id,
                url=req.url,
                events=req.events,
                status=WebhookStatus.active,
                secret=result.get("secret") or settings.inflow_webhook_secret,
            )

            db.add(webhook)
            db.commit()
            db.refresh(webhook)

            response = WebhookResponse(
                id=str(webhook.id),
                webhook_id=webhook.webhook_id,
                url=webhook.url,
                events=webhook.events,
                status=webhook.status.value,
                last_received_at=webhook.last_received_at,
                failure_count=webhook.failure_count,
                created_at=webhook.created_at,
                updated_at=webhook.updated_at,
            )
            return jsonify(response.model_dump())
    except Exception as e:
        logger.error(f"Failed to register webhook: {e}", exc_info=True)
        abort(500, description=str(e))


@bp.route("/webhooks", methods=["GET"])
@require_admin
def list_webhooks():
    """List all registered webhooks"""
    with get_db() as db:
        webhooks = db.query(InflowWebhook).all()

        response = WebhookListResponse(
            webhooks=[
                WebhookResponse(
                    id=str(w.id),
                    webhook_id=w.webhook_id,
                    url=w.url,
                    events=w.events,
                    status=w.status.value,
                    last_received_at=w.last_received_at,
                    failure_count=w.failure_count,
                    created_at=w.created_at,
                    updated_at=w.updated_at,
                )
                for w in webhooks
            ]
        )
        return jsonify(response.model_dump())


@bp.route("/webhooks/defaults", methods=["GET"])
@require_admin
def get_webhook_defaults():
    """Get default webhook URL and events from settings"""
    return jsonify(
        {"url": settings.inflow_webhook_url, "events": settings.inflow_webhook_events}
    )


@bp.route("/webhooks/<webhook_id>", methods=["DELETE"])
@require_admin
def delete_webhook(webhook_id):
    """Delete a webhook registration"""
    try:
        with get_db() as db:
            webhook = (
                db.query(InflowWebhook)
                .filter(InflowWebhook.webhook_id == webhook_id)
                .first()
            )

            if not webhook:
                abort(404, description="Webhook not found")

            inflow_service = InflowService()
            inflow_service.delete_webhook_sync(webhook_id)

            db.delete(webhook)
            db.commit()

            return jsonify({"success": True, "message": "Webhook deleted"})
    except Exception as e:
        if "404" in str(e):
            abort(404, description="Webhook not found")
        logger.error(f"Failed to delete webhook: {e}", exc_info=True)
        abort(500, description=str(e))


@bp.route("/webhooks/test", methods=["POST"])
@require_admin
def test_webhook():
    """Test webhook endpoint (sends a test payload)"""
    try:
        with get_db() as db:
            webhook = (
                db.query(InflowWebhook)
                .filter(InflowWebhook.status == WebhookStatus.active)
                .first()
            )

            if not webhook:
                abort(404, description="No active webhook found")

            return jsonify(
                {
                    "success": True,
                    "message": "Webhook endpoint is configured",
                    "webhook_url": webhook.url,
                    "last_received_at": to_utc_iso_z(webhook.last_received_at),
                }
            )
    except Exception as e:
        if "404" in str(e):
            abort(404, description="No active webhook found")
        logger.error(f"Failed to test webhook: {e}", exc_info=True)
        abort(500, description=str(e))
