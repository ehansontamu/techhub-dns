import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.models.order import Order
from app.services.order_service import OrderService


def test_get_incomplete_steps_reports_expected_missing_steps():
    service = OrderService(MagicMock())
    order = MagicMock(spec=Order)
    order.inflow_order_id = "TH000134"
    order.tagged_at = None
    order.picklist_generated_at = None
    order.qa_completed_at = None

    with patch.object(service, "_requires_asset_tags", return_value=True):
        assert service._get_incomplete_steps(order) == ["asset_tagging", "picklist", "qa"]


def test_get_incomplete_steps_skips_asset_tagging_when_not_required():
    service = OrderService(MagicMock())
    order = MagicMock(spec=Order)
    order.inflow_order_id = "TH000134"
    order.tagged_at = None
    order.picklist_generated_at = None
    order.qa_completed_at = None

    with patch.object(service, "_requires_asset_tags", return_value=False):
        assert service._get_incomplete_steps(order) == ["picklist", "qa"]