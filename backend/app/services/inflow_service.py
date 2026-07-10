import httpx
from typing import Optional, List, Dict, Any
import logging
import uuid
from copy import deepcopy
from datetime import datetime
import time
from sqlalchemy.orm import Session
from app.config import settings
from app.utils.pdf_helpers import filter_picklines

logger = logging.getLogger(__name__)


class InflowService:
    _CATEGORY_MAP_TTL_SECONDS = 300
    _CATEGORY_MAP_EMPTY_TTL_SECONDS = 30
    _category_map_cache: Optional[Dict[str, str]] = None
    _category_map_cache_expires_at = 0.0
    _KNOWN_NON_PICKABLE_PRODUCT_NAMES = {
        "computer imaging",
    }

    def __init__(self):
        self.base_url = settings.inflow_api_url
        self.company_id = settings.inflow_company_id
        self._api_key: Optional[str] = None
        self._headers: Optional[Dict[str, str]] = None

    @property
    def api_key(self) -> str:
        """Lazy API key retrieval - prevents crash on startup if Service Principal not ready."""
        if self._api_key is None:
            self._api_key = self._get_api_key()
        return self._api_key

    @property
    def headers(self) -> Dict[str, str]:
        """Lazy headers - depends on api_key property."""
        if self._headers is None:
            self._headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json;version=2024-03-12",
            }
        return self._headers

    @staticmethod
    def _summarize_line_quantities(lines: Any) -> List[Dict[str, Any]]:
        if not isinstance(lines, list):
            return []

        summary: List[Dict[str, Any]] = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            summary.append(
                {
                    "productId": line.get("productId"),
                    "description": line.get("description"),
                    "standardQuantity": ((line.get("quantity") or {}).get("standardQuantity")),
                    "uomQuantity": ((line.get("quantity") or {}).get("uomQuantity")),
                    "containerNumber": line.get("containerNumber"),
                }
            )
        return summary

    def _build_fulfillment_http_error(
        self,
        *,
        action: str,
        order_number: str,
        response: httpx.Response,
        payload: Optional[Dict[str, Any]] = None,
    ) -> ValueError:
        response_text = (response.text or "").strip()
        message = (
            f"{action} failed for order {order_number}: "
            f"{response.status_code} {response.reason_phrase}"
        )
        if response_text:
            message = f"{message} - {response_text[:1000]}"

        if isinstance(payload, dict):
            logger.error(
                "%s payload summary for %s: packLines=%s shipLines=%s",
                action,
                order_number,
                self._summarize_line_quantities(payload.get("packLines")),
                self._summarize_line_quantities(payload.get("shipLines")),
            )

        return ValueError(message)

    def _is_fully_picked(self, order: Dict[str, Any]) -> bool:
        """
        Check if an order is fully picked by comparing ordered lines vs pick lines.
        """
        lines = order.get("lines", [])
        pick_lines = order.get("pickLines", [])

        # Build map of required quantities by product ID
        required = {}
        picked = {}
        for line in lines:
            pid = line.get("productId")
            qty = 0
            try:
                qty = float(line.get("quantity", {}).get("standardQuantity", 0) or 0)
            except (ValueError, TypeError):
                pass
            if pid and qty > 0:
                required[pid] = required.get(pid, 0) + qty
                if self._counts_as_picked_service_line(line):
                    picked[pid] = picked.get(pid, 0) + qty

        # Build map of picked quantities
        for line in pick_lines:
            pid = line.get("productId")
            qty = 0
            try:
                qty = float(line.get("quantity", {}).get("standardQuantity", 0) or 0)
            except (ValueError, TypeError):
                pass
            if pid and qty > 0:
                picked[pid] = picked.get(pid, 0) + qty

        # Compare
        for pid, req_qty in required.items():
            picked_qty = picked.get(pid, 0)
            # floating point comparison tolerance
            if picked_qty < (req_qty - 0.0001):
                return False

        return True

    @staticmethod
    def _parse_standard_quantity(value: Any) -> float:
        if isinstance(value, dict):
            value = value.get("standardQuantity")
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _parse_optional_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize_pack_quantity(
        cls,
        line: Dict[str, Any],
        quantity: Optional[float] = None,
    ) -> Dict[str, Any]:
        quantity_data = dict((line.get("quantity") or {}))
        normalized_quantity = (
            quantity if quantity is not None else cls._parse_standard_quantity(quantity_data)
        )
        original_standard_quantity = cls._parse_standard_quantity(quantity_data)
        original_uom_quantity = cls._parse_optional_float(quantity_data.get("uomQuantity"))
        existing_uom = str(quantity_data.get("uom") or "").strip()

        product = line.get("product")
        product_dict = product if isinstance(product, dict) else {}
        sales_uom = product_dict.get("salesUom")
        sales_uom_dict = sales_uom if isinstance(sales_uom, dict) else {}
        resolved_uom = (
            str(
                sales_uom_dict.get("name")
                or sales_uom_dict.get("uom")
                or product_dict.get("standardUomName")
                or existing_uom
                or ""
            ).strip()
        )

        quantity_data["standardQuantity"] = str(
            int(normalized_quantity) if float(normalized_quantity).is_integer() else normalized_quantity
        )

        if (
            original_uom_quantity is not None
            and original_standard_quantity > 0
            and "uomQuantity" in quantity_data
        ):
            raw_uom_quantity = quantity_data.get("uomQuantity")
            decimal_places = (
                len(raw_uom_quantity.split(".", 1)[1])
                if isinstance(raw_uom_quantity, str) and "." in raw_uom_quantity
                else None
            )
            scaled_uom_quantity = original_uom_quantity * (
                normalized_quantity / original_standard_quantity
            )

            if resolved_uom:
                quantity_data["uom"] = resolved_uom
                if decimal_places is not None:
                    quantity_data["uomQuantity"] = f"{scaled_uom_quantity:.{decimal_places}f}"
                else:
                    quantity_data["uomQuantity"] = scaled_uom_quantity
            else:
                if decimal_places is not None:
                    quantity_data["uomQuantity"] = f"{normalized_quantity:.{decimal_places}f}"
                else:
                    quantity_data["uomQuantity"] = str(normalized_quantity)

        return quantity_data

    @staticmethod
    def _normalized_text(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().replace("-", " ").split())

    @classmethod
    def _is_service_line(cls, line: Dict[str, Any]) -> bool:
        product = line.get("product")
        product_dict = product if isinstance(product, dict) else {}

        type_candidates = [
            line.get("type"),
            line.get("lineType"),
            line.get("itemType"),
            line.get("productType"),
            product_dict.get("type"),
            product_dict.get("itemType"),
            product_dict.get("productType"),
        ]
        for candidate in type_candidates:
            normalized = cls._normalized_text(candidate)
            if normalized in {"service", "services", "non inventory", "non stock"}:
                return True

        for bool_key in ("isService", "service", "isNonInventory", "isNonStock"):
            if line.get(bool_key) is True or product_dict.get(bool_key) is True:
                return True

        category = product_dict.get("category")
        category_dict = category if isinstance(category, dict) else {}
        category_name = cls._normalized_text(category_dict.get("name"))
        if category_name in {"service", "services"}:
            return True

        names = [
            line.get("description"),
            line.get("productName"),
            product_dict.get("name"),
        ]
        return any(
            cls._normalized_text(name) in cls._KNOWN_NON_PICKABLE_PRODUCT_NAMES
            for name in names
        )

    @classmethod
    def _is_pick_required_line(cls, line: Dict[str, Any]) -> bool:
        if not isinstance(line, dict):
            return False
        if not line.get("productId"):
            return False
        if cls._parse_standard_quantity(line.get("quantity")) <= 0:
            return False
        return not cls._is_service_line(line)

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if value is True:
            return True
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}
        return False

    @classmethod
    def _is_service_completed_line(cls, line: Dict[str, Any]) -> bool:
        return cls._is_service_line(line) and cls._is_truthy(
            line.get("serviceCompleted")
        )

    @classmethod
    def _is_known_non_pickable_service_line(cls, line: Dict[str, Any]) -> bool:
        if not isinstance(line, dict):
            return False
        product = line.get("product")
        product_dict = product if isinstance(product, dict) else {}
        names = [
            line.get("description"),
            line.get("productName"),
            product_dict.get("name"),
        ]
        return any(
            cls._normalized_text(name) in cls._KNOWN_NON_PICKABLE_PRODUCT_NAMES
            for name in names
        )

    @classmethod
    def _counts_as_picked_service_line(cls, line: Dict[str, Any]) -> bool:
        # Known non-pickable service rows should never force a partial workflow.
        return cls._is_service_line(line) and (
            cls._is_service_completed_line(line)
            or cls._is_known_non_pickable_service_line(line)
        )

    @classmethod
    def _line_name(cls, line: Dict[str, Any]) -> str:
        product = line.get("product")
        product_dict = product if isinstance(product, dict) else {}
        return str(
            line.get("description")
            or line.get("productName")
            or product_dict.get("name")
            or line.get("productId")
            or ""
        )

    @staticmethod
    def _line_key(line: Dict[str, Any]) -> str:
        product_id = line.get("productId")
        if product_id is not None:
            return f"product:{product_id}"
        return f"description:{line.get('description') or line.get('productName') or ''}"

    def build_picklist_view(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Return a picklist payload that includes service lines as picked items."""
        order_view = dict(order or {})
        lines = order.get("lines", []) if isinstance(order, dict) else []
        pick_lines = order.get("pickLines", []) if isinstance(order, dict) else []

        if not isinstance(lines, list):
            lines = []
        if not isinstance(pick_lines, list):
            pick_lines = []

        displayed_pick_lines = filter_picklines(order_view, pick_lines)
        displayed_keys = {
            self._line_key(line)
            for line in displayed_pick_lines
            if isinstance(line, dict)
        }

        for line in lines:
            if not isinstance(line, dict) or not self._is_service_line(line):
                continue
            if not self._counts_as_picked_service_line(line):
                continue
            if self._parse_standard_quantity(line.get("quantity")) <= 0:
                continue
            line_key = self._line_key(line)
            if line_key in displayed_keys:
                continue
            service_line = dict(line)
            product = service_line.get("product")
            product_dict = dict(product) if isinstance(product, dict) else {}
            if not product_dict:
                product_dict = {"name": self._line_name(line), "sku": "SERVICE"}
            elif not product_dict.get("sku"):
                product_dict["sku"] = product_dict.get("sku") or "SERVICE"
            service_line["product"] = product_dict
            displayed_pick_lines.append(service_line)
            displayed_keys.add(line_key)

        order_view["pickLines"] = displayed_pick_lines
        return order_view

    @staticmethod
    def _copy_line_with_quantity(
        line: Dict[str, Any], quantity: float
    ) -> Dict[str, Any]:
        copied_line = dict(line)
        quantity_data = dict(line.get("quantity") or {})
        quantity_data["standardQuantity"] = str(
            int(quantity) if quantity.is_integer() else quantity
        )
        copied_line["quantity"] = quantity_data
        return copied_line

    def build_remaining_order_view(self, order: Dict[str, Any]) -> Dict[str, Any]:
        order_view = dict(order or {})
        lines = order.get("lines", []) if isinstance(order, dict) else []
        pack_lines = order.get("packLines", []) if isinstance(order, dict) else []
        pick_lines = order.get("pickLines", []) if isinstance(order, dict) else []

        if not isinstance(lines, list):
            lines = []
        if not isinstance(pack_lines, list):
            pack_lines = []
        if not isinstance(pick_lines, list):
            pick_lines = []

        shipped_quantities: Dict[str, float] = {}
        shipped_serials: Dict[str, set[str]] = {}
        for pack_line in pack_lines:
            if not isinstance(pack_line, dict):
                continue
            product_id = pack_line.get("productId")
            if not product_id:
                continue
            product_key = str(product_id)
            shipped_quantities[product_key] = shipped_quantities.get(
                product_key, 0.0
            ) + self._parse_standard_quantity(pack_line.get("quantity"))
            serial_numbers = (
                pack_line.get("quantity", {}).get("serialNumbers", []) or []
            )
            shipped_serials.setdefault(product_key, set()).update(
                str(serial) for serial in serial_numbers if serial is not None
            )

        remaining_lines: List[Dict[str, Any]] = []
        remaining_subtotal = 0.0

        for line in lines:
            if not isinstance(line, dict):
                continue

            product_id = line.get("productId")
            if not product_id:
                continue

            product_key = str(product_id)
            original_qty = self._parse_standard_quantity(line.get("quantity"))
            if original_qty <= 0:
                continue
            if self._is_service_line(line):
                if self._counts_as_picked_service_line(line):
                    continue
                remaining_line = self._copy_line_with_quantity(line, original_qty)
                remaining_lines.append(remaining_line)
                raw_price = line.get("unitPrice")
                try:
                    unit_price = float(raw_price or 0)
                except (TypeError, ValueError):
                    unit_price = 0.0
                remaining_subtotal += unit_price * original_qty
                continue
            if not self._is_pick_required_line(line):
                continue

            serial_numbers = [
                str(serial)
                for serial in (line.get("quantity", {}).get("serialNumbers", []) or [])
                if serial is not None
            ]

            if serial_numbers:
                remaining_serials = [
                    serial
                    for serial in serial_numbers
                    if serial not in shipped_serials.get(product_key, set())
                ]
                if not remaining_serials:
                    continue
                remaining_line = self._copy_line_with_quantity(
                    line, float(len(remaining_serials))
                )
                remaining_line["quantity"] = dict(remaining_line.get("quantity") or {})
                remaining_line["quantity"]["serialNumbers"] = remaining_serials
                remaining_qty = float(len(remaining_serials))
            else:
                shipped_qty = shipped_quantities.get(product_key, 0.0)
                remaining_qty = max(original_qty - shipped_qty, 0.0)
                if remaining_qty <= 0:
                    continue
                remaining_line = self._copy_line_with_quantity(line, remaining_qty)
                shipped_quantities[product_key] = max(shipped_qty - original_qty, 0.0)

            raw_price = line.get("unitPrice")
            try:
                unit_price = float(raw_price or 0)
            except (TypeError, ValueError):
                unit_price = 0.0
            remaining_subtotal += unit_price * remaining_qty
            remaining_lines.append(remaining_line)

        order_view["lines"] = remaining_lines
        order_view["pickLines"] = filter_picklines(order_view, pick_lines)
        order_view["subtotal"] = remaining_subtotal
        order_view["total"] = remaining_subtotal
        return order_view

    def get_pick_status(
        self, order: Dict[str, Any], include_services: bool = True
    ) -> Dict[str, Any]:
        """
        Get detailed pick status for an order.

        Returns:
            {
                "is_fully_picked": bool,
                "total_ordered": int,
                "total_picked": int,
                "missing_items": [{"product_id": str, "product_name": str, "ordered": int, "picked": int}]
            }
        """
        lines = order.get("lines", [])
        pick_lines = order.get("pickLines", [])

        if not isinstance(lines, list):
            lines = []
        if not isinstance(pick_lines, list):
            pick_lines = []

        # Build map of required quantities and product names by product ID
        required = {}
        product_names = {}
        picked = {}
        for line in lines:
            if not isinstance(line, dict):
                continue
            if self._is_service_line(line) and not include_services:
                continue
            pid = line.get("productId")
            if pid is not None:
                pid = str(pid)
            qty = 0
            try:
                quantity = line.get("quantity")
                quantity_dict = quantity if isinstance(quantity, dict) else {}
                qty = float(quantity_dict.get("standardQuantity", 0) or 0)
            except (ValueError, TypeError):
                pass
            if pid and qty > 0:
                required[pid] = required.get(pid, 0) + qty
                if self._counts_as_picked_service_line(line):
                    picked[pid] = picked.get(pid, 0) + qty
                # Try to get product name from line description or product data
                if pid not in product_names:
                    product_names[pid] = self._line_name(line) or pid

        # Build map of picked quantities
        for line in pick_lines:
            if not isinstance(line, dict):
                continue
            pid = line.get("productId")
            if pid is not None:
                pid = str(pid)
            qty = 0
            try:
                quantity = line.get("quantity")
                quantity_dict = quantity if isinstance(quantity, dict) else {}
                qty = float(quantity_dict.get("standardQuantity", 0) or 0)
            except (ValueError, TypeError):
                pass
            if pid and qty > 0:
                picked[pid] = picked.get(pid, 0) + qty
                # Also capture product name from pick lines if available
                if pid not in product_names:
                    product = line.get("product")
                    product_dict = product if isinstance(product, dict) else {}
                    product_names[pid] = str(
                        line.get("description") or product_dict.get("name") or pid
                    )

        # Calculate totals and missing items
        total_ordered = sum(required.values())
        total_picked = sum(
            min(picked.get(pid, 0), req_qty) for pid, req_qty in required.items()
        )

        missing_items = []
        for pid, req_qty in required.items():
            picked_qty = picked.get(pid, 0)
            if picked_qty < (req_qty - 0.0001):
                missing_items.append(
                    {
                        "product_id": pid,
                        "product_name": str(product_names.get(pid, pid)),
                        "ordered": int(req_qty),
                        "picked": int(picked_qty),
                    }
                )

        return {
            "is_fully_picked": len(missing_items) == 0,
            "total_ordered": int(total_ordered),
            "total_picked": int(total_picked),
            "missing_items": missing_items,
        }

    def _get_api_key(self) -> str:
        """Get API key from environment variable or Azure Key Vault using Service Principal."""
        # Priority 1: Direct environment variable
        if settings.inflow_api_key:
            return settings.inflow_api_key

        # Priority 2: Azure Key Vault with Service Principal
        if settings.azure_key_vault_url:
            if not all(
                [
                    settings.azure_tenant_id,
                    settings.azure_client_id,
                    settings.azure_client_secret,
                ]
            ):
                raise ValueError(
                    "Azure Key Vault configured but Service Principal credentials missing. "
                    "Set AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET."
                )

            try:
                from azure.identity import ClientSecretCredential
                from azure.keyvault.secrets import SecretClient

                credential = ClientSecretCredential(
                    tenant_id=settings.azure_tenant_id,
                    client_id=settings.azure_client_id,
                    client_secret=settings.azure_client_secret,
                )
                kv_client = SecretClient(
                    vault_url=settings.azure_key_vault_url, credential=credential
                )
                secret = kv_client.get_secret("inflow-API-key-new")
                logger.info("Retrieved Inflow API key from Azure Key Vault")
                return secret.value
            except Exception as e:
                raise ValueError(f"Failed to get API key from Key Vault: {e}")

        raise ValueError("INFLOW_API_KEY or AZURE_KEY_VAULT_URL must be set")

    async def fetch_orders(
        self,
        inventory_status: Optional[str] = None,
        is_active: bool = True,
        order_number: Optional[str] = None,
        count: int = 100,
        skip: int = 0,
        sort: str = "orderDate",
        sort_desc: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch orders from Inflow API"""
        url = f"{self.base_url}/{self.company_id}/sales-orders"

        params = {
            "include": "pickLines.product,shipLines,packLines.product,lines",
            "filter[isActive]": str(is_active).lower(),
            "count": str(count),
            "skip": str(skip),
            "sort": sort,
            "sortDesc": str(sort_desc).lower(),
        }

        if inventory_status:
            params["filter[inventoryStatus][]"] = inventory_status

        if order_number:
            params["filter[orderNumber]"] = order_number

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            # Handle both dict with 'items' key and list response
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            elif isinstance(data, list):
                return data
            else:
                return []

    async def get_order_by_number(self, order_number: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific order by order number"""
        orders = await self.fetch_orders(order_number=order_number, count=1)
        if orders:
            return orders[0]
        return None

    async def sync_recent_started_orders(
        self, max_pages: int = 3, per_page: int = 100, target_matches: int = 100
    ) -> List[Dict[str, Any]]:
        """Sync recent started orders that already have pickLines."""
        matches = []

        for page in range(max_pages):
            orders = await self.fetch_orders(
                inventory_status="started",
                count=per_page,
                skip=page * per_page,
            )

            # Filter for orders that already have pickLines and are ready to ingest.
            for order in orders:
                if self.is_started_and_picked(order):
                    matches.append(order)
                    if len(matches) >= target_matches:
                        return matches

            if len(orders) < per_page:
                break  # No more pages

        return matches

    def is_strict_started(self, order: Dict[str, Any]) -> bool:
        """Check if order has inventoryStatus='started' (case-insensitive)"""
        return str(order.get("inventoryStatus", "")).strip().lower() == "started"

    def is_started_and_picked(self, order: Dict[str, Any]) -> bool:
        """Check if order is still started and has pickLines to ingest into TechHub."""
        return self.is_strict_started(order) and bool(order.get("pickLines"))

    async def get_order_by_id(self, sales_order_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific order by sales order ID (UUID)."""
        url = f"{self.base_url}/{self.company_id}/sales-orders/{sales_order_id}"
        params = {
            "include": "pickLines.product,shipLines,packLines.product,lines.product,lines"
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                logger.error(
                    f"Failed to fetch order {sales_order_id}: {e.response.status_code} - {e.response.text}"
                )
                raise

            data = response.json()

            if isinstance(data, dict) and "items" in data:
                return data["items"][0] if data["items"] else None
            if isinstance(data, list):
                return data[0] if data else None
            return data

    async def fulfill_sales_order(
        self,
        sales_order_id: str,
        db: Session = None,
        user_id: str = None,
        only_picked_items: bool = False,
        source_order_data: Optional[Dict[str, Any]] = None,
        source_order_identifier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fulfill a sales order by ensuring pickLines, packLines, and shipLines are populated.
        Based on inFlow docs: inventoryStatus becomes fulfilled when all products are in pickLines
        and, for shippable orders, packLines/shipLines are present.

        Args:
            sales_order_id: The Inflow sales order ID
            db: Database session for audit logging
            user_id: User ID for audit logging
            only_picked_items: If True, only fulfill items in pickLines (for partial orders from delivery runs).
                              When True, packLines are created from pickLines instead of original order lines,
                              and the "fully picked" validation is skipped.
            source_order_data: Optional local order snapshot to use as the source for partial-leg fulfillment.
            source_order_identifier: Optional local order number/identifier for split-leg detection.
        """
        from app.services.audit_service import AuditService

        order = await self.get_order_by_id(sales_order_id)
        if not order:
            raise ValueError(f"Sales order {sales_order_id} not found in Inflow")

        # Require actual pickLines - don't create them artificially unless a split
        # delivery leg provides its own local source snapshot.
        is_split_delivery_leg = bool(
            only_picked_items
            and source_order_identifier
            and source_order_identifier != order.get("orderNumber")
        )
        if not order.get("pickLines") and not is_split_delivery_leg:
            order_number = order.get("orderNumber") or sales_order_id
            raise ValueError(
                f"Order {order_number} has no pickLines - items were not picked from inventory"
            )

        if not order.get("customerId"):
            raise ValueError("Sales order missing customerId; cannot fulfill")

        now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        order_number = order.get("orderNumber") or sales_order_id

        def positive_quantity(line: Dict[str, Any]) -> bool:
            qty = line.get("quantity", {})
            raw = qty.get("standardQuantity")
            if raw is None:
                return False
            try:
                return float(raw) > 0
            except (TypeError, ValueError):
                return False

        # pickLines validation is now done above - they must exist

        pack_lines = order.get("packLines", [])
        if not isinstance(pack_lines, list):
            pack_lines = []

        ship_lines = order.get("shipLines", [])
        if not isinstance(ship_lines, list):
            ship_lines = []

        if only_picked_items:
            source_snapshot = (
                source_order_data if isinstance(source_order_data, dict) else None
            )
            if (
                is_split_delivery_leg
                and source_snapshot
                and (source_snapshot.get("packLines") or source_snapshot.get("shipLines"))
            ):
                return order

            if is_split_delivery_leg and source_snapshot:
                source_pick_lines = source_snapshot.get("pickLines")
                if not isinstance(source_pick_lines, list) or not source_pick_lines:
                    source_pick_lines = source_snapshot.get("lines", [])
                source_lines = [
                    deepcopy(line)
                    for line in source_pick_lines
                    if isinstance(line, dict)
                ]
                if not source_lines:
                    logger.warning(
                        "Split delivery leg %s has no local pick/line snapshot; falling back to live InFlow pickLines",
                        source_order_identifier or sales_order_id,
                    )
                    source_lines = filter_picklines(order, order.get("pickLines", []))
            else:
                source_lines = filter_picklines(order, order.get("pickLines", []))

            new_pack_lines = []
            existing_suffixes: List[int] = []
            for existing_pack_line in pack_lines:
                container_name = existing_pack_line.get("containerNumber")
                if not isinstance(container_name, str):
                    continue
                prefix = f"DELIVERY-{order_number}-"
                if not container_name.startswith(prefix):
                    continue
                suffix = container_name[len(prefix) :]
                if suffix.isdigit():
                    existing_suffixes.append(int(suffix))
            next_suffix = (
                max(existing_suffixes) if existing_suffixes else len(ship_lines)
            ) + 1
            container_number = f"DELIVERY-{order_number}-{next_suffix}"

            for line in source_lines:
                if not positive_quantity(line) or not self._is_pick_required_line(line):
                    continue
                new_pack_lines.append(
                    {
                        "salesOrderPackLineId": str(uuid.uuid4()),
                        "productId": line.get("productId"),
                        "quantity": self._normalize_pack_quantity(line),
                        "description": line.get("description"),
                        "containerNumber": container_number,
                    }
                )

            if not new_pack_lines:
                if is_split_delivery_leg and source_snapshot:
                    raise ValueError(
                        f"Order {order_number} has no usable split-leg items to fulfill in InFlow"
                    )
                raise ValueError(
                    f"Order {order_number} has no newly picked items to fulfill in InFlow"
                )

            new_ship_lines = [
                {
                    "salesOrderShipLineId": str(uuid.uuid4()),
                    "carrier": "TechHub",
                    "containers": [container_number],
                    "shippedDate": now,
                }
            ]
            order["packLines"] = pack_lines + new_pack_lines
            order["shipLines"] = ship_lines + new_ship_lines
        else:
            if not pack_lines:
                container_number = f"DELIVERY-{order_number}"
                new_pack_lines = []
                for line in order.get("lines", []):
                    if not positive_quantity(line) or not self._is_pick_required_line(line):
                        continue
                    new_pack_lines.append(
                        {
                            "salesOrderPackLineId": str(uuid.uuid4()),
                            "productId": line.get("productId"),
                            "quantity": self._normalize_pack_quantity(line),
                            "description": line.get("description"),
                            "containerNumber": container_number,
                        }
                    )
                order["packLines"] = new_pack_lines
                pack_lines = new_pack_lines

            if not ship_lines and pack_lines:
                order["shipLines"] = [
                    {
                        "salesOrderShipLineId": str(uuid.uuid4()),
                        "carrier": "TechHub",
                        "containers": list(
                            {
                                line.get("containerNumber")
                                for line in pack_lines
                                if line.get("containerNumber")
                            }
                        ),
                        "shippedDate": now,
                    }
                ]

            # Check if order is fully picked (skip this check if only_picked_items=True)
            if not only_picked_items:
                is_fully_picked = self._is_fully_picked(order)
                if not is_fully_picked:
                    msg = f"Order {order_number} is only partially picked. Skipping InFlow fulfillment to avoid inventory issues."
                    logger.warning(msg)

                    if db:
                        # Log the skip
                        audit_service = AuditService(db)
                        audit_service.log_action(
                            entity_type="inflow_order",
                            entity_id=sales_order_id,
                            action="fulfillment_skipped",
                            user_id=user_id,
                            description=msg,
                            audit_metadata={
                                "reason": "partial_pick",
                                "inflow_order_number": order.get("orderNumber"),
                            },
                        )

                    # Return success structure but indicate skipped
                    return {
                        "salesOrderId": sales_order_id,
                        "orderNumber": order_number,
                        "status": "skipped",
                        "message": msg,
                    }

        # Proceed with fulfillment (either fully picked, or only_picked_items=True)
        url = f"{self.base_url}/{self.company_id}/sales-orders"
        async with httpx.AsyncClient() as client:
            response = await client.put(url, json=order, headers=self.headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                raise self._build_fulfillment_http_error(
                    action="InFlow fulfillment",
                    order_number=order_number,
                    response=response,
                    payload=order,
                )
            result = response.json()

        if isinstance(result, dict) and "items" in result:
            items = result.get("items")
            result = items[0] if isinstance(items, list) and items else {}
        elif isinstance(result, list):
            result = result[0] if result else {}

        if not isinstance(result, dict) or not result or "lines" not in result:
            result = order
        elif only_picked_items:
            result = dict(result)

        if only_picked_items:
            result["_techhub_partial_leg_pack_lines"] = deepcopy(new_pack_lines)
            result["_techhub_partial_leg_ship_lines"] = deepcopy(new_ship_lines)

        # Audit logging for inFlow fulfillment
        if db:
            audit_service = AuditService(db)
            description = "Order fulfilled in inFlow system"
            if only_picked_items:
                description = "Order fulfilled in inFlow system (only picked items, partial fulfillment)"

            audit_service.log_action(
                entity_type="inflow_order",
                entity_id=sales_order_id,
                action="fulfilled",
                user_id=user_id,
                description=description,
                audit_metadata={
                    "inflow_order_number": order.get("orderNumber"),
                    "pick_lines_count": len(order.get("pickLines", [])),
                    "pack_lines_count": len(order.get("packLines", [])),
                    "ship_lines_count": len(order.get("shipLines", [])),
                    "only_picked_items": only_picked_items,
                },
            )

        return result

    async def register_webhook(
        self, webhook_url: str, events: List[str]
    ) -> Dict[str, Any]:
        """
        Register a webhook with Inflow API.

        Args:
            webhook_url: Public URL for webhook endpoint
            events: List of events to subscribe to (e.g., ["orderCreated", "orderUpdated"])

        Returns:
            Webhook registration response from Inflow
        """
        import uuid

        url = f"{self.base_url}/{self.company_id}/webhooks"

        # Generate a WebHookSubscriptionId for new webhook registration
        # Inflow API requires this field for PUT requests
        webhook_subscription_id = str(uuid.uuid4())

        # Map event names to Inflow's expected format
        # Inflow uses salesOrder.created, salesOrder.updated for order events
        event_mapping = {
            "orderCreated": "salesOrder.created",
            "orderUpdated": "salesOrder.updated",
            "orderStatusChanged": "salesOrder.updated",
        }

        # Map events to Inflow's format, fallback to original if no mapping exists
        mapped_events = [event_mapping.get(e, e) for e in events]
        mapped_events = list(dict.fromkeys(mapped_events))

        payload = {
            "webHookSubscriptionId": webhook_subscription_id,
            "url": webhook_url,
            "events": mapped_events,
        }
        if settings.inflow_webhook_secret:
            payload["secret"] = settings.inflow_webhook_secret

        async with httpx.AsyncClient() as client:
            try:
                # Inflow API uses PUT for webhook registration (idempotent create/update)
                response = await client.put(url, json=payload, headers=self.headers)
                response.raise_for_status()
                result = response.json()
                logger.info(
                    f"Webhook registered successfully: {result.get('id', 'unknown')}"
                )
                return result
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Failed to register webhook: {e.response.status_code} - {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Error registering webhook: {e}", exc_info=True)
                raise

    async def list_webhooks(self) -> List[Dict[str, Any]]:
        """
        List all registered webhooks for this company.

        Returns:
            List of webhook registrations
        """
        url = f"{self.base_url}/{self.company_id}/webhooks"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()

                # Handle both dict with 'items' key and list response
                if isinstance(data, dict) and "items" in data:
                    return data["items"]
                elif isinstance(data, list):
                    return data
                else:
                    return []
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Failed to list webhooks: {e.response.status_code} - {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Error listing webhooks: {e}", exc_info=True)
                raise

    async def delete_webhook(self, webhook_id: str) -> bool:
        """
        Delete a webhook registration from Inflow.

        Args:
            webhook_id: Inflow's webhook ID

        Returns:
            True if successful
        """
        url = f"{self.base_url}/{self.company_id}/webhooks/{webhook_id}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.delete(url, headers=self.headers)
                response.raise_for_status()
                logger.info(f"Webhook {webhook_id} deleted successfully")
                return True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Webhook {webhook_id} not found")
                    return False
                logger.error(
                    f"Failed to delete webhook: {e.response.status_code} - {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Error deleting webhook: {e}", exc_info=True)
                raise

    def verify_webhook_signature(
        self, payload: bytes, signature: str, secret: Optional[str] = None
    ) -> bool:
        """
        Verify webhook signature using configured secret.

        Args:
            payload: Raw request body bytes
            signature: Signature from webhook header

        Returns:
            True if signature is valid
        """
        from app.utils.webhook_security import (
            verify_webhook_signature as verify_signature,
        )

        secret_to_use = secret or settings.inflow_webhook_secret
        return (
            verify_signature(payload, signature, secret_to_use)
            if secret_to_use
            else True
        )

    def _normalize_category_name(self, name: str) -> str:
        return " ".join(name.lower().replace("-", " ").split())

    def _is_asset_tag_required_line(
        self, category_name: Optional[str], unit_price: float
    ) -> bool:
        if unit_price <= 500:
            return False

        normalized = (
            self._normalize_category_name(category_name) if category_name else ""
        )
        return (
            normalized in {"desktops", "laptops", "custom computer"}
            or normalized.startswith("desktops ")
            or normalized.startswith("laptops ")
        )

    def requires_asset_tags(self, order: Dict[str, Any]) -> bool:
        order = self.build_remaining_order_view(order)
        lines = order.get("lines", []) or []
        if not isinstance(lines, list) or not lines:
            return False

        pending_category_ids: list[tuple[str, float]] = []

        for line in lines:
            if not isinstance(line, dict):
                continue

            raw_price = line.get("unitPrice")
            try:
                unit_price = float(raw_price or 0)
            except (TypeError, ValueError):
                unit_price = 0

            if unit_price <= 500:
                continue

            category_name = self._extract_category_name(line)
            if category_name:
                if self._is_asset_tag_required_line(category_name, unit_price):
                    return True
                continue

            category_id = self._extract_category_id(line)
            if category_id:
                pending_category_ids.append((str(category_id), unit_price))

        if not pending_category_ids:
            return False

        category_map: Dict[str, str] = {}
        try:
            category_map = self.get_category_map_sync()
        except Exception as exc:
            logger.warning(f"Failed to fetch inflow categories: {exc}")
            return False

        for category_id, unit_price in pending_category_ids:
            category_name = category_map.get(category_id)
            if self._is_asset_tag_required_line(category_name, unit_price):
                return True

        return False

    def build_asset_tag_requirement_key(self, order: Dict[str, Any]) -> tuple[Any, ...]:
        order = self.build_remaining_order_view(order)
        lines = order.get("lines", []) or []
        if not isinstance(lines, list) or not lines:
            return tuple()

        key_parts: list[tuple[str, str, str]] = []
        for line in lines:
            if not isinstance(line, dict):
                continue

            raw_price = line.get("unitPrice")
            try:
                unit_price = float(raw_price or 0)
            except (TypeError, ValueError):
                unit_price = 0

            if unit_price <= 500:
                continue

            category_name = self._extract_category_name(line)
            if category_name:
                key_parts.append(
                    (
                        f"{unit_price:.2f}",
                        self._normalize_category_name(category_name),
                        "",
                    )
                )
                continue

            category_id = self._extract_category_id(line)
            key_parts.append((f"{unit_price:.2f}", "", str(category_id or "")))

        return tuple(key_parts)

    def requires_asset_tags_cached(
        self,
        order: Dict[str, Any],
        cache: Dict[tuple[Any, ...], bool],
    ) -> bool:
        key = self.build_asset_tag_requirement_key(order)
        cached_result = cache.get(key)
        if cached_result is not None:
            return cached_result

        result = self.requires_asset_tags(order)
        cache[key] = result
        return result

    def _extract_product_name(
        self, line: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None
    ) -> str:
        product = line.get("product", {})
        return (
            product.get("name")
            or line.get("productName")
            or line.get("description")
            or (fallback or {}).get("product_name")
            or line.get("productId")
            or "Unknown Product"
        )

    def _extract_category_id(
        self, line: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        product = line.get("product", {})
        return product.get("categoryId") or (fallback or {}).get("category_id")

    def _extract_category_name(
        self, line: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        product = line.get("product", {})
        category = product.get("category", {})
        return category.get("name") or (fallback or {}).get("category_name")

    def fetch_categories_sync(self) -> List[Dict[str, Any]]:
        """Fetch product categories from Inflow API (sync version)."""
        endpoints = ["categories", "product-categories", "productCategories"]

        with httpx.Client() as client:
            for endpoint in endpoints:
                url = f"{self.base_url}/{self.company_id}/{endpoint}"
                try:
                    response = client.get(url, headers=self.headers)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        f"Failed to fetch inflow categories from {endpoint}: {exc}"
                    )
                    continue

                data = response.json()

                if isinstance(data, dict) and "items" in data:
                    return data["items"]
                if isinstance(data, list):
                    return data

        return []

    def get_category_map_sync(self) -> Dict[str, str]:
        cls = type(self)
        now = time.monotonic()
        if (
            cls._category_map_cache is not None
            and now < cls._category_map_cache_expires_at
        ):
            return cls._category_map_cache

        categories = self.fetch_categories_sync()
        category_map: Dict[str, str] = {}

        for category in categories:
            category_id = (
                category.get("categoryId")
                or category.get("id")
                or category.get("category_id")
            )
            name = (
                category.get("name")
                or category.get("categoryName")
                or category.get("label")
            )
            if category_id and name:
                category_map[str(category_id)] = name

        if category_map:
            cls._category_map_cache = category_map
            cls._category_map_cache_expires_at = now + cls._CATEGORY_MAP_TTL_SECONDS
            return category_map

        if cls._category_map_cache is not None:
            return cls._category_map_cache

        cls._category_map_cache = {}
        cls._category_map_cache_expires_at = now + cls._CATEGORY_MAP_EMPTY_TTL_SECONDS

        return cls._category_map_cache

    def get_asset_tag_serials(self, order: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract serial numbers for asset-tag-required items from inflow order data.
        Uses pickLines when available to preserve device pick order.
        """
        order = self.build_remaining_order_view(order)
        lines = order.get("lines", [])
        pick_lines = order.get("pickLines", [])

        line_unit_price_by_product: Dict[str, float] = {}
        for line in lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if not product_id:
                continue
            raw_price = line.get("unitPrice")
            try:
                unit_price = float(raw_price or 0)
            except (TypeError, ValueError):
                unit_price = 0
            existing = line_unit_price_by_product.get(product_id)
            if existing is None or unit_price > existing:
                line_unit_price_by_product[product_id] = unit_price

        def has_serials(source: List[Dict[str, Any]]) -> bool:
            return any(
                bool(line.get("quantity", {}).get("serialNumbers")) for line in source
            )

        pick_lines_have_serials = has_serials(pick_lines)
        source_lines = pick_lines if pick_lines_have_serials else lines

        line_serials_by_product: Dict[str, List[str]] = {}
        for line in lines:
            product_id = line.get("productId")
            serials = list(line.get("quantity", {}).get("serialNumbers", []) or [])
            if not product_id or not serials:
                continue
            line_serials_by_product.setdefault(product_id, []).extend(serials)

        line_products: Dict[str, Dict[str, Any]] = {}
        for line in lines:
            product_id = line.get("productId")
            if not product_id or product_id in line_products:
                continue
            line_products[product_id] = {
                "product_name": self._extract_product_name(line),
                "category_id": self._extract_category_id(line),
                "category_name": self._extract_category_name(line),
                "custom_fields": line.get("product", {}).get("customFields"),
            }

        category_map: Optional[Dict[str, str]] = None

        asset_tag_serials: List[Dict[str, Any]] = []
        index_by_product: Dict[str, int] = {}

        for line in source_lines:
            product_id = line.get("productId")
            fallback_info = line_products.get(product_id or "")
            product_name = self._extract_product_name(line, fallback_info)
            category_id = self._extract_category_id(line, fallback_info)
            category_name = self._extract_category_name(line, fallback_info)
            if not category_name and category_id:
                if category_map is None:
                    try:
                        category_map = self.get_category_map_sync()
                    except Exception as exc:
                        logger.warning(f"Failed to fetch inflow categories: {exc}")
                        category_map = {}
                category_name = category_map.get(str(category_id))

            raw_price = line.get("unitPrice")
            try:
                unit_price = (
                    float(raw_price)
                    if raw_price is not None
                    else float(line_unit_price_by_product.get(product_id or "", 0))
                )
            except (TypeError, ValueError):
                unit_price = float(
                    line_unit_price_by_product.get(product_id or "", 0) or 0
                )

            if not self._is_asset_tag_required_line(category_name, unit_price):
                continue

            serial_numbers = list(
                line.get("quantity", {}).get("serialNumbers", []) or []
            )
            if not serial_numbers and product_id in line_serials_by_product:
                serial_numbers = list(line_serials_by_product.get(product_id, []))

            if product_id in index_by_product:
                asset_tag_serials[index_by_product[product_id]]["serials"].extend(
                    serial_numbers
                )
                continue

            entry = {
                "product_id": product_id,
                "product_name": product_name,
                "category_id": category_id,
                "category_name": category_name,
                "serials": serial_numbers,
            }
            asset_tag_serials.append(entry)
            if product_id:
                index_by_product[product_id] = len(asset_tag_serials) - 1

        return asset_tag_serials

    # ========== SYNC VERSIONS FOR FLASK ==========

    def fetch_orders_sync(
        self,
        inventory_status: Optional[str] = None,
        is_active: bool = True,
        order_number: Optional[str] = None,
        count: int = 100,
        skip: int = 0,
        sort: str = "orderDate",
        sort_desc: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch orders from Inflow API (sync version)"""
        url = f"{self.base_url}/{self.company_id}/sales-orders"

        params = {
            "include": "pickLines.product,shipLines,packLines.product,lines.product,lines",
            "filter[isActive]": str(is_active).lower(),
            "count": str(count),
            "skip": str(skip),
            "sort": sort,
            "sortDesc": str(sort_desc).lower(),
        }

        if inventory_status:
            params["filter[inventoryStatus][]"] = inventory_status

        if order_number:
            params["filter[orderNumber]"] = order_number

        with httpx.Client() as client:
            response = client.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, dict) and "items" in data:
                return data["items"]
            elif isinstance(data, list):
                return data
            else:
                return []

    def get_order_by_number_sync(self, order_number: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific order by order number (sync version)"""
        orders = self.fetch_orders_sync(order_number=order_number, count=1)
        if orders:
            return orders[0]
        return None

    def sync_recent_started_orders_sync(
        self, max_pages: int = 3, per_page: int = 100, target_matches: int = 100
    ) -> List[Dict[str, Any]]:
        """Sync recent started orders that already have pickLines (sync version)."""
        matches = []

        for page in range(max_pages):
            orders = self.fetch_orders_sync(
                inventory_status="started",
                count=per_page,
                skip=page * per_page,
            )

            for order in orders:
                if self.is_started_and_picked(order):
                    matches.append(order)
                    if len(matches) >= target_matches:
                        return matches

            if len(orders) < per_page:
                break

        return matches

    def get_order_by_id_sync(self, sales_order_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific order by sales order ID (sync version)"""
        url = f"{self.base_url}/{self.company_id}/sales-orders/{sales_order_id}"
        params = {
            "include": "pickLines.product,shipLines,packLines.product,lines.product,lines"
        }

        with httpx.Client() as client:
            try:
                response = client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                raise

            data = response.json()
            if isinstance(data, dict) and "items" in data:
                return data["items"][0] if data["items"] else None
            if isinstance(data, list):
                return data[0] if data else None
            return data

    def update_order_remarks_sync(
        self, sales_order_id: str, order_remarks: str
    ) -> Dict[str, Any]:
        """Update the orderRemarks field for a sales order in inFlow."""
        order = self.get_order_by_id_sync(sales_order_id)
        if not order:
            raise ValueError(f"Sales order {sales_order_id} not found in Inflow")

        updated_order = dict(order)
        updated_order["orderRemarks"] = order_remarks

        url = f"{self.base_url}/{self.company_id}/sales-orders"
        with httpx.Client() as client:
            response = client.put(url, json=updated_order, headers=self.headers)
            response.raise_for_status()
            result = response.json()

        if isinstance(result, dict) and "items" in result:
            items = result.get("items") or []
            return items[0] if items else updated_order
        if isinstance(result, list):
            return result[0] if result else updated_order
        if isinstance(result, dict):
            return result
        return updated_order

    def update_proof_of_delivery_url_sync(
        self, sales_order_id: str, proof_of_delivery_url: str
    ) -> Dict[str, Any]:
        """Update the Proof of Delivery custom field (custom5) for a sales order in inFlow."""
        order = self.get_order_by_id_sync(sales_order_id)
        if not order:
            raise ValueError(f"Sales order {sales_order_id} not found in Inflow")

        updated_order = dict(order)
        custom_fields = updated_order.get("customFields")
        if not isinstance(custom_fields, dict):
            custom_fields = {}
        else:
            custom_fields = dict(custom_fields)

        custom_fields["custom5"] = proof_of_delivery_url
        updated_order["customFields"] = custom_fields

        url = f"{self.base_url}/{self.company_id}/sales-orders"
        with httpx.Client() as client:
            response = client.put(url, json=updated_order, headers=self.headers)
            response.raise_for_status()
            result = response.json()

        if isinstance(result, dict) and "items" in result:
            items = result.get("items") or []
            return items[0] if items else updated_order
        if isinstance(result, list):
            return result[0] if result else updated_order
        if isinstance(result, dict):
            return result
        return updated_order

    def fulfill_sales_order_sync(
        self, sales_order_id: str, db: Session = None, user_id: str = None
    ) -> Dict[str, Any]:
        """Fulfill a sales order (sync version)"""
        from app.services.audit_service import AuditService

        order = self.get_order_by_id_sync(sales_order_id)
        if not order:
            raise ValueError(f"Sales order {sales_order_id} not found in Inflow")

        if not order.get("pickLines"):
            order_number = order.get("orderNumber") or sales_order_id
            raise ValueError(f"Order {order_number} has no pickLines")

        if not order.get("customerId"):
            raise ValueError("Sales order missing customerId")

        now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        order_number = order.get("orderNumber") or sales_order_id
        container_number = f"DELIVERY-{order_number}"

        def positive_quantity(line: Dict[str, Any]) -> bool:
            qty = line.get("quantity", {})
            raw = qty.get("standardQuantity")
            if raw is None:
                return False
            try:
                return float(raw) > 0
            except (TypeError, ValueError):
                return False

        if not order.get("packLines"):
            pack_lines = []
            for line in order.get("lines", []):
                if not positive_quantity(line):
                    continue
                pack_lines.append(
                    {
                        "salesOrderPackLineId": str(uuid.uuid4()),
                        "productId": line.get("productId"),
                        "quantity": line.get("quantity"),
                        "description": line.get("description"),
                        "containerNumber": container_number,
                    }
                )
            order["packLines"] = pack_lines

        if not order.get("shipLines") and order.get("packLines"):
            order["shipLines"] = [
                {
                    "salesOrderShipLineId": str(uuid.uuid4()),
                    "carrier": "TechHub",
                    "containers": list(
                        {
                            line.get("containerNumber")
                            for line in order["packLines"]
                            if line.get("containerNumber")
                        }
                    ),
                    "shippedDate": now,
                }
            ]

        # Check if order is fully picked
        is_fully_picked = self._is_fully_picked(order)
        if not is_fully_picked:
            msg = f"Order {order_number} is only partially picked. Skipping InFlow fulfillment to avoid inventory issues."
            logger.warning(msg)

            if db:
                # Log the skip
                audit_service = AuditService(db)
                audit_service.log_action(
                    entity_type="inflow_order",
                    entity_id=sales_order_id,
                    action="fulfillment_skipped",
                    user_id=user_id,
                    description=msg,
                    audit_metadata={
                        "reason": "partial_pick",
                        "inflow_order_number": order.get("orderNumber"),
                    },
                )

            # Return success structure but indicate skipped
            return {
                "salesOrderId": sales_order_id,
                "orderNumber": order_number,
                "status": "skipped",
                "message": msg,
            }

        url = f"{self.base_url}/{self.company_id}/sales-orders"
        with httpx.Client() as client:
            response = client.put(url, json=order, headers=self.headers)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                raise self._build_fulfillment_http_error(
                    action="InFlow fulfillment",
                    order_number=order_number,
                    response=response,
                    payload=order,
                )
            result = response.json()

            if db:
                audit_service = AuditService(db)
                audit_service.log_action(
                    entity_type="inflow_order",
                    entity_id=sales_order_id,
                    action="fulfilled",
                    user_id=user_id,
                    description="Order fulfilled in inFlow system",
                    audit_metadata={
                        "inflow_order_number": order.get("orderNumber"),
                        "pick_lines_count": len(order.get("pickLines", [])),
                        "pack_lines_count": len(order.get("packLines", [])),
                        "ship_lines_count": len(order.get("shipLines", [])),
                    },
                )

            return result

    def register_webhook_sync(
        self, webhook_url: str, events: List[str]
    ) -> Dict[str, Any]:
        """Register a webhook with Inflow API (sync version)"""
        url = f"{self.base_url}/{self.company_id}/webhooks"
        webhook_subscription_id = str(uuid.uuid4())

        event_mapping = {
            "orderCreated": "salesOrder.created",
            "orderUpdated": "salesOrder.updated",
            "orderStatusChanged": "salesOrder.updated",
        }

        mapped_events = [event_mapping.get(e, e) for e in events]
        mapped_events = list(dict.fromkeys(mapped_events))

        payload = {
            "webHookSubscriptionId": webhook_subscription_id,
            "url": webhook_url,
            "events": mapped_events,
        }
        if settings.inflow_webhook_secret:
            payload["secret"] = settings.inflow_webhook_secret

        with httpx.Client() as client:
            response = client.put(url, json=payload, headers=self.headers)
            response.raise_for_status()
            result = response.json()
            logger.info(
                f"Webhook registered successfully: {result.get('id', 'unknown')}"
            )
            return result

    def list_webhooks_sync(self) -> List[Dict[str, Any]]:
        """List all registered webhooks (sync version)"""
        url = f"{self.base_url}/{self.company_id}/webhooks"

        with httpx.Client() as client:
            response = client.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, dict) and "items" in data:
                return data["items"]
            elif isinstance(data, list):
                return data
            else:
                return []

    def delete_webhook_sync(self, webhook_id: str) -> bool:
        """Delete a webhook registration (sync version)"""
        url = f"{self.base_url}/{self.company_id}/webhooks/{webhook_id}"

        with httpx.Client() as client:
            try:
                response = client.delete(url, headers=self.headers)
                response.raise_for_status()
                logger.info(f"Webhook {webhook_id} deleted successfully")
                return True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Webhook {webhook_id} not found")
                    return False
                raise
