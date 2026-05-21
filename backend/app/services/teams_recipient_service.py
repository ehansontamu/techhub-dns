import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from app.config import settings
from app.database import get_db_session
from app.models.teams_notification import NotificationStatus, TeamsNotification
from app.models.user import User
from app.services.graph_service import graph_service
from app.utils.display_labels import _format_to_first_last
from app.utils.idempotency import check_recent_notification
from app.utils.timezone import to_utc_iso_z


logger = logging.getLogger(__name__)


class TeamsRecipientService:
    """
    Service for sending Teams notifications to order recipients via Graph API.
    Replaces the legacy Power Automate flow.
    """

    def __init__(self):
        # We check enabled status dynamically via SystemSettingService
        pass

    @property
    def is_enabled(self) -> bool:
        """Check if teams recipient notifications are enabled in system settings."""
        from app.services.system_setting_service import (
            SETTING_TEAMS_RECIPIENT_ENABLED,
            SystemSettingService,
        )

        return SystemSettingService.is_setting_enabled(SETTING_TEAMS_RECIPIENT_ENABLED)

    def is_configured(self) -> bool:
        """Check if service is configured and enabled."""
        if not self.is_enabled:
            return False
        return graph_service.is_configured()

    @staticmethod
    def _sanitize_reference(value: Optional[str]) -> str:
        reference = (value or "delivery").strip() or "delivery"
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in reference)

    def send_delivery_notification(
        self,
        recipient_email: str,
        recipient_name: str,
        order_number: str,
        delivery_runner: str,
        order_items: List[str] = None,
        order_numbers: List[str] = None,
        notification_group_key: str = None,
        force: bool = False,
    ) -> bool:
        """
        Send a delivery notification by dropping a JSON file in the SharePoint queue.
        This triggers a Power Automate Flow to send the actual Teams message.

        Args:
            recipient_email: Email address of the recipient
            recipient_name: Name of the recipient
            order_number: Primary order number (e.g., TH1234)
            delivery_runner: Name of the person delivering
            order_items: List of items in the order or delivery group
            order_numbers: Optional list of order numbers to aggregate into one message
            notification_group_key: Stable delivery-group key used for logging/dedupe
            force: If True, send even if disabled in settings
        """
        primary_order_number = (order_number or "").strip()
        normalized_order_numbers = [
            str(number).strip()
            for number in (order_numbers or [primary_order_number])
            if str(number).strip()
        ]
        if not normalized_order_numbers and primary_order_number:
            normalized_order_numbers = [primary_order_number]

        display_order_number = ", ".join(dict.fromkeys(normalized_order_numbers))
        payload_order_items = list(dict.fromkeys([str(item).strip() for item in (order_items or []) if str(item).strip()]))
        safe_reference = self._sanitize_reference(notification_group_key or primary_order_number)

        if not self.is_configured() and not force:
            logger.info(
                f"Teams recipient notifications disabled or not configured. Skipping for {display_order_number or primary_order_number}."
            )
            return False

        if not recipient_email:
            logger.warning(
                f"No recipient email provided for order {display_order_number or primary_order_number}. Skipping Teams notification."
            )
            return False

        # Construct payload matching the Power Automate "Parse JSON" schema.
        # Extra fields are ignored by the flow today but preserve aggregation context.
        payload = {
            "id": f"notif_{safe_reference}_{int(datetime.now().timestamp())}",
            "type": "delivery_notification",
            "recipientEmail": recipient_email,
            "recipientName": recipient_name,
            "orderNumber": display_order_number,
            "orderNumbers": normalized_order_numbers,
            "orderCount": len(normalized_order_numbers),
            "orderItems": payload_order_items,
            "deliveryRunner": delivery_runner,
            "createdAt": to_utc_iso_z(datetime.utcnow()),
        }
        if notification_group_key:
            payload["notificationGroupKey"] = notification_group_key

        try:
            file_content = json.dumps(payload, indent=2).encode("utf-8")
            filename = f"notification_{safe_reference}_{int(datetime.now().timestamp())}.json"

            sharepoint_url = graph_service.upload_file_to_sharepoint(
                file_content=file_content,
                file_name=filename,
                folder_path=settings.teams_notification_queue_folder,
                initiated_by="system-delivery-process",
            )

            if sharepoint_url:
                logger.info(
                    f"Queued Teams notification for {display_order_number or primary_order_number} (File: {filename})"
                )
                return True

            logger.error(
                f"Failed to queue Teams notification for {display_order_number or primary_order_number}"
            )
            return False

        except Exception as e:
            logger.error(
                f"Error queuing Teams notification for {display_order_number or primary_order_number}: {e}"
            )
            return False

    @staticmethod
    def _sort_orders_for_notification(orders: List) -> List:
        return sorted(
            orders,
            key=lambda order: (
                getattr(order, "delivery_sequence", None) is None,
                getattr(order, "delivery_sequence", 0) or 0,
                getattr(order, "inflow_order_id", "") or "",
                getattr(order, "id", "") or "",
            ),
        )

    @staticmethod
    def _notification_group_key(order) -> Tuple[str, str]:
        delivery_run_id = getattr(order, "delivery_run_id", None)
        recipient_email = (getattr(order, "recipient_contact", None) or "").strip().lower()
        group_scope = str(delivery_run_id) if delivery_run_id else f"order:{getattr(order, 'id', '')}"
        return group_scope, recipient_email

    @staticmethod
    def _resolve_delivery_runner(order, email_to_display: Dict[str, str]) -> str:
        assigned = (getattr(order, "assigned_deliverer", None) or "").strip()
        if assigned:
            if "@" in assigned:
                return email_to_display.get(assigned, assigned)
            return assigned

        delivery_run = getattr(order, "delivery_run", None)
        if delivery_run is not None:
            runner = (getattr(delivery_run, "runner", None) or "").strip()
            if runner:
                if "@" in runner:
                    return email_to_display.get(runner, runner)
                return runner

        return "TechHub Staff"

    @staticmethod
    def _collect_item_names(orders: List) -> List[str]:
        item_names: List[str] = []
        for order in orders:
            inflow_data = getattr(order, "inflow_data", None)
            if not isinstance(inflow_data, dict):
                continue
            for line in inflow_data.get("lines", []):
                if not isinstance(line, dict):
                    continue
                item_name = line.get("productName", "Item")
                if item_name:
                    item_names.append(str(item_name))
        return list(dict.fromkeys(item_names))

    @staticmethod
    def _has_recent_group_notification(db, orders: List) -> bool:
        for order in orders:
            order_id = getattr(order, "id", None)
            if not order_id:
                continue
            try:
                recent = check_recent_notification(db, UUID(str(order_id)), time_window_seconds=300)
            except (TypeError, ValueError):
                continue
            if recent:
                return True
        return False

    @staticmethod
    def _record_group_notifications(db, orders: List, notification_reference: str) -> None:
        sent_at = datetime.utcnow()
        for order in orders:
            order_id = getattr(order, "id", None)
            if not order_id:
                continue
            db.add(
                TeamsNotification(
                    order_id=str(order_id),
                    teams_message_id=notification_reference,
                    sent_at=sent_at,
                    status=NotificationStatus.SENT,
                    notification_type="delivery_notification",
                )
            )
        db.commit()

    def notify_orders_in_delivery(self, orders: List, force: bool = False):
        """
        Trigger Teams notifications for a list of orders in the background.
        Calculates item names and recipient info for each order.
        """
        if not self.is_configured() and not force:
            logger.info(
                "Teams recipient notifications skipped: service not configured or disabled"
            )
            return

        from app.services.background_tasks import run_in_background

        def _notify_task():
            # Batch fetch display names for deliverers with email addresses
            deliverer_emails = set()
            for order in orders:
                assigned = (getattr(order, "assigned_deliverer", None) or "").strip()
                if assigned and "@" in assigned:
                    deliverer_emails.add(assigned)

                delivery_run = getattr(order, "delivery_run", None)
                runner = (getattr(delivery_run, "runner", None) or "").strip() if delivery_run else ""
                if runner and "@" in runner:
                    deliverer_emails.add(runner)

            email_to_display = {}
            if deliverer_emails:
                db = get_db_session()
                try:
                    users = db.query(User).filter(User.email.in_(deliverer_emails)).all()
                    for user in users:
                        if user.display_name:
                            email_to_display[user.email] = _format_to_first_last(user.display_name)
                finally:
                    db.close()

            grouped_orders: Dict[Tuple[str, str], List] = defaultdict(list)
            for order in self._sort_orders_for_notification(list(orders)):
                group_scope, recipient_email = self._notification_group_key(order)
                if not recipient_email:
                    logger.warning(
                        f"Skipping Teams notification group {group_scope}: missing recipient email"
                    )
                    continue
                grouped_orders[(group_scope, recipient_email)].append(order)

            for (group_scope, recipient_email), grouped in grouped_orders.items():
                try:
                    if not grouped:
                        continue

                    recent_db = get_db_session()
                    try:
                        if self._has_recent_group_notification(recent_db, grouped):
                            logger.info(
                                f"Skipping Teams notification for group {group_scope}: recent notification already sent"
                            )
                            continue
                    finally:
                        recent_db.close()

                    item_names = self._collect_item_names(grouped)
                    order_numbers = [
                        str(getattr(order, "inflow_order_id", "")).strip()
                        for order in grouped
                        if str(getattr(order, "inflow_order_id", "")).strip()
                    ]
                    order_numbers = list(dict.fromkeys(order_numbers))
                    if not order_numbers:
                        order_numbers = [
                            str(getattr(grouped[0], "inflow_order_id", "")).strip() or "UNKNOWN"
                        ]

                    delivery_runner = self._resolve_delivery_runner(
                        grouped[0],
                        email_to_display,
                    )

                    recipient_name = grouped[0].recipient_name or "TechHub User"
                    success = self.send_delivery_notification(
                        recipient_email=recipient_email,
                        recipient_name=recipient_name,
                        order_number=order_numbers[0],
                        delivery_runner=delivery_runner,
                        order_items=item_names,
                        order_numbers=order_numbers,
                        notification_group_key=group_scope,
                        force=force,
                    )
                    if success:
                        record_db = get_db_session()
                        try:
                            self._record_group_notifications(
                                record_db,
                                grouped,
                                notification_reference=group_scope,
                            )
                        finally:
                            record_db.close()
                except Exception as ex:
                    logger.error(
                        f"Failed to trigger Teams notification for group {group_scope}: {ex}"
                    )

        run_in_background(_notify_task, task_name="batch_teams_notifications")


teams_recipient_service = TeamsRecipientService()
