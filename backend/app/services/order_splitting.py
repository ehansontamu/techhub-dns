"""
Order Splitting Service for handling partial pick remainder orders.

The active workflow splits a partial order into separate picked and remainder
legs when the picklist is generated, then keeps those local legs in sync with
later InFlow refreshes.
"""
import logging
import re
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List, Literal

from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.audit_log import AuditLog
from app.services.inflow_service import InflowService
from app.services.audit_service import AuditService
from app.utils.exceptions import ValidationError

logger = logging.getLogger(__name__)


class OrderSplittingService:
    """Service for splitting partial pick orders into picked and remainder legs."""

    def __init__(self, db: Session):
        self.db = db
        self.inflow_service = InflowService()

    def get_remainder_items(self, inflow_data: Dict[str, Any]) -> Tuple[list, list]:
        """
        Calculate remaining items that were not picked.

        Returns:
            Tuple of (remaining_lines, all_lines_with_remaining_quantity)
        """
        if not inflow_data:
            return [], []

        lines = inflow_data.get("lines", [])
        pick_lines = inflow_data.get("pickLines", [])

        # Build map of picked quantities by product ID
        picked = {}
        for line in pick_lines:
            pid = line.get("productId")
            qty = 0
            try:
                qty = float(line.get("quantity", {}).get("standardQuantity", 0) or 0)
            except (ValueError, TypeError):
                pass
            if pid:
                picked[pid] = picked.get(pid, 0) + qty

        # Calculate remaining for each line
        remaining_lines = []
        for line in lines:
            if not self.inflow_service._is_pick_required_line(line):
                continue
            pid = line.get("productId")
            ordered_qty = 0
            try:
                ordered_qty = float(line.get("quantity", {}).get("standardQuantity", 0) or 0)
            except (ValueError, TypeError):
                pass

            picked_qty = picked.get(pid, 0)
            remaining_qty = ordered_qty - picked_qty

            if remaining_qty > 0.0001:  # Use tolerance for float comparison
                # Create a copy of the line with remaining quantity
                remaining_line = deepcopy(line)
                quantity_data = dict(remaining_line.get("quantity") or {})
                quantity_data["standardQuantity"] = remaining_qty

                source_serials = [
                    str(serial_number)
                    for serial_number in (quantity_data.get("serialNumbers") or [])
                    if serial_number is not None
                ]
                picked_serials = {
                    str(serial_number)
                    for pick_line in pick_lines
                    if str(pick_line.get("productId") or "") == str(pid or "")
                    for serial_number in (
                        (pick_line.get("quantity") or {}).get("serialNumbers") or []
                    )
                    if serial_number is not None
                }
                if source_serials and picked_serials:
                    quantity_data["serialNumbers"] = [
                        serial_number
                        for serial_number in source_serials
                        if serial_number not in picked_serials
                    ]

                remaining_line["quantity"] = quantity_data
                remaining_lines.append(remaining_line)

        return remaining_lines, lines

    @staticmethod
    def _parse_standard_quantity(quantity_value: Any) -> float:
        if isinstance(quantity_value, dict):
            quantity_value = quantity_value.get("standardQuantity", 0)
        try:
            return float(quantity_value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _copy_line_with_quantity(
        line: Dict[str, Any], quantity: float, serial_numbers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        copied_line = deepcopy(line)
        quantity_data = dict(copied_line.get("quantity") or {})
        quantity_data["standardQuantity"] = str(quantity)
        if serial_numbers is not None:
            quantity_data["serialNumbers"] = list(serial_numbers)
        copied_line["quantity"] = quantity_data
        return copied_line

    def _build_partial_leg_view(self, inflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build a local snapshot for the picked leg of a partial order."""
        order_view = deepcopy(inflow_data or {})
        lines = order_view.get("lines", []) if isinstance(order_view, dict) else []
        pick_lines = order_view.get("pickLines", []) if isinstance(order_view, dict) else []

        if not isinstance(lines, list):
            lines = []
        if not isinstance(pick_lines, list):
            pick_lines = []

        picked_quantities: Dict[str, float] = {}
        picked_serials: Dict[str, List[str]] = {}

        for pick_line in pick_lines:
            if not isinstance(pick_line, dict):
                continue
            product_id = pick_line.get("productId")
            if not product_id:
                continue
            product_key = str(product_id)
            picked_quantities[product_key] = picked_quantities.get(product_key, 0.0) + self._parse_standard_quantity(
                pick_line.get("quantity")
            )
            serial_numbers = pick_line.get("quantity", {}).get("serialNumbers", []) or []
            if serial_numbers:
                picked_serials.setdefault(product_key, []).extend(
                    str(serial_number)
                    for serial_number in serial_numbers
                    if serial_number is not None
                )

        picked_lines: List[Dict[str, Any]] = []
        subtotal = 0.0

        for line in lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if not product_id:
                continue

            product_key = str(product_id)
            picked_qty = picked_quantities.get(product_key, 0.0)
            if picked_qty <= 0:
                continue

            line_serial_numbers = picked_serials.get(product_key)
            if line_serial_numbers:
                quantity = float(len(line_serial_numbers))
                picked_line = self._copy_line_with_quantity(
                    line, quantity, serial_numbers=line_serial_numbers
                )
            else:
                picked_line = self._copy_line_with_quantity(line, picked_qty)
                quantity = picked_qty

            raw_price = line.get("unitPrice")
            try:
                unit_price = float(raw_price or 0)
            except (TypeError, ValueError):
                unit_price = 0.0

            subtotal += unit_price * quantity
            picked_lines.append(picked_line)

        order_view["lines"] = picked_lines
        order_view["pickLines"] = deepcopy(picked_lines)
        order_view["packLines"] = []
        order_view["shipLines"] = []
        order_view["subtotal"] = subtotal
        order_view["total"] = subtotal
        return order_view

    def _build_remainder_leg_state(self, inflow_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build the persisted inflow snapshot for the remainder leg."""
        if not inflow_data:
            return None

        remaining_lines, _ = self.get_remainder_items(inflow_data)
        remainder_view = deepcopy(inflow_data)
        remainder_view["lines"] = remaining_lines
        remainder_view["pickLines"] = []
        remainder_view["packLines"] = []
        remainder_view["shipLines"] = []
        remainder_view["subtotal"] = sum(
            (float(line.get("unitPrice") or 0) if isinstance(line, dict) else 0.0)
            * self._parse_standard_quantity(line.get("quantity") if isinstance(line, dict) else 0)
            for line in remaining_lines
            if isinstance(line, dict)
        )
        remainder_view["total"] = remainder_view["subtotal"]
        return remainder_view

    def _get_partial_remainder_source(self, original_order: Order) -> Optional[Dict[str, Any]]:
        """Build the current inflow snapshot for the parent remainder leg."""
        if not original_order.inflow_data:
            return None
        if original_order.parent_order_id or not original_order.remainder_order_id:
            return None

        return deepcopy(original_order.inflow_data)

    def _get_recursive_child_pick_lines(self, original_order: Order) -> List[Dict[str, Any]]:
        """Return the pick lines already consumed by earlier recursive child legs."""
        if not original_order.id:
            return []

        child_orders = (
            self.db.query(Order)
            .filter(Order.parent_order_id == original_order.id)
            .order_by(Order.created_at.asc(), Order.updated_at.asc(), Order.id.asc())
            .all()
        )

        child_pick_lines: List[Dict[str, Any]] = []
        for child_order in child_orders:
            inflow_data = child_order.inflow_data if isinstance(child_order.inflow_data, dict) else {}
            pick_lines = inflow_data.get("pickLines") or inflow_data.get("lines") or []
            for line in pick_lines:
                if isinstance(line, dict):
                    child_pick_lines.append(deepcopy(line))
        return child_pick_lines

    def normalize_partial_remainder_snapshot(
        self,
        original_order: Order,
        inflow_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Remove quantities already assigned to earlier child legs from a remainder snapshot.

        InFlow may resend cumulative pick quantities for the original sales order. The
        recursive partial workflow needs only the delta that belongs to the current
        remainder leg, so we subtract the quantities already represented by prior child
        legs before persisting the refreshed remainder snapshot.
        """
        normalized = deepcopy(inflow_data or {})
        if not normalized:
            return normalized
        if original_order.parent_order_id or not original_order.remainder_order_id:
            return normalized

        child_pick_lines = self._get_recursive_child_pick_lines(original_order)
        if not child_pick_lines:
            return normalized

        current_pick_lines = [
            line
            for line in normalized.get("pickLines", [])
            if isinstance(line, dict)
        ]
        current_product_ids = {
            str(line.get("productId"))
            for line in current_pick_lines
            if line.get("productId")
        }
        child_product_ids = {
            str(line.get("productId"))
            for line in child_pick_lines
            if line.get("productId")
        }
        if not current_product_ids.intersection(child_product_ids):
            return normalized

        normalized_pick_lines = self._subtract_lines(
            current_pick_lines,
            child_pick_lines,
        )
        normalized["pickLines"] = normalized_pick_lines

        # Older split parents can drift back to full-order lines when InFlow
        # refreshes the cumulative sales order snapshot. If every picked line
        # belongs to existing child legs, rebuild the parent line set as the
        # unpicked remainder instead of leaving the full-order lines in place.
        current_lines = [
            line
            for line in normalized.get("lines", [])
            if isinstance(line, dict)
        ]
        if (
            len(child_pick_lines) > 1
            and not normalized_pick_lines
            and self._line_quantity_total(current_lines)
            > self._line_quantity_total(current_pick_lines) + 0.0001
        ):
            remainder_source = deepcopy(normalized)
            remainder_source["pickLines"] = current_pick_lines
            remainder_lines, _ = self.get_remainder_items(remainder_source)
            normalized["lines"] = remainder_lines
            subtotal = sum(
                (float(line.get("unitPrice") or 0) if isinstance(line, dict) else 0.0)
                * self._parse_standard_quantity(line.get("quantity") if isinstance(line, dict) else 0)
                for line in remainder_lines
                if isinstance(line, dict)
            )
            normalized["subtotal"] = subtotal
            normalized["total"] = subtotal

        return normalized

    def _line_quantity_total(self, lines: List[Dict[str, Any]]) -> float:
        return sum(
            self._parse_standard_quantity(line.get("quantity"))
            for line in lines
            if isinstance(line, dict)
        )

    def _subtract_lines(
        self,
        source_lines: List[Dict[str, Any]],
        subtract_lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Subtract one line set from another by product and quantity."""
        subtract_quantities: Dict[str, float] = {}
        subtract_serials: Dict[str, List[str]] = {}

        for line in subtract_lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if not product_id:
                continue
            product_key = str(product_id)
            subtract_quantities[product_key] = subtract_quantities.get(product_key, 0.0) + self._parse_standard_quantity(
                line.get("quantity")
            )
            serial_numbers = line.get("quantity", {}).get("serialNumbers", []) or []
            if serial_numbers:
                subtract_serials.setdefault(product_key, []).extend(
                    str(serial_number)
                    for serial_number in serial_numbers
                    if serial_number is not None
                )

        remaining_lines: List[Dict[str, Any]] = []
        remaining_subtract_quantities = dict(subtract_quantities)

        for line in source_lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if not product_id:
                continue

            product_key = str(product_id)
            copied_line = deepcopy(line)
            quantity_data = dict(copied_line.get("quantity") or {})
            line_quantity = self._parse_standard_quantity(quantity_data)
            serial_numbers = [
                str(serial_number)
                for serial_number in (quantity_data.get("serialNumbers", []) or [])
                if serial_number is not None
            ]

            if serial_numbers and subtract_serials.get(product_key):
                remaining_serials = list(serial_numbers)
                for serial_number in subtract_serials[product_key]:
                    if serial_number in remaining_serials:
                        remaining_serials.remove(serial_number)
                if not remaining_serials:
                    continue
                quantity_data["serialNumbers"] = remaining_serials
                quantity_data["standardQuantity"] = float(len(remaining_serials))
                copied_line["quantity"] = quantity_data
                remaining_lines.append(copied_line)
                continue

            subtract_quantity = remaining_subtract_quantities.get(product_key, 0.0)
            remaining_quantity = line_quantity - subtract_quantity
            if remaining_quantity <= 0.0001:
                remaining_subtract_quantities[product_key] = max(-remaining_quantity, 0.0)
                continue

            quantity_data["standardQuantity"] = remaining_quantity
            copied_line["quantity"] = quantity_data
            remaining_lines.append(copied_line)
            remaining_subtract_quantities[product_key] = 0.0

        return remaining_lines

    def _restrict_lines_to_source(
        self,
        candidate_lines: List[Dict[str, Any]],
        allowed_lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Limit candidate lines to the quantities/serials available on the allowed line set."""
        allowed_quantities: Dict[str, float] = {}
        allowed_serials: Dict[str, List[str]] = {}

        for line in allowed_lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if not product_id:
                continue
            product_key = str(product_id)
            allowed_quantities[product_key] = allowed_quantities.get(product_key, 0.0) + self._parse_standard_quantity(
                line.get("quantity")
            )
            serial_numbers = line.get("quantity", {}).get("serialNumbers", []) or []
            if serial_numbers:
                allowed_serials.setdefault(product_key, []).extend(
                    str(serial_number)
                    for serial_number in serial_numbers
                    if serial_number is not None
                )

        restricted_lines: List[Dict[str, Any]] = []

        for line in candidate_lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if not product_id:
                continue

            product_key = str(product_id)
            available_quantity = allowed_quantities.get(product_key, 0.0)
            if available_quantity <= 0.0001:
                continue

            copied_line = deepcopy(line)
            quantity_data = dict(copied_line.get("quantity") or {})
            serial_numbers = [
                str(serial_number)
                for serial_number in (quantity_data.get("serialNumbers", []) or [])
                if serial_number is not None
            ]

            if serial_numbers and allowed_serials.get(product_key):
                remaining_allowed_serials = allowed_serials[product_key]
                matched_serials: List[str] = []
                for serial_number in serial_numbers:
                    if serial_number in remaining_allowed_serials:
                        matched_serials.append(serial_number)
                        remaining_allowed_serials.remove(serial_number)
                if not matched_serials:
                    continue
                quantity_data["serialNumbers"] = matched_serials
                quantity_data["standardQuantity"] = float(len(matched_serials))
                copied_line["quantity"] = quantity_data
                restricted_lines.append(copied_line)
                allowed_quantities[product_key] = max(
                    available_quantity - float(len(matched_serials)),
                    0.0,
                )
                continue

            line_quantity = self._parse_standard_quantity(quantity_data)
            restricted_quantity = min(line_quantity, available_quantity)
            if restricted_quantity <= 0.0001:
                continue

            quantity_data["standardQuantity"] = restricted_quantity
            copied_line["quantity"] = quantity_data
            restricted_lines.append(copied_line)
            allowed_quantities[product_key] = max(
                available_quantity - restricted_quantity,
                0.0,
            )

        return restricted_lines

    def _next_partial_child_order_id(self, original_order: Order) -> str:
        """Return the next recursive picked-leg order number for a remainder row."""
        base_order_id = str(original_order.inflow_order_id or "").strip()
        if not base_order_id:
            raise ValidationError(
                "Partial child order generation requires a base inflow order number"
            )
        prefix = f"{base_order_id}-P"
        pattern = re.compile(rf"^{re.escape(prefix)}(?:(\d+))?$", re.IGNORECASE)

        highest_suffix = 0
        child_order_ids = (
            self.db.query(Order.inflow_order_id)
            .filter(Order.parent_order_id == original_order.id)
            .filter(Order.inflow_order_id.ilike(f"{prefix}%"))
            .all()
        )

        for (child_order_id,) in child_order_ids:
            if not child_order_id:
                continue
            match = pattern.match(str(child_order_id).strip())
            if not match:
                continue
            suffix_text = match.group(1)
            suffix = int(suffix_text) if suffix_text else 1
            highest_suffix = max(highest_suffix, suffix)

        next_suffix = highest_suffix + 1
        return prefix if next_suffix == 1 else f"{prefix}{next_suffix}"

    def _build_parent_remainder_assigned_view(
        self,
        original_order: Order,
        pick_lines_mode: Literal["stored", "lines"] = "stored",
    ) -> Optional[Dict[str, Any]]:
        """Build the remainder-leg item set for a parent order after a partial split."""
        remainder_source = self._get_partial_remainder_source(original_order)
        if not remainder_source:
            return None

        normalized_source = self.normalize_partial_remainder_snapshot(
            original_order,
            remainder_source,
        )
        assigned_view = (
            normalized_source
            if normalized_source.get("lines") != remainder_source.get("lines")
            else deepcopy(remainder_source)
        )
        source_lines = [
            line
            for line in assigned_view.get("lines", [])
            if isinstance(line, dict)
        ]
        if not source_lines:
            assigned_view["lines"] = []
            assigned_view["pickLines"] = []
            assigned_view["packLines"] = []
            assigned_view["shipLines"] = []
            assigned_view["subtotal"] = 0.0
            assigned_view["total"] = 0.0
            return assigned_view

        normalized_lines: List[Dict[str, Any]] = []
        subtotal = 0.0

        for line in source_lines:
            copied_line = deepcopy(line)
            quantity_data = dict(copied_line.get("quantity") or {})
            quantity_value = self._parse_standard_quantity(quantity_data)
            quantity_data["standardQuantity"] = quantity_value
            copied_line["quantity"] = quantity_data
            raw_price = copied_line.get("unitPrice")
            try:
                unit_price = float(raw_price or 0)
            except (TypeError, ValueError):
                unit_price = 0.0
            subtotal += unit_price * quantity_value
            normalized_lines.append(copied_line)

        assigned_view["lines"] = normalized_lines
        if pick_lines_mode == "lines":
            assigned_view["pickLines"] = deepcopy(normalized_lines)
        else:
            stored_pick_lines = [
                line
                for line in assigned_view.get("pickLines", [])
                if isinstance(line, dict)
            ]
            assigned_view["pickLines"] = self._restrict_lines_to_source(
                stored_pick_lines,
                normalized_lines,
            )
        assigned_view["packLines"] = []
        assigned_view["shipLines"] = []
        assigned_view["subtotal"] = subtotal
        assigned_view["total"] = subtotal
        return assigned_view

    def build_parent_remainder_document_view(self, original_order: Order) -> Optional[Dict[str, Any]]:
        """Build the remainder-only document snapshot for a parent partial leg."""
        return self._build_parent_remainder_assigned_view(original_order, pick_lines_mode="stored")

    def build_parent_remainder_pick_status_source(self, original_order: Order) -> Optional[Dict[str, Any]]:
        """Build the pick-status snapshot for a parent partial leg."""
        return self._build_parent_remainder_assigned_view(original_order, pick_lines_mode="stored")

    def build_parent_remainder_picklist_view(self, original_order: Order) -> Optional[Dict[str, Any]]:
        """Build the picklist payload for a parent remainder leg."""
        return self._build_parent_remainder_assigned_view(original_order, pick_lines_mode="lines")

    def create_partial_picklist_leg(
        self, original_order: Order, user_id: Optional[str] = None
    ) -> Optional[Order]:
        """Create a local child order containing the picked leg for a partial order."""
        if not original_order.inflow_data:
            return None

        if original_order.parent_order_id:
            return original_order

        pick_status = self.inflow_service.get_pick_status(
            original_order.inflow_data, include_services=False
        )
        if pick_status.get("is_fully_picked", True):
            return None

        leg_order_id = self._next_partial_child_order_id(original_order)

        picked_leg_inflow_data = self._build_partial_leg_view(original_order.inflow_data)
        remainder_leg_state = self._build_remainder_leg_state(original_order.inflow_data)

        child_order = Order(
            id=str(uuid.uuid4()),
            inflow_order_id=leg_order_id,
            inflow_sales_order_id=original_order.inflow_sales_order_id,
            recipient_name=original_order.recipient_name,
            recipient_contact=original_order.recipient_contact,
            delivery_location=original_order.delivery_location,
            po_number=original_order.po_number,
            status=OrderStatus.PICKED.value,
            tagged_at=original_order.tagged_at,
            tagged_by=original_order.tagged_by,
            tag_data=deepcopy(original_order.tag_data) if original_order.tag_data else None,
            inflow_data=picked_leg_inflow_data,
            parent_order_id=str(original_order.id),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(child_order)
        self.db.flush()

        child_audit = AuditLog(
            order_id=child_order.id,
            changed_by=user_id or "system",
            from_status=None,
            to_status=OrderStatus.PICKED.value,
            reason=f"Partial picklist child created from {original_order.inflow_order_id}",
            timestamp=datetime.utcnow(),
        )
        self.db.add(child_audit)

        original_order.has_remainder = "Y"
        original_order.remainder_order_id = child_order.id
        if remainder_leg_state is not None:
            original_order.inflow_data = remainder_leg_state
        original_order.updated_at = datetime.utcnow()

        audit_service = AuditService(self.db)
        audit_service.log_order_action(
            order_id=str(original_order.id),
            action="partial_picklist_child_created",
            user_id=user_id,
            description=f"Created partial picklist child {leg_order_id}",
            audit_metadata={
                "child_order_id": str(child_order.id),
                "child_inflow_order_id": leg_order_id,
                "picked_line_count": len(picked_leg_inflow_data.get("pickLines", [])),
            },
        )
        audit_service.log_order_action(
            order_id=str(child_order.id),
            action="created_as_partial_picklist_leg",
            user_id=user_id,
            description=f"Created as partial picklist leg for order {original_order.inflow_order_id}",
            audit_metadata={
                "parent_order_id": str(original_order.id),
                "parent_inflow_order_id": original_order.inflow_order_id,
                "picked_line_count": len(picked_leg_inflow_data.get("pickLines", [])),
            },
        )

        self.db.commit()
        self.db.refresh(child_order)

        logger.info(
            "Created partial picklist child %s for parent %s",
            child_order.inflow_order_id,
            original_order.inflow_order_id,
        )
        return child_order
