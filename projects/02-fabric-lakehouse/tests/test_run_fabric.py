"""Test the Microsoft Fabric Medallion pipeline without credentials or cloud resources."""

from dataclasses import asdict
import importlib
from io import StringIO
import json
import subprocess
from urllib.parse import urlsplit
from uuid import UUID

from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
import pytest

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.02-fabric-lakehouse.run")

TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-02-test",
    location="westeurope",
    fabric_capacity_name="azpg02test",
    workspace_name="ws-azpg-02-test",
    lakehouse_name="lh_azpg_02_test",
    onelake_endpoint="https://onelake.blob.fabric.microsoft.com",
)


@pytest.fixture(autouse=True)
def sandbox(offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read local CLI output and confirmation while using the real Blob SDK."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {
        "location": TARGET.location,
        "name": TARGET.fabric_capacity_name,
        "state": "Active",
    }
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))


def test_success(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the complete Medallion pipeline and clean up all batch artifacts."""
    assert experiment.main() == 0

    assert offline.cli.calls[0] == [
        "tofu",
        f"-chdir={experiment.INFRA_DIR}",
        "output",
        "-json",
    ]
    az_args = offline.cli.calls[1]
    assert az_args[az_args.index("--subscription") + 1] == TARGET.subscription_id
    assert az_args[az_args.index("--resource-group") + 1] == TARGET.resource_group_name
    assert az_args[az_args.index("--capacity-name") + 1] == TARGET.fabric_capacity_name

    output = capsys.readouterr().out
    assert "Reconciliation successful: all metrics match expected values" in output
    assert "Total valid transactions: 4" in output
    assert "Total revenue: $375.5" in output
    assert "Pipeline batch artifacts removed" in output

    assert offline.transport.closed
    assert offline.credential.closed


def test_cancel(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to run when the user does not confirm the resource group name."""
    monkeypatch.setattr("sys.stdin", StringIO("wrong-rg-name\n"))
    assert experiment.main() == 1
    output = capsys.readouterr().out
    assert "Cancelled. No Fabric data was changed." in output


def test_suspended_capacity(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse to execute pipeline if Fabric capacity is in suspended state."""
    metadata = {
        "location": TARGET.location,
        "name": TARGET.fabric_capacity_name,
        "state": "Suspended",
    }
    offline.cli.outputs["az"] = json.dumps(metadata)
    offline.cli.sync()

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "is suspended" in output
    assert "Run make resume before executing the pipeline." in output


def test_mismatched_capacity_metadata(
    offline: OfflineAzure, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject capacity metadata when name or region differs from project state."""
    metadata = {
        "location": "eastus",
        "name": TARGET.fabric_capacity_name,
        "state": "Active",
    }
    offline.cli.outputs["az"] = json.dumps(metadata)
    offline.cli.sync()

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Azure Fabric capacity scope or region differs" in output


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("subscription_id", "not-a-uuid"),
        ("resource_group_name", "wrong-prefix-rg"),
        ("location", "West Europe"),
        ("fabric_capacity_name", "wrongprefix"),
        ("workspace_name", "invalid_workspace_name"),
        ("lakehouse_name", "invalid-lakehouse-with-hyphens!"),
        ("onelake_endpoint", "http://insecure-endpoint.com"),
    ],
)
def test_invalid_target_values(
    key: str, value: str, offline: OfflineAzure, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject invalid output values in target configuration."""
    outputs = {name: {"value": val} for name, val in asdict(TARGET).items()}
    outputs[key] = {"value": value}
    offline.cli.outputs["tofu"] = json.dumps(outputs)
    offline.cli.sync()

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Invalid project outputs" in output


def test_non_string_outputs(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject OpenTofu outputs containing non-string or null values."""
    outputs = {name: {"value": 12345} for name in asdict(TARGET).keys()}
    offline.cli.outputs["tofu"] = json.dumps(outputs)
    offline.cli.sync()

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Invalid project outputs" in output


def test_reconciliation_revenue_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Detect when Gold layer total revenue differs from expected calculations."""
    modified_events = [
        {**experiment.RAW_EVENTS[0], "amount": 999.00},
        *experiment.RAW_EVENTS[1:],
    ]
    monkeypatch.setattr(experiment, "RAW_EVENTS", modified_events)

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Expected total revenue 375.50" in output


def test_reconciliation_status_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Detect when Gold layer status counts differ from expected distributions."""
    modified_events = [
        {**experiment.RAW_EVENTS[0]},
        {**experiment.RAW_EVENTS[1]},
        {**experiment.RAW_EVENTS[2], "status": "cancelled"},
        *experiment.RAW_EVENTS[3:],
    ]
    monkeypatch.setattr(experiment, "RAW_EVENTS", modified_events)

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Status distribution mismatch in Gold layer." in output


@pytest.mark.parametrize("program", ["tofu", "az"])
def test_command_failure(
    program: str, offline: OfflineAzure, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report CLI process failures clearly."""
    offline.cli.errors[program] = 1
    offline.cli.sync()

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "OpenTofu or Azure CLI failed" in output


def test_command_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handle process timeout cleanly."""

    def timed_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["tofu"], timeout=30)

    monkeypatch.setattr(experiment.subprocess, "run", timed_out)
    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "OpenTofu or Azure CLI failed" in output


def test_authentication_failure(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Handle credential failure when authenticating against OneLake."""
    offline.credential.error = ClientAuthenticationError("token acquisition failed")
    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "OneLake storage access failed" in output


def test_storage_service_failure(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Handle Azure storage service request errors gracefully."""
    offline.transport.failures["upload"] = ServiceRequestError("connection reset")
    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "OneLake storage access failed" in output


def test_reconciliation_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Detect when Gold layer metrics do not reconcile with expected counts."""
    monkeypatch.setattr(experiment, "RAW_EVENTS", [experiment.RAW_EVENTS[0]])

    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Expected 4 valid transactions, found 1." in output


def test_transform_bronze_to_silver() -> None:
    """Directly test Bronze-to-Silver parsing, filtering, and deduplication."""
    lines = [
        "",  # empty line
        json.dumps(
            {
                "transaction_id": "tx-1",
                "timestamp": "2026-10-27T10:00:00Z",
                "customer_id": "c1",
                "amount": 50.0,
                "status": "completed",
                "category": "books",
            }
        ),
        json.dumps(
            {
                "transaction_id": "tx-1",  # duplicate
                "timestamp": "2026-10-27T10:00:00Z",
                "customer_id": "c1",
                "amount": 50.0,
                "status": "completed",
                "category": "books",
            }
        ),
        "not-a-valid-json-string",
        json.dumps(
            {
                "transaction_id": "bad-id",  # does not start with tx-
                "timestamp": "2026-10-27T10:00:00Z",
                "customer_id": "c2",
                "amount": 10.0,
                "status": "completed",
                "category": "books",
            }
        ),
        json.dumps(
            {
                "transaction_id": "tx-2",
                "timestamp": "not-a-timestamp",
                "customer_id": "c2",
                "amount": 10.0,
                "status": "completed",
                "category": "books",
            }
        ),
    ]

    records, metrics = experiment.transform_bronze_to_silver(lines)
    assert len(records) == 1
    assert records[0]["transaction_id"] == "tx-1"
    assert metrics["valid"] == 1
    assert metrics["duplicates"] == 1
    assert metrics["corrupted"] == 3


def test_aggregate_silver_to_gold() -> None:
    """Directly test Gold metric aggregation calculations."""
    records = [
        {
            "transaction_id": "tx-1",
            "timestamp": "2026-10-27T10:00:00Z",
            "customer_id": "c1",
            "amount": 100.0,
            "status": "completed",
            "category": "electronics",
        },
        {
            "transaction_id": "tx-2",
            "timestamp": "2026-10-27T10:05:00Z",
            "customer_id": "c2",
            "amount": 20.0,
            "status": "refunded",
            "category": "clothing",
        },
        {
            "transaction_id": "tx-3",
            "timestamp": "2026-10-27T10:10:00Z",
            "customer_id": "c3",
            "amount": 50.0,
            "status": "completed",
            "category": "electronics",
        },
    ]

    gold = experiment.aggregate_silver_to_gold(records)
    assert gold["total_transactions"] == 3
    assert gold["completed_count"] == 2
    assert gold["refunded_count"] == 1
    assert gold["total_revenue"] == 150.0
    assert gold["average_transaction_amount"] == 75.0
    assert gold["revenue_by_category"]["electronics"] == 150.0
    assert gold["revenue_by_category"].get("clothing", 0.0) == 0.0


def test_interrupt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Handle keyboard interrupt during input prompt."""

    def raise_interrupt(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_interrupt)
    assert experiment.main() == 1
    output = capsys.readouterr().err
    assert "Interrupted" in output
