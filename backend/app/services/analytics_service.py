from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import case, func

from app.models.order import Order, OrderStatus
from app.models.delivery_run import DeliveryRun, DeliveryRunStatus
from app.models.audit_log import AuditLog


class AnalyticsService:
    """Service for analytics and dashboard data aggregation"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _is_business_day(date_value: Any) -> bool:
        if isinstance(date_value, datetime):
            target_date = date_value.date()
        elif hasattr(date_value, "weekday"):
            target_date = date_value
        else:
            target_date = datetime.fromisoformat(str(date_value)).date()

        return target_date.weekday() < 5

    def _visible_orders_query(self, *entities):
        return self.db.query(*entities).filter(Order.hidden_from_ops.is_(False))

    def get_order_status_counts(self) -> Dict[str, int]:
        """
        Get count of orders grouped by status.

        Returns:
            Dict mapping status names to counts, e.g. {"picked": 5, "qa": 3, "delivered": 10}
        """
        results = (
            self._visible_orders_query(Order.status, func.count(Order.id).label("count"))
            .group_by(Order.status)
            .all()
        )

        if not results:
            return {}

        return {status: count for status, count in results}

    def get_delivery_performance(self) -> Dict[str, int]:
        """
        Get delivery performance metrics.

        Returns:
            Dict with keys:
            - active_runs: count of delivery runs with status=Active
            - completed_today: count of orders delivered today
            - ready_for_delivery: count of orders with status=pre-delivery
        """
        # Count active delivery runs
        active_runs = (
            self.db.query(func.count(DeliveryRun.id))
            .filter(DeliveryRun.status == DeliveryRunStatus.ACTIVE.value)
            .scalar()
            or 0
        )

        # Count orders completed today (delivered status + delivered today)
        today_start = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        completed_today = (
            self._visible_orders_query(func.count(Order.id))
            .filter(
                Order.status == OrderStatus.DELIVERED.value,
                Order.updated_at >= today_start,
            )
            .scalar()
            or 0
        )

        # Count orders ready for delivery (pre-delivery status)
        ready_for_delivery = (
            self._visible_orders_query(func.count(Order.id))
            .filter(Order.status == OrderStatus.PRE_DELIVERY.value)
            .scalar()
            or 0
        )

        return {
            "active_runs": active_runs,
            "completed_today": completed_today,
            "ready_for_delivery": ready_for_delivery,
        }

    def get_recent_activity(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get recent activity combining recent orders and audit log entries.

        Returns recent orders (by created_at) and status changes (from audit logs),
        merged and sorted by timestamp, returning the latest N items.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of dicts with keys: type, timestamp, description, order_id, status (for changes)
        """
        activity = []

        # Get recent orders
        recent_orders = (
            self._visible_orders_query(Order)
            .order_by(Order.created_at.desc())
            .limit(limit * 2)
            .all()
        )  # Get extra to account for filtering

        for order in recent_orders:
            activity.append(
                {
                    "type": "order_created",
                    "timestamp": order.created_at,
                    "description": "Order created",
                    "order_id": order.id,
                    "order_number": order.inflow_order_id,
                }
            )

        # Get recent audit log entries (status changes)
        recent_changes = (
            self.db.query(AuditLog)
            .join(Order, AuditLog.order_id == Order.id)
            .filter(Order.hidden_from_ops.is_(False))
            .order_by(AuditLog.timestamp.desc())
            .limit(limit * 2)
            .all()
        )

        # Batch lookup order_id -> inflow_order_id to avoid N+1.
        order_ids = [
            str(log.order_id)
            for log in recent_changes
            if getattr(log, "order_id", None)
        ]
        unique_order_ids = sorted(set(order_ids))
        unique_order_ids_lower = sorted({oid.lower() for oid in unique_order_ids})

        order_number_by_id: Dict[str, Optional[str]] = {}
        if unique_order_ids:
            rows = (
                self._visible_orders_query(Order.id, Order.inflow_order_id)
                .filter(Order.id.in_(unique_order_ids))
                .all()
            )
            for oid, inflow_order_id in rows:
                if oid is None:
                    continue
                order_number_by_id[str(oid).lower()] = inflow_order_id

            # Some environments use case-sensitive collations for UUID strings.
            # If the direct lookup misses rows, retry with a lower() predicate.
            if len(order_number_by_id) < len(unique_order_ids_lower):
                rows_fallback = (
                    self._visible_orders_query(Order.id, Order.inflow_order_id)
                    .filter(func.lower(Order.id).in_(unique_order_ids_lower))
                    .all()
                )
                for oid, inflow_order_id in rows_fallback:
                    if oid is None:
                        continue
                    order_number_by_id.setdefault(str(oid).lower(), inflow_order_id)

        for log in recent_changes:
            order_id = str(log.order_id)
            activity.append(
                {
                    "type": "status_change",
                    "timestamp": log.timestamp,
                    "description": f"Status changed to {log.to_status}",
                    "order_id": order_id,
                    "order_number": order_number_by_id.get(order_id.lower()),
                    "from_status": log.from_status,
                    "to_status": log.to_status,
                    "changed_by": log.changed_by,
                    "reason": log.reason,
                }
            )

        # Sort by timestamp descending and return top N
        activity.sort(key=lambda x: x["timestamp"], reverse=True)
        return activity[:limit]

    def get_time_trends(
        self, period: str = "day", days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get time-series data for orders grouped by date.

        Args:
            period: Grouping period ("day", "week", "month") - currently only "day" supported
            days: Number of days to look back

        Returns:
            List of dicts with keys: date, count, status_breakdown
            Example: [
                {"date": "2025-01-29", "count": 5, "status_breakdown": {"picked": 2, "delivered": 3}},
                {"date": "2025-01-28", "count": 3, "status_breakdown": {"picked": 1, "delivered": 2}}
            ]
        """
        if period != "day":
            # Only day period supported for now
            period = "day"

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Query orders grouped by date
        results = (
            self._visible_orders_query(
                func.date(Order.signature_captured_at).label("date"),
                Order.status,
                func.count(Order.id).label("count"),
            )
            .filter(
                Order.status == OrderStatus.DELIVERED.value,
                Order.signature_captured_at.isnot(None),
                Order.signature_captured_at >= cutoff_date,
            )
            .group_by(func.date(Order.signature_captured_at), Order.status)
            .order_by(func.date(Order.signature_captured_at).desc())
            .all()
        )

        if not results:
            return []

        # Aggregate by date
        trends = {}
        for date, status, count in results:
            if not self._is_business_day(date):
                continue
            date_str = str(date)
            if date_str not in trends:
                trends[date_str] = {
                    "date": date_str,
                    "count": 0,
                    "status_breakdown": {},
                }
            trends[date_str]["count"] += count
            trends[date_str]["status_breakdown"][status] = count

        # Convert to list and sort by date descending
        return sorted(trends.values(), key=lambda x: x["date"], reverse=True)

    def get_workflow_daily_trends(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get daily transition totals for picked/shipped/delivered/fulfilled metrics."""
        days = max(1, min(days, 365))
        cutoff_date = datetime.utcnow() - timedelta(days=days - 1)

        results = (
            self.db.query(
                func.date(AuditLog.timestamp).label("date"),
                func.sum(
                    case(
                        (
                            func.lower(AuditLog.to_status)
                            == OrderStatus.SHIPPING.value,
                            1,
                        ),
                        else_=0,
                    )
                ).label("shipped_count"),
                func.sum(
                    case(
                        (
                            func.lower(AuditLog.to_status)
                            == OrderStatus.DELIVERED.value,
                            1,
                        ),
                        else_=0,
                    )
                ).label("delivered_count"),
                func.sum(
                    case(
                        (func.lower(AuditLog.to_status) == OrderStatus.PICKED.value, 1),
                        else_=0,
                    )
                ).label("picked_count"),
            )
            .join(Order, AuditLog.order_id == Order.id)
            .filter(AuditLog.timestamp >= cutoff_date)
            .filter(Order.hidden_from_ops.is_(False))
            .group_by(func.date(AuditLog.timestamp))
            .order_by(func.date(AuditLog.timestamp).asc())
            .all()
        )

        row_map: Dict[str, Dict[str, int]] = {}
        for date_value, shipped_count, delivered_count, picked_count in results:
            date_str = str(date_value)
            shipped = int(shipped_count or 0)
            delivered = int(delivered_count or 0)
            picked = int(picked_count or 0)
            row_map[date_str] = {
                "shipped_count": shipped,
                "delivered_count": delivered,
                "picked_count": picked,
                "fulfilled_count": shipped + delivered,
            }

        start_date = cutoff_date.date()
        today = datetime.utcnow().date()
        current_date = start_date
        data: List[Dict[str, Any]] = []
        while current_date <= today:
            if not self._is_business_day(current_date):
                current_date = current_date + timedelta(days=1)
                continue
            date_str = current_date.isoformat()
            metrics = row_map.get(
                date_str,
                {
                    "shipped_count": 0,
                    "delivered_count": 0,
                    "picked_count": 0,
                    "fulfilled_count": 0,
                },
            )
            data.append({"date": date_str, **metrics})
            current_date = current_date + timedelta(days=1)

        return data

    def get_fulfilled_totals_by_month(self, months: int = 12) -> List[Dict[str, Any]]:
        """Get fulfilled totals grouped by month using shipping+delivered transitions."""
        months = max(1, min(months, 60))
        cutoff_date = datetime.utcnow() - timedelta(days=months * 31)

        rows = (
            self.db.query(
                func.extract("year", AuditLog.timestamp).label("year"),
                func.extract("month", AuditLog.timestamp).label("month"),
                func.count(AuditLog.id).label("fulfilled_count"),
            )
            .join(Order, AuditLog.order_id == Order.id)
            .filter(
                AuditLog.timestamp >= cutoff_date,
                Order.hidden_from_ops.is_(False),
                func.lower(AuditLog.to_status).in_(
                    [OrderStatus.SHIPPING.value, OrderStatus.DELIVERED.value]
                ),
            )
            .group_by(
                func.extract("year", AuditLog.timestamp),
                func.extract("month", AuditLog.timestamp),
            )
            .order_by(
                func.extract("year", AuditLog.timestamp).asc(),
                func.extract("month", AuditLog.timestamp).asc(),
            )
            .all()
        )

        data = [
            {
                "period": f"{int(year):04d}-{int(month):02d}",
                "fulfilled_count": int(fulfilled_count or 0),
            }
            for year, month, fulfilled_count in rows
        ]

        return data[-months:]

    def get_fulfilled_totals_by_year(self, years: int = 5) -> List[Dict[str, Any]]:
        """Get fulfilled totals grouped by year using shipping+delivered transitions."""
        years = max(1, min(years, 20))
        cutoff_date = datetime.utcnow() - timedelta(days=years * 366)

        rows = (
            self.db.query(
                func.extract("year", AuditLog.timestamp).label("year"),
                func.count(AuditLog.id).label("fulfilled_count"),
            )
            .join(Order, AuditLog.order_id == Order.id)
            .filter(
                AuditLog.timestamp >= cutoff_date,
                Order.hidden_from_ops.is_(False),
                func.lower(AuditLog.to_status).in_(
                    [OrderStatus.SHIPPING.value, OrderStatus.DELIVERED.value]
                ),
            )
            .group_by(func.extract("year", AuditLog.timestamp))
            .order_by(func.extract("year", AuditLog.timestamp).asc())
            .all()
        )

        data = [
            {
                "period": str(int(year)),
                "fulfilled_count": int(fulfilled_count or 0),
            }
            for year, fulfilled_count in rows
        ]
        return data[-years:]
