"""Delivery proof gating, retry behavior, attribution, and transient photo storage."""

import io
import os
import runpy
from contextlib import contextmanager
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import pytest
from flask import Flask
from PIL import Image
from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from werkzeug.datastructures import FileStorage

from app.database import Base
from app.models.audit_log import AuditLog
from app.models.order import Order, OrderStatus
from app.schemas.order import OrderDetailResponse, SignatureData
from app.schemas.delivery_run import OrderSummary
from app.services.delivery_evidence_service import (
    DeliveryEvidenceService, MAX_PHOTO_BYTES, MAX_REQUEST_BYTES, current_evidence, normalize_photo,
)
from app.services.order_service import OrderService
from app.utils.exceptions import ConflictError, ExternalServiceError, ValidationError


def photo(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, format="PNG")
    stream.seek(0)
    return FileStorage(stream, filename="package.png", content_type="image/png")


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    order = Order(inflow_order_id="TH1001", status="in-delivery", qa_method="Delivery")
    db.add(order)
    db.commit()
    monkeypatch.setattr("app.services.order_service.settings.local_document_storage", str(tmp_path))
    monkeypatch.setattr(OrderService, "generate_bundled_documents", lambda *_args: (
        "https://sharepoint.test/signed/TH1001_signed.pdf", "https://sharepoint.test/bundles/TH1001_bundle.pdf", None,
    ))
    service = DeliveryEvidenceService(db)
    record = service.stage_signature(order.id, SignatureData(signature_image="signature"), "runner@example.test")
    yield db, order, record, service, tmp_path
    db.close()
    engine.dispose()


class SharePoint:
    def __init__(self, fail_on=None):
        self.folders = []
        self.uploads = []
        self.fail_on = fail_on
        self.files = {}
        for folder, filename in (("signed", "TH1001_signed.pdf"), ("qa", "TH1001_qa.pdf")):
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            output = io.BytesIO()
            writer.write(output)
            self.files[(folder, filename)] = output.getvalue()

    def ensure_folder(self, folder):
        self.folders.append(folder)
        return f"https://sharepoint.test/{folder}"

    def upload_file(self, content, folder, filename):
        if self.fail_on == len(self.uploads) + 1:
            raise RuntimeError("SharePoint unavailable")
        self.uploads.append((content, folder, filename))
        self.files[(folder, filename)] = content
        return f"https://sharepoint.test/{folder}/{filename}"

    def download_file(self, folder, filename):
        return self.files.get((folder, filename))


def test_signature_is_resumable_and_does_not_complete_delivery(delivery):
    db, order, record, service, _path = delivery
    assert order.status == "in-delivery"
    assert order.signature_captured_at is not None
    assert current_evidence(order)["id"] == record["id"]
    again = service.stage_signature(order.id, SignatureData(signature_image="retry"), "other@example.test")
    assert again["id"] == record["id"]
    assert len(order.delivery_evidence) == 1
    assert OrderDetailResponse.model_validate(order).delivery_evidence[0].signed_by == "runner@example.test"
    assert OrderSummary.model_validate(order).signature_captured_at == order.signature_captured_at
    assert db.query(AuditLog).filter_by(order_id=order.id).count() == 1


def test_direct_status_completion_cannot_bypass_proof(delivery):
    db, order, _record, _service, _path = delivery
    with pytest.raises(ValidationError, match="delivery photos or a skip reason"):
        OrderService(db).transition_status(order.id, OrderStatus.DELIVERED)
    assert order.status == "in-delivery"


def test_invalid_status_or_stale_order_blocks_signature_before_pdf_io(delivery, monkeypatch):
    db, _order, _record, service, _path = delivery
    order = Order(inflow_order_id="TH2000", status="picked")
    db.add(order)
    db.commit()
    calls = []
    monkeypatch.setattr(OrderService, "generate_bundled_documents", lambda *_args: calls.append(True))
    with pytest.raises(ValidationError, match="In Delivery"):
        service.stage_signature(order.id, SignatureData(signature_image="sig"), "runner")
    order.status = "in-delivery"
    db.commit()
    with pytest.raises(ConflictError):
        service.stage_signature(order.id, SignatureData(signature_image="sig", expected_updated_at="2000-01-01T00:00:00Z"), "runner")
    assert calls == []


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_no_photos_requires_nonblank_reason(delivery, reason):
    _db, order, record, service, _path = delivery
    with pytest.raises(ValidationError, match="At least one photo"):
        service.complete(order.id, record["id"], [], reason, "runner@example.test")
    assert order.status == "in-delivery"


