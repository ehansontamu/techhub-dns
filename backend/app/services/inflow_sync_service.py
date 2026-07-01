import logging
from typing import Optional, Set

from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.services.inflow_service import InflowService
from app.services.order_service import OrderService

logger = logging.getLogger(__name__)


def refresh_active_remainder_parent_orders(
    db: Session,
    inflow_service: InflowService,
    order_service: OrderService,
    *,
    seen_order_numbers: Optional[Set[str]] = None,
    limit: int = 100,
) -> int:
    """Refresh known parent remainder rows by exact InFlow order number.

    The broad InFlow sync only scans recent started orders. Parent remainder rows
    can be older than that window, so refresh them explicitly to keep later pick
    batches visible after the first partial leg is created.
    """
    seen = seen_order_numbers or set()
    parent_remainders = (
        db.query(Order)
        .filter(Order.remainder_order_id.isnot(None))
        .filter(Order.parent_order_id.is_(None))
        .filter(Order.hidden_from_ops.is_(False))
        .filter(Order.inflow_order_id.isnot(None))
        .filter(Order.status != OrderStatus.DELIVERED.value)
        .order_by(Order.updated_at.desc())
        .limit(limit)
        .all()
    )

    refreshed_count = 0
    for order in parent_remainders:
        order_number = str(order.inflow_order_id or "").strip()
        if not order_number or order_number in seen:
            continue

        try:
            inflow_order = inflow_service.get_order_by_number_sync(order_number)
            if not inflow_order:
                logger.info(
                    "Active remainder refresh skipped for %s: not found in InFlow",
                    order_number,
                )
                continue
            if not inflow_service.is_started_and_picked(inflow_order):
                logger.info(
                    "Active remainder refresh skipped for %s: not started or no pickLines",
                    order_number,
                )
                continue

            order_service.create_order_from_inflow(inflow_order)
            seen.add(order_number)
            refreshed_count += 1
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to refresh active remainder parent %s from InFlow",
                order_number,
            )

    return refreshed_count
