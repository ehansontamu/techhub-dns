import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.order_service import OrderService


def test_partial_leg_bundle_uses_original_order_folder():
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)
        picklist_path = temp_path / "picklist.pdf"
        signed_source = temp_path / "signed-source.pdf"
        qa_source = temp_path / "qa-source.pdf"
        picklist_path.write_bytes(b"picklist")
        signed_source.write_bytes(b"signed")
        qa_source.write_bytes(b"qa")

        partial_leg = SimpleNamespace(
            id="child-id",
            inflow_order_id="TH5000-P2",
            parent_order_id="parent-id",
            has_remainder=None,
            remainder_order_id=None,
            picklist_path=str(picklist_path),
            qa_path="sharepoint://qa/TH5000-P2_qa.json",
            qa_data={},
            signed_picklist_path=None,
            bundle_path=None,
        )
        parent_order = SimpleNamespace(id="parent-id", inflow_order_id="TH5000")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            partial_leg,
            parent_order,
        ]
        service = OrderService(db)
        service._local_doc_path = (  # type: ignore[method-assign]
            lambda category, filename: temp_path / category / filename
        )
        service._apply_signature_to_pdf = (  # type: ignore[method-assign]
            lambda pdf_path, signature_data: str(signed_source)
        )
        service._generate_qa_pdf = (  # type: ignore[method-assign]
            lambda qa_data, order: str(qa_source)
        )
        service._bundle_pdfs = (  # type: ignore[method-assign]
            lambda pdf_paths, output_path: Path(output_path).write_bytes(b"bundle")
        )

        class _FakeSharePointService:
            is_enabled = True

            def __init__(self):
                self.folder_calls = []
                self.upload_calls = []

            def ensure_folder(self, subfolder):
                self.folder_calls.append(subfolder)
                return "https://sharepoint.example.test/bundles/TH5000_bundles"

            def upload_file(self, content, subfolder, filename):
                self.upload_calls.append((subfolder, filename))
                return f"https://sharepoint.example.test/{subfolder}/{filename}"

        sharepoint = _FakeSharePointService()
        with patch(
            "app.services.sharepoint_service.get_sharepoint_service",
            return_value=sharepoint,
        ):
            signed_url, bundle_url, folder_url = service.generate_bundled_documents(
                partial_leg.id,
                {},
            )

        assert sharepoint.folder_calls == ["bundles/TH5000_bundles"]
        assert (
            "bundles/TH5000_bundles",
            "TH5000-P2_bundle.pdf",
        ) in sharepoint.upload_calls
        assert bundle_url.endswith(
            "/bundles/TH5000_bundles/TH5000-P2_bundle.pdf"
        )
        assert folder_url == (
            "https://sharepoint.example.test/bundles/TH5000_bundles"
        )
        assert partial_leg.bundle_path == bundle_url
        assert partial_leg.signed_picklist_path == signed_url
