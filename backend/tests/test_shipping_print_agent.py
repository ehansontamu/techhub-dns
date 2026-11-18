"""Printer routing checks; subprocess and HTTP are mocked."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("shipping_test_agent", Path(__file__).resolve().parents[2] / "ops/print_agent/agent.py")
agent_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_module)


def test_label_and_picklist_target_separate_printers():
    config = SimpleNamespace(
        api_base_url="http://localhost", agent_token="test", printer_name="Existing LaserJet",
        label_printer_name="Brother QL-1110NWB", sumatra_pdf_path="SumatraPDF.exe", print_timeout_seconds=120,
    )
    agent = object.__new__(agent_module.PicklistPrintAgent)
    agent.config = config
    with patch.object(agent_module.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
        agent.print_pdf(Path("test-label.pdf"), shipping_label=True)
        label_command = run.call_args.args[0]
        assert label_command[label_command.index("-print-to") + 1] == "Brother QL-1110NWB"
        assert label_command[label_command.index("-print-settings") + 1] == "noscale"
        agent.print_pdf(Path("test-picklist.pdf"))
        picklist_command = run.call_args.args[0]
        assert picklist_command[picklist_command.index("-print-to") + 1] == "Existing LaserJet"
        assert "-print-settings" not in picklist_command


def test_agent_claims_labels_only_when_label_printer_is_configured():
    agent = object.__new__(agent_module.PicklistPrintAgent)
    agent.config = SimpleNamespace(api_base_url="http://localhost", label_printer_name="")
    agent.session = Mock()
    agent.session.post.return_value.json.return_value = {"job": None}
    agent.claim_next_job()
    assert agent.session.post.call_args.kwargs["json"] == {"document_types": ["picklist"]}
    agent.config.label_printer_name = "Brother QL-1110NWB"
    agent.claim_next_job()
    assert agent.session.post.call_args.kwargs["json"] == {"document_types": ["picklist", "shipping_label"]}
