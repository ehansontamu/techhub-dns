"""Packing validation, durable shipment creation, and label queue orchestration."""

import base64
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_UP

import httpx
from pydantic import ValidationError as SchemaError
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.order import Order, OrderStatus, ShippingWorkflowStatus
from app.models.print_job import PrintJob, PrintJobStatus
from app.models.shipment import Shipment, ShippingProduct
from app.models.shipment_order import ShipmentOrder
from app.models.shipping_contact import ShippingContact
from app.schemas.shipment import PackingDraft, ProductWeightUpdate, ShipmentRecipient
from app.services.audit_service import AuditService
from app.services.fedex_service import FedExRejected, FedExService, shipping_date
from app.services.inflow_service import InflowService
from app.services.shipping_destinations import order_city
from app.services.shipping_product_identity import product_identity
from app.utils.exceptions import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from app.utils.pdf_helpers import filter_picklines
from app.utils.timezone import to_utc_iso_z

def shipping_items(order: Order, products: dict | None = None) -> list[dict]:
    """Use only this leg's unshipped physical picks, never the full parent order."""
    data = order.inflow_data or {}
    details = {str(line.get("productId")): line for line in data.get("lines", []) if isinstance(line, dict)}
    items = {}
    for pick in filter_picklines(data, data.get("pickLines", [])):
        product_id = str(pick.get("productId") or "")
        detail = details.get(product_id, {})
        line = {**detail, **pick}
        # Pick lines can contain only a partial product object (for example, SKU).
        detail_product = detail.get("product") if isinstance(detail.get("product"), dict) else {}
        pick_product = pick.get("product") if isinstance(pick.get("product"), dict) else {}
        line["product"] = {**detail_product, **{key: value for key, value in pick_product.items() if value is not None and value != ""}}
        if not product_id or InflowService._is_service_line(line):
            continue
        quantity = Decimal(str((pick.get("quantity") or {}).get("standardQuantity", 0)))
        if quantity <= 0:
            continue
        if not quantity.is_finite() or quantity != int(quantity):
            raise ValidationError("Shipping requires whole-unit product quantities")
        identity = product_identity(product_id, line, (products or {}).get(product_id))
        item = items.setdefault(product_id, {
            "product_id": product_id,
            "name": identity.name,
            "sku": identity.sku,
            "quantity": 0,
        })
        item["quantity"] += int(quantity)
    return list(items.values())