def test_skip_is_attributed_audited_and_idempotent(delivery):
    db, order, record, service, _path = delivery
    order, result = service.complete(order.id, record["id"], [], " Camera unavailable ", "runner@example.test")
    assert order.status == "delivered"
    assert result["skip_reason"] == "Camera unavailable"
    assert result["completed_by"] == "runner@example.test"
    assert result["completed_at"]
    count = db.query(AuditLog).count()
    assert service.complete(order.id, record["id"], [], "retry", "other@example.test")[1] == result
    assert db.query(AuditLog).count() == count
    assert any("Camera unavailable" in (log.reason or "") for log in db.query(AuditLog).all())


def test_photo_completion_and_no_local_archive(delivery, monkeypatch):
    _db, order, record, service, path = delivery
    sharepoint = SharePoint()
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)
    order, result = service.complete(order.id, record["id"], [photo()], None, "runner@example.test")
    assert order.status == "delivered"
    assert len(result["photos"]) == 1
    assert result["photos"][0]["uploaded_by"] == "runner@example.test"
    assert sharepoint.folders == []
    assert sharepoint.uploads[0][1] == "bundles"
    assert sharepoint.uploads[0][0].startswith(b"\xff\xd8")
    assert sharepoint.uploads[1][1:] == ("bundles", "TH1001_bundle.pdf")
    assert len(PdfReader(io.BytesIO(sharepoint.uploads[1][0])).pages) == 3
    assert result["photos"][0]["url"].startswith("https://sharepoint.test/bundles/")
    assert list(path.iterdir()) == []


def test_upload_failure_retains_signature_and_deduplicates_retry(delivery, monkeypatch):
    db, order, record, service, path = delivery
    sharepoint = SharePoint(fail_on=2)
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)
    with pytest.raises(ExternalServiceError, match="upload delivery photo"):
        service.complete(order.id, record["id"], [photo("red"), photo("blue")], None, "runner@example.test")
    db.refresh(order)
    assert order.status == "in-delivery"
    assert order.signature_captured_at
    assert len(current_evidence(order)["photos"]) == 1
    assert list(path.iterdir()) == []
    sharepoint.fail_on = None
    order, result = service.complete(order.id, record["id"], [photo("red"), photo("blue")], None, "runner@example.test")
    assert order.status == "delivered"
    assert len(result["photos"]) == 2
    assert len(sharepoint.uploads) == 3
    assert len(PdfReader(io.BytesIO(sharepoint.uploads[-1][0])).pages) == 4


def test_bundle_update_failure_retries_without_duplicate_photo_pages(delivery, monkeypatch):
    db, order, record, service, _path = delivery
    sharepoint = SharePoint(fail_on=2)
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)

    with pytest.raises(ExternalServiceError, match="update delivery bundle"):
        service.complete(order.id, record["id"], [photo()], None, "runner@example.test")
    db.refresh(order)
    assert order.status == "in-delivery"
    assert len(current_evidence(order)["photos"]) == 1

    sharepoint.fail_on = None
    order, result = service.complete(order.id, record["id"], [photo()], None, "runner@example.test")
    assert order.status == "delivered"
    assert len(result["photos"]) == 1
    assert len(sharepoint.uploads) == 2
    assert len(PdfReader(io.BytesIO(sharepoint.uploads[-1][0])).pages) == 3


def test_pending_legacy_photo_can_be_added_to_bundle(delivery, monkeypatch):
    db, order, record, service, _path = delivery
    sharepoint = SharePoint()
    checksum, content = normalize_photo(photo())
    filename = f"photo-{checksum}.jpg"
    legacy_folder = f"delivery-photos/TH1001/{record['id']}"
    sharepoint.files[(legacy_folder, filename)] = content
    record["photos"] = [{"url": f"https://sharepoint.test/{legacy_folder}/{filename}",
                         "filename": filename, "sha256": checksum}]
    order.delivery_evidence = [record]
    db.commit()
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)

    order, result = service.complete(order.id, record["id"], [], None, "runner@example.test")
    assert order.status == "delivered"
    assert len(result["photos"]) == 1
    assert len(PdfReader(io.BytesIO(sharepoint.uploads[-1][0])).pages) == 3


def test_ten_photos_allowed_eleven_rejected(delivery, monkeypatch):
    _db, order, record, service, _path = delivery
    sharepoint = SharePoint()
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)
    with pytest.raises(ValidationError, match="at most 10"):
        service.complete(order.id, record["id"], [photo()] * 11, None, "runner@example.test")
    selected = [photo((i * 20, 0, 0)) for i in range(10)]
    assert len(service.complete(order.id, record["id"], selected, None, "runner@example.test")[1]["photos"]) == 10


def test_partial_leg_uses_original_order_folder(delivery, monkeypatch):
    db, order, record, service, _path = delivery
    parent = Order(inflow_order_id="TH1000", status="picked")
    db.add(parent)
    db.flush()
    order.parent_order_id = parent.id
    order.inflow_order_id = "TH1000-P1"
    bundle_url = "https://sharepoint.test/bundles/TH1000_bundles/TH1000-P1_bundle.pdf"
    order.bundle_path = bundle_url
    record["bundle_path"] = bundle_url
    order.delivery_evidence = [record]
    db.commit()
    sharepoint = SharePoint()
    sharepoint.files[("signed", "TH1000-P1_signed.pdf")] = sharepoint.files[("signed", "TH1001_signed.pdf")]
    sharepoint.files[("qa", "TH1000-P1_qa.pdf")] = sharepoint.files[("qa", "TH1001_qa.pdf")]
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)
    service.complete(order.id, record["id"], [photo()], None, "runner@example.test")
    assert sharepoint.uploads[0][1] == "bundles/TH1000_bundles"
    assert sharepoint.uploads[-1][1:] == ("bundles/TH1000_bundles", "TH1000-P1_bundle.pdf")


def test_old_proof_cannot_complete_reopened_order(delivery):
    db, order, record, service, _path = delivery
    order.signature_captured_at = None
    db.commit()
    with pytest.raises(ConflictError):
        service.complete(order.id, record["id"], [], "Reason", "runner@example.test")


def test_images_validate_content_and_remove_metadata():
    with pytest.raises(ValidationError, match="valid JPEG"):
        normalize_photo(FileStorage(io.BytesIO(b"not an image"), filename="fake.jpg"))
    with pytest.raises(ValidationError, match="10 MB"):
        normalize_photo(FileStorage(io.BytesIO(b"x" * (MAX_PHOTO_BYTES + 1))))
    output = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "Private location description"
    Image.new("RGB", (2100, 50)).save(output, format="JPEG", exif=exif)
    output.seek(0)
    _checksum, content = normalize_photo(FileStorage(output))
    with Image.open(io.BytesIO(content)) as normalized:
        assert max(normalized.size) == 2048
        assert not normalized.getexif()


def test_route_closes_upload_stream_and_blocks_oversized_request(delivery, monkeypatch):
    from app.api.routes import orders as routes

    db, order, record, _service, _path = delivery
    sharepoint = SharePoint(fail_on=1)
    monkeypatch.setattr("app.services.delivery_evidence_service.get_sharepoint_service", lambda: sharepoint)

    @contextmanager
    def get_db():
        yield db

    monkeypatch.setattr(routes, "get_db", get_db)
    monkeypatch.setattr(routes, "get_current_user_email", lambda: "runner@example.test")
    app = Flask(__name__)
    with app.test_request_context(method="POST", data={"photos": (photo().stream, "package.png")}):
        stream = routes.request.files["photos"].stream
        with pytest.raises(ExternalServiceError):
            routes.complete_delivery_evidence.__wrapped__(order.id, record["id"])
        assert stream.closed
    with app.test_request_context(method="POST", content_type="multipart/form-data", environ_overrides={"CONTENT_LENGTH": str(MAX_REQUEST_BYTES + 1)}):
        with pytest.raises(ValidationError, match="101 MB"):
            routes.complete_delivery_evidence.__wrapped__(order.id, record["id"])
        assert "files" not in routes.request.__dict__
    with app.test_request_context(method="POST", json=[]):
        with pytest.raises(ValidationError, match="JSON object"):
            routes.complete_delivery_evidence.__wrapped__(order.id, record["id"])
        with pytest.raises(ValidationError, match="JSON object"):
            routes.sign_order.__wrapped__(order.id)


def test_additive_migration_preserves_existing_orders():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/0023_add_delivery_evidence.py"))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE orders (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO orders (id) VALUES ('existing-order')"))
        with Operations.context(MigrationContext.configure(connection)):
            revision["upgrade"]()
        assert connection.execute(text("SELECT id, delivery_evidence FROM orders")).one() == ("existing-order", None)
    engine.dispose()