class ShipmentService:
    def __init__(self, db, carrier=None, *, is_admin=False):
        self.db = db
        self.carrier = carrier or FedExService()
        self.is_admin = is_admin

    def validate_recipient(self, draft: PackingDraft):
        if draft.manual_address_override:
            if not self.is_admin:
                raise ValidationError("Only an admin can use a one-off address override")
            return
        contact = self.db.get(ShippingContact, draft.contact_id) if draft.contact_id else None
        if contact is None:
            raise ValidationError("Select a saved FedEx address-book contact before shipping")
        try:
            expected = ShipmentRecipient.model_validate(contact.recipient).model_dump()
        except SchemaError as exc:
            raise ValidationError("The saved contact needs complete shipping details; ask an admin to update it or use a one-off override") from exc
        if expected != draft.recipient.model_dump():
            raise ValidationError("The recipient must match the saved contact. Reselect it, or ask an admin to use a one-off override")

    def order(self, order_id: str, *, ready=False) -> Order:
        order = self.db.get(Order, order_id)
        if not order or order.hidden_from_ops:
            raise NotFoundError("Order", order_id)
        if ready and (order.status != OrderStatus.SHIPPING.value or not order.qa_completed_at or order.qa_method != "Shipping"):
            raise ValidationError("Complete shipping QA before preparing a shipment")
        if ready and order.shipping_workflow_status == ShippingWorkflowStatus.SHIPPED.value:
            raise ValidationError("This order has already been handed to the carrier")
        return order

    def get(self, order_id: str) -> Shipment | None:
        return (self.db.query(Shipment).join(ShipmentOrder, ShipmentOrder.shipment_id == Shipment.id)
                .filter(ShipmentOrder.order_id == order_id).first()
                or self.db.query(Shipment).filter_by(order_id=order_id).first())

    def member_orders(self, order: Order, draft: PackingDraft) -> list[Order]:
        ids = draft.order_ids or [order.id]
        if len(ids) != len(set(ids)) or order.id not in ids:
            raise ValidationError("Select distinct orders including the primary order")
        orders = [self.order(order_id, ready=True) for order_id in sorted(ids)]
        return orders

    def shipping_queue(self) -> dict:
        orders = self.db.query(Order).filter(Order.status == "shipping", Order.qa_completed_at.isnot(None),
                    Order.qa_method == "Shipping", Order.hidden_from_ops.is_(False)).order_by(Order.inflow_order_id).all()
        products = {product.product_id: product for product in self.db.query(ShippingProduct).all()}
        rows = []
        for order in orders:
            if order.shipping_workflow_status == ShippingWorkflowStatus.SHIPPED.value:
                continue
            shipment = self.get(order.id)
            rows.append({"id": order.id, "order_number": order.inflow_order_id, "recipient_name": order.recipient_name,
                "city": order_city(order), "address": (order.inflow_data or {}).get("shippingAddress") or {},
                "products": shipping_items(order, products), "shipment_id": shipment.id if shipment else None,
                "shipment_order_id": shipment.order_id if shipment else None,
                "shipment_status": shipment.status if shipment else None,
                "shipping_workflow_status": order.shipping_workflow_status})
        # Keep former response keys for open browser tabs from before this deployment.
        # New clients use the flat orders list and choose a shipment recipient explicitly.
        return {"orders": rows, "cities": [], "unmatched": rows}

    @staticmethod
    def serialize(shipment: Shipment | None, db=None) -> dict | None:
        if not shipment:
            return None
        draft = dict(shipment.draft)
        if (draft.get("rate_quote") or {}).get("policy") != "cheapest_only":
            draft.pop("rate_quote", None)
        jobs = db.query(PrintJob).filter(PrintJob.file_path.like(f"fedex://{shipment.id}/%" )).all() if db is not None else []
        return {
            "id": shipment.id, "status": shipment.status, "revision": shipment.revision,
            "order_ids": ([row.order_id for row in db.query(ShipmentOrder).filter_by(shipment_id=shipment.id).all()] if db is not None else shipment.draft.get("order_ids")) or [shipment.order_id],
            "environment": shipment.environment, "draft": draft,
            "master_tracking_number": shipment.master_tracking_number,
            "packages": [
                {**{k: v for k, v in package.items() if k != "encoded_label"}, "index": i,
                 "print_jobs": [{"id": job.id, "status": job.status, "last_error": job.last_error}
                                for job in jobs if job.file_path == f"fedex://{shipment.id}/{i}"]}
                for i, package in enumerate(shipment.packages or [])
            ],
            "last_error": shipment.last_error,
            "updated_at": to_utc_iso_z(shipment.updated_at),
        }

    def workspace(self, order_id: str, selected_ids: list[str] | None = None, contact_id: str | None = None) -> dict:
        order = self.order(order_id)
        existing = self.get(order_id)
        if existing:
            member_ids = [row.order_id for row in self.db.query(ShipmentOrder).filter_by(shipment_id=existing.id).all()] or [existing.order_id]
        else:
            member_ids = selected_ids or [order_id]
        if len(member_ids) > 50 or len(member_ids) != len(set(member_ids)) or order_id not in member_ids:
            raise ValidationError("Select up to 50 distinct orders including the primary order")
        members = [self.order(identifier) for identifier in member_ids]
        products = {p.product_id: p for p in self.db.query(ShippingProduct).all()}
        items = [{**item, "weight_lb": float(products[item["product_id"]].weight_lb)
                  if item["product_id"] in products and products[item["product_id"]].weight_lb is not None else None}
                 for member in members for item in [{**picked, "order_id": member.id, "order_number": member.inflow_order_id} for picked in shipping_items(member, products)]]
        address = (order.inflow_data or {}).get("shippingAddress") or {}
        customer = (order.inflow_data or {}).get("customer") or {}
        country = str(address.get("country") or "US").strip()
        if country.upper() in {"USA", "US", "UNITED STATES", "UNITED STATES OF AMERICA"}:
            country = "US"
        contact = self.db.get(ShippingContact, contact_id) if contact_id and not existing else None
        result = {
            "order_number": order.inflow_order_id, "order_status": order.status,
            "order_ids": member_ids, "orders": [{"id": member.id, "order_number": member.inflow_order_id} for member in members],
            "contact_id": contact_id if not existing else existing.draft.get("contact_id"),
            "shipping_workflow_status": order.shipping_workflow_status,
            "eligible": all(member.status == "shipping" and bool(member.qa_completed_at) and member.qa_method == "Shipping" and member.shipping_workflow_status != ShippingWorkflowStatus.SHIPPED.value for member in members),
            "items": items, "shipment": self.serialize(self.get(order_id), self.db),
            "config": {**self.carrier.config_status(), **({} if self.is_admin else {"missing": []})},
            "recipient": {
                "name": order.recipient_name or customer.get("name") or "",
                "company": address.get("companyName") or "",
                "phone": customer.get("phone") or "",
                "address1": address.get("address1") or "", "address2": address.get("address2") or "",
                "city": address.get("city") or "", "state": address.get("stateProvince") or address.get("state") or "",
                "postal_code": address.get("postalCode") or "", "country": country,
                "residential": False,
            },
        }
        if contact:
            result["recipient"] = contact.recipient
        return result

    def catalog(self) -> list[dict]:
        # Local reads do not call Inflow or rewrite stored product identities/weights.
        products = {}
        priorities = {}
        for (data,) in self.db.query(Order.inflow_data).filter(Order.inflow_data.isnot(None)).yield_per(200):
            for line in (data or {}).get("lines", []):
                if not isinstance(line, dict) or InflowService._is_service_line(line):
                    continue
                product_id = str(line.get("productId") or "")
                if not product_id:
                    continue
                identity = product_identity(product_id, line)
                previous = products.get(product_id)
                if previous is None or identity.name_priority > priorities[product_id]:
                    products[product_id] = {"product_id": product_id, "name": identity.name,
                        "sku": identity.sku or (previous or {}).get("sku", ""),
                        "weight_lb": None, "updated_at": None}
                    priorities[product_id] = identity.name_priority
                elif not previous["sku"] and identity.sku:
                    previous["sku"] = identity.sku
        for product in self.db.query(ShippingProduct).all():
            previous = products.get(product.product_id)
            identity = product_identity(product.product_id, saved=product)
            use_saved = previous is None or identity.name_priority >= priorities[product.product_id]
            products[product.product_id] = {
                "product_id": product.product_id,
                "name": identity.name if use_saved else previous["name"],
                "sku": identity.sku or (previous or {}).get("sku", ""),
                "weight_lb": float(product.weight_lb) if product.weight_lb is not None else None,
                "updated_at": to_utc_iso_z(product.updated_at),
            }
        return sorted(products.values(), key=lambda product: product["name"].casefold())

    def save_weight(self, product_id: str, update: ProductWeightUpdate, actor: str):
        if len(product_id) > 255 or not product_id.strip():
            raise ValidationError("Invalid product ID")
        product = self.db.get(ShippingProduct, product_id)
        if product is None:
            product = ShippingProduct(product_id=product_id)
            self.db.add(product)
        identity = product_identity(product_id, {"name": update.name, "sku": update.sku})
        product.name, product.sku = identity.name, identity.sku
        product.weight_lb = Decimal(str(update.weight_lb)).quantize(Decimal("0.001"), rounding=ROUND_UP)
        product.updated_by, product.updated_at = actor, datetime.utcnow()
        AuditService(self.db).log_action("shipping_product", product_id, "weight_updated", actor,
                                        new_value={"weight_lb": float(product.weight_lb)})
        self.db.commit()

    def import_catalog(self, actor: str) -> int:
        if not settings.inflow_api_key:
            raise ValidationError("Configure the Inflow integration before importing products")
        count = 0
        headers = {"Authorization": f"Bearer {settings.inflow_api_key}", "Accept": "application/json;version=2024-03-12"}
        # Import product identity only: unknown units must never become guessed weights.
        try:
            with httpx.Client(timeout=45) as client:
                for skip in range(0, 100000, 100):
                    response = client.get(f"{settings.inflow_api_url.rstrip('/')}/{settings.inflow_company_id}/products",
                                          headers=headers, params={"count": 100, "skip": skip, "filter[isActive]": "true"})
                    response.raise_for_status()
                    batch = response.json()
                    if not isinstance(batch, list):
                        raise ValueError("Invalid Inflow catalog response")
                    if not batch:
                        break
                    for record in batch:
                        product_id = str(record.get("productId") or "")
                        if not product_id or InflowService._is_service_line(record):
                            continue
                        product = self.db.get(ShippingProduct, product_id)
                        if product is None:
                            product = ShippingProduct(product_id=product_id)
                            self.db.add(product)
                        identity = product_identity(product_id, record)
                        product.name, product.sku = identity.name, identity.sku
                        product.updated_by = actor
                        self.db.flush()
                        count += 1
                else:
                    raise ValueError("Inflow catalog exceeded the import limit")
        except (httpx.HTTPError, ValueError) as exc:
            self.db.rollback()
            raise ExternalServiceError("Inflow", "catalog import") from exc
        AuditService(self.db).log_system_action("shipping_catalog_imported", user_id=actor, audit_metadata={"count": count})
        self.db.commit()
        return count

    def calculate(self, order: Order, draft: PackingDraft) -> list[dict]:
        self.validate_recipient(draft)
        members = self.member_orders(order, draft)
        available = {(member.id, item["product_id"]): item for member in members for item in shipping_items(member)}
        if not available:
            raise ValidationError("No unshipped physical items are available on this order part")
        assigned = Counter()
        weights = {p.product_id: p.weight_lb for p in self.db.query(ShippingProduct).filter(ShippingProduct.product_id.in_({key[1] for key in available})).all()}
        boxes = []
        for box in draft.boxes:
            if box.use_as_box and (len(box.items) != 1 or box.items[0].quantity != 1):
                raise ValidationError("Use as box requires exactly one product unit per package")
            total = Decimal("0")
            for item in box.items:
                item_order_id = item.order_id or (members[0].id if len(members) == 1 else None)
                key = (item_order_id, item.product_id)
                if key not in available:
                    raise ValidationError("A box contains an item that is not on this order part")
                weight = weights.get(item.product_id)
                if weight is None or weight <= 0:
                    raise ValidationError(f"Save a product weight for {available[key]['name']} before packing")
                total += weight * item.quantity
                assigned[key] += item.quantity
            shipping_weight = total.quantize(Decimal("0.001"), rounding=ROUND_UP)
            if shipping_weight <= 0 or shipping_weight > 150:
                raise ValidationError(f"{box.name}: FedEx packages must weigh more than 0 and at most 150 lb")
            boxes.append({**box.model_dump(), "product_weight_lb": float(total), "shipping_weight_lb": float(shipping_weight)})
        if assigned != Counter({key: item["quantity"] for key, item in available.items()}):
            raise ValidationError("Assign every unshipped item exactly once; box quantities must match this order part")
        return boxes

    @staticmethod
    def draft_schema(shipment: Shipment, revision: int) -> PackingDraft:
        try:
            return PackingDraft.model_validate({**{key: value for key, value in shipment.draft.items() if key in PackingDraft.model_fields}, "expected_revision": revision})
        except SchemaError as exc:
            raise ValidationError("This packing plan uses an older format; save it again before getting prices") from exc

    def reviewed_boxes(self, order: Order, shipment: Shipment, revision: int) -> list[dict]:
        boxes = self.calculate(order, self.draft_schema(shipment, revision))
        if boxes != shipment.draft["computed_boxes"]:
            raise ConflictError("Product weights or order items changed; save and review packing again")
        return boxes

    def quote_rates(self, order_id: str, revision: int, actor: str) -> Shipment:
        order = self.order(order_id, ready=True)
        shipment = self.get(order_id)
        if not shipment or shipment.status not in {"draft", "rejected"} or shipment.revision != revision:
            raise ConflictError("Save the latest packing plan before getting prices")
        boxes = self.reviewed_boxes(order, shipment, revision)
        # End the read transaction before calling FedEx; the final CAS rejects stale quotes.
        draft = dict(shipment.draft)
        shipment_id = shipment.id
        self.db.commit()
        options = self.carrier.rates(draft, boxes)
        if not options:
            raise ValidationError("FedEx returned no eligible prices; refresh before creating the shipment")
        cheapest = min(options, key=lambda option: (Decimal(option["amount"]), option["service_type"]))
        now = datetime.utcnow()
        quote = {"options": [cheapest], "policy": "cheapest_only", "quoted_at": to_utc_iso_z(now), "expires_at": to_utc_iso_z(now + timedelta(minutes=15)),
                 "ship_date": shipping_date(), "environment": self.carrier.environment}
        updated = self.db.query(Shipment).filter(Shipment.id == shipment_id, Shipment.revision == revision,
                                                Shipment.status.in_(("draft", "rejected"))).update({
            "draft": {**draft, "service_type": cheapest["service_type"], "rate_quote": quote},
            "revision": Shipment.revision + 1,
        }, synchronize_session=False)
        if not updated:
            self.db.rollback()
            raise ConflictError("Packing changed while prices loaded; reload and request new prices")
        AuditService(self.db).log_order_action(order_id, "fedex_rates_quoted", actor)
        self.db.commit()
        self.db.refresh(shipment)
        return shipment

    @staticmethod
    def checked_rate(shipment: Shipment) -> dict:
        quote = shipment.draft.get("rate_quote") or {}
        if quote and quote.get("policy") != "cheapest_only":
            raise ValidationError("Refresh FedEx prices to use the cheapest-only service policy")
        try:
            expires = datetime.fromisoformat(quote["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError, AttributeError) as exc:
            raise ValidationError("Get FedEx prices before creating the shipment") from exc
        if expires <= datetime.utcnow() or quote.get("ship_date") != shipping_date():
            raise ValidationError("The FedEx price estimate expired; refresh prices before creating the shipment")
        options = quote.get("options", [])
        selected = options[0] if len(options) == 1 and options[0]["service_type"] == shipment.draft["service_type"] else None
        if selected is None:
            raise ValidationError("Refresh FedEx prices; only the cheapest quoted service can be used")
        return selected

    def save_draft(self, order_id: str, draft: PackingDraft, actor: str) -> Shipment:
        order = self.order(order_id, ready=True)
        ids = draft.order_ids or [order_id]
        self.db.query(Order).filter(Order.id.in_(ids)).order_by(Order.id).with_for_update().populate_existing().all()
        boxes = self.calculate(order, draft)
        data = {**draft.model_dump(exclude={"expected_revision"}), "computed_boxes": boxes}
        shipment = self.get(order_id)
        if shipment is None:
            if draft.expected_revision != 0:
                raise ConflictError("Packing changed; reload before saving")
            shipment = Shipment(order_id=order_id, draft=data, created_by=actor)
            self.db.add(shipment)
            try:
                self.db.flush()
            except IntegrityError as exc:
                self.db.rollback()
                raise ConflictError("Another user saved packing; reload before saving") from exc
        else:
            if shipment.order_id not in ids:
                raise ValidationError("The primary order must remain in this shipment")
            updated = self.db.query(Shipment).filter(
                Shipment.id == shipment.id, Shipment.revision == draft.expected_revision,
                Shipment.status.in_(("draft", "rejected")),
            ).update({"draft": data, "revision": Shipment.revision + 1, "status": "draft", "last_error": None}, synchronize_session=False)
            if not updated:
                raise ConflictError("Packing changed or a shipment was already submitted; reload")
        reservations = self.db.query(ShipmentOrder).filter(ShipmentOrder.order_id.in_(ids)).all()
        if any(row.shipment_id != shipment.id for row in reservations):
            self.db.rollback()
            raise ConflictError("An order is already assigned to another shipment")
        # Legacy rows are also checked until their association backfill is applied.
        if self.db.query(Shipment).filter(Shipment.order_id.in_(ids), Shipment.id != shipment.id).first():
            self.db.rollback()
            raise ConflictError("An order is already assigned to another shipment")
        for row in self.db.query(ShipmentOrder).filter_by(shipment_id=shipment.id).all():
            if row.order_id not in ids:
                self.db.delete(row)
        reserved = {row.order_id for row in reservations}
        for identifier in ids:
            if identifier not in reserved:
                self.db.add(ShipmentOrder(order_id=identifier, shipment_id=shipment.id))
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError("Another user reserved an order; reload the shipping queue") from exc
        AuditService(self.db).log_order_action(order_id, "shipping_packing_saved", actor,
            audit_metadata={"contact_id": draft.contact_id, "manual_address_override": draft.manual_address_override, "order_ids": ids})
        self.db.commit()
        self.db.refresh(shipment)
        return shipment

    def create(self, order_id: str, revision: int, actor: str, *, confirm_high_shipping_cost: bool = False) -> Shipment:
        order = self.order(order_id, ready=True)
        shipment = self.get(order_id)
        if shipment is None:
            raise ValidationError("Save and review the packing plan first")
        if shipment.status == "processing_response":
            return self.finalize(shipment, actor)
        if shipment.status not in {"draft", "rejected"}:
            return shipment  # Browser retries never purchase a duplicate shipment.
        boxes = self.reviewed_boxes(order, shipment, revision)
        rate = self.checked_rate(shipment)
        if Decimal(rate["amount"]) > Decimal("100") and not confirm_high_shipping_cost:
            raise ValidationError(f"Shipping costs ${rate['amount']}. Are you sure?", field="confirm_high_shipping_cost", details={"confirmation_required": True, "amount": rate["amount"], "currency": rate["currency"]})
        payload = self.carrier.build_request(shipment.draft, boxes, self.order(shipment.order_id).inflow_order_id)
        # Atomic compare-and-set works even where SELECT FOR UPDATE is unavailable.
        updated = self.db.query(Shipment).filter(Shipment.id == shipment.id, Shipment.revision == revision,
                                                Shipment.status.in_(("draft", "rejected"))).update({
            "status": "creating", "revision": Shipment.revision + 1, "request_payload": payload,
            "environment": self.carrier.environment, "last_error": None,
        }, synchronize_session=False)
        if not updated:
            raise ConflictError("Packing changed or creation has already started; reload")
        AuditService(self.db).log_order_action(order_id, "fedex_creation_started", actor,
                                               audit_metadata={"shipment_id": shipment.id, "environment": self.carrier.environment, "shipping_estimate": rate["amount"], "high_cost_confirmed": confirm_high_shipping_cost, "order_ids": shipment.draft.get("order_ids") or [order_id]})
        self.db.commit()  # Durable before sending; don't hold an SQL transaction over HTTP.
        self.db.refresh(shipment)
        try:
            response = self.carrier.create_shipment(payload, shipment.id)
        except FedExRejected as exc:
            shipment.status, shipment.last_error = "rejected", str(exc)
            self.db.commit()
            return shipment
        except Exception:
            # Includes timeouts: FedEx may have created it. Never auto-resubmit.
            shipment.status = "needs_review"
            shipment.last_error = "FedEx outcome is uncertain. Reconcile this transaction with FedEx before creating another shipment."
            self.db.commit()
            return shipment
        shipment.carrier_response = response
        shipment.status = "processing_response"
        self.db.commit()  # Keep tracking and label data even if later processing fails.
        return self.finalize(shipment, actor)

    def finalize(self, shipment: Shipment, actor: str) -> Shipment:
        # Lock the saved response for local processing. This transaction contains
        # no network calls, and rolls back to processing_response if interrupted.
        claimed = self.db.query(Shipment).filter(
            Shipment.id == shipment.id, Shipment.status == "processing_response"
        ).update({"status": "finalizing"}, synchronize_session=False)
        if not claimed:
            self.db.refresh(shipment)
            return shipment
        try:
            tracking, packages = self.carrier.extract_packages(shipment.carrier_response, shipment.draft["computed_boxes"])
        except (ValueError, KeyError, TypeError):
            shipment.status = "needs_review"
            shipment.last_error = "FedEx responded, but complete PDF labels could not be read. The response is saved; reconcile it before resubmitting."
            self.db.commit()
            return shipment
        shipment.master_tracking_number, shipment.packages = tracking, packages
        shipment.status, shipment.last_error = "created", None
        self.db.flush()
        for index in range(len(packages)):
            self.queue_label(shipment, index, actor, automatic=True)
        for member_id in shipment.draft.get("order_ids") or [shipment.order_id]:
            AuditService(self.db).log_order_action(member_id, "fedex_shipment_created", actor,
                audit_metadata={"shipment_id": shipment.id, "tracking": tracking, "environment": shipment.environment,
                                "order_ids": shipment.draft.get("order_ids") or [shipment.order_id]})
        self.db.commit()
        return shipment

    def queue_label(self, shipment: Shipment, index: int, actor: str, *, automatic=False) -> PrintJob:
        if shipment.status != "created" or index < 0 or index >= len(shipment.packages or []):
            raise ValidationError("A saved FedEx label is required for printing")
        path = f"fedex://{shipment.id}/{index}"
        if not automatic:
            # Serialize concurrent reprint requests on the shipment row.
            self.db.query(Shipment).filter_by(id=shipment.id).with_for_update().first()
        existing = self.db.query(PrintJob).filter(PrintJob.file_path == path, PrintJob.status.in_(("pending", "claimed"))).first()
        if existing:
            return existing
        job = PrintJob(order_id=shipment.order_id, document_type="shipping_label", status=PrintJobStatus.PENDING,
                       trigger_source="automatic" if automatic else "manual", requested_by=actor, file_path=path)
        self.db.add(job)
        self.db.flush()
        AuditService(self.db).log_order_action(shipment.order_id, "shipping_label_print_queued", actor,
                                               audit_metadata={"print_job_id": job.id, "box": index + 1})
        return job

    def label(self, shipment_id: str, index: int) -> bytes:
        shipment = self.db.get(Shipment, shipment_id)
        if not shipment or shipment.status != "created" or index < 0 or index >= len(shipment.packages or []):
            raise NotFoundError("Shipping label")
        return base64.b64decode(shipment.packages[index]["encoded_label"], validate=True)
