"""Test the Event Streaming pipeline without credentials, network requests, or cloud resources."""

from dataclasses import asdict
from datetime import datetime, timezone
import importlib
from io import StringIO
import json
import subprocess
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from azure.core.exceptions import AzureError, ClientAuthenticationError, ServiceRequestError
from azure.eventhub import EventData
import pytest

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.03-event-streaming.run")

TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-03-test",
    location="norwayeast",
    eventhub_namespace_fqdn="azpg03test.servicebus.windows.net",
    eventhub_name="telemetry-stream",
    storage_account_url="https://azpg03test.blob.core.windows.net/",
    container_name="telemetry",
)


class MockEventDataBatch:
    """Collect test events added to the batch."""

    def __init__(self) -> None:
        self.events: list[EventData] = []

    def add(self, event_data: EventData) -> None:
        self.events.append(event_data)


class MockEventHubProducer:
    """In-memory event hub producer test double."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.sent_batches: list[MockEventDataBatch] = []

    def __enter__(self) -> "MockEventHubProducer":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def create_batch(self, *args: Any, **kwargs: Any) -> MockEventDataBatch:
        return MockEventDataBatch()

    def send_batch(self, batch: MockEventDataBatch) -> None:
        self.sent_batches.append(batch)


class MockEventHubConsumer:
    """In-memory event hub consumer test double."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.closed = False

    def __enter__(self) -> "MockEventHubConsumer":
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True

    def receive_batch(
        self,
        on_event_batch: Any,
        max_batch_size: int = 300,
        max_wait_time: float | None = None,
        starting_position: Any = None,
        **kwargs: Any,
    ) -> None:
        batch = [
            EventData(json.dumps(event).encode("utf-8"))
            for event in experiment.RAW_TELEMETRY_EVENTS
        ]
        on_event_batch(None, batch)


@pytest.fixture(autouse=True)
def sandbox(offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read local CLI output and confirmation while mocking Azure services."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {
        "location": TARGET.location,
        "endpoint": TARGET.storage_account_url,
    }
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)
    monkeypatch.setattr(experiment, "EventHubProducerClient", MockEventHubProducer)
    monkeypatch.setattr(experiment, "EventHubConsumerClient", MockEventHubConsumer)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))


def test_success(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the complete streaming pipeline, store records, and clean up batch files."""
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

    out = capsys.readouterr().out
    assert "Emitted 6 events to telemetry-stream" in out
    assert "Consumed 6 events from stream" in out
    assert "Processed 6 records: 4 valid, 1 corrupted filtered, 1 duplicate filtered" in out
    assert (
        "Verification successful: all telemetry events and summary metrics match expected values"
        in out
    )
    assert "Streaming ingestion and validation succeeded" in out


def test_invalid_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject mismatched confirmation input and exit with error."""
    monkeypatch.setattr("sys.stdin", StringIO("wrong-name\n"))
    assert experiment.main() == 1


def test_invalid_target_outputs(offline: OfflineAzure) -> None:
    """Handle missing or corrupted OpenTofu outputs gracefully."""
    offline.cli.outputs.update(tofu=json.dumps({"invalid": "structure"}))
    offline.cli.sync()
    assert experiment.main() == 1


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://azpg03test.blob.core.windows.net/",
        "https://azpg03test.blob.core.windows.net/extra-path",
        "https://invalid-account.blob.core.windows.net/",
    ],
)
def test_invalid_storage_urls(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch, invalid_url: str
) -> None:
    """Validate that storage account URL constraints are enforced."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    outputs["storage_account_url"] = {"value": invalid_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs))
    offline.cli.sync()
    assert experiment.main() == 1


def test_verify_target_mismatch(offline: OfflineAzure) -> None:
    """Detect differences between Azure CLI metadata and local OpenTofu state."""
    offline.cli.outputs.update(
        az=json.dumps({"location": "eastus", "endpoint": TARGET.storage_account_url})
    )
    offline.cli.sync()
    assert experiment.main() == 1


def test_is_valid_event() -> None:
    """Verify event validation logic for missing fields, bad types, and out-of-range sensor values."""
    valid = {
        "device_id": "sensor-01",
        "timestamp": "2026-11-03T10:00:00Z",
        "reading_id": "rd-001",
        "temperature_c": 20.0,
        "humidity_pct": 50.0,
        "status": "normal",
    }
    assert experiment.is_valid_event(valid) is True

    # Missing field
    assert experiment.is_valid_event({"device_id": "sensor-01"}) is False

    # Invalid timestamp
    assert experiment.is_valid_event({**valid, "timestamp": "not-a-timestamp"}) is False

    # Out of range temperature
    assert experiment.is_valid_event({**valid, "temperature_c": 120.0}) is False
    assert experiment.is_valid_event({**valid, "temperature_c": -100.0}) is False

    # Out of range humidity
    assert experiment.is_valid_event({**valid, "humidity_pct": 105.0}) is False
    assert experiment.is_valid_event({**valid, "humidity_pct": -5.0}) is False

    # Invalid status
    assert experiment.is_valid_event({**valid, "status": "unknown"}) is False


def test_process_telemetry_stream() -> None:
    """Test deduplication, corruption filtering, and aggregation computations."""
    cleaned, summary = experiment.process_telemetry_stream(experiment.RAW_TELEMETRY_EVENTS)
    assert len(cleaned) == 4
    assert summary["valid_events_count"] == 4
    assert summary["corrupted_events_filtered"] == 1
    assert summary["duplicate_events_filtered"] == 1
    assert "sensor-alpha" in summary["devices"]
    assert "sensor-beta" in summary["devices"]
    assert summary["devices"]["sensor-alpha"]["record_count"] == 2
    assert summary["devices"]["sensor-beta"]["record_count"] == 2


def test_azure_error_handling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handle AzureError during streaming pipeline gracefully."""

    class FailingProducer(MockEventHubProducer):
        def send_batch(self, batch: MockEventDataBatch) -> None:
            raise AzureError("Event Hub send failed")

    monkeypatch.setattr(experiment, "EventHubProducerClient", FailingProducer)
    assert experiment.main() == 1
    err = capsys.readouterr().err
    assert "Azure Event Hubs or storage access failed" in err


def test_cli_failure(offline: OfflineAzure) -> None:
    """Handle subprocess failures from OpenTofu or Azure CLI."""
    offline.cli.errors.update(tofu=1)
    offline.cli.sync()
    assert experiment.main() == 1


def test_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle keyboard interruption during confirmation prompt cleanly."""

    def raise_interrupt(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_interrupt)
    assert experiment.main() == 1


def test_verify_storage_persistence_mismatch(offline: OfflineAzure) -> None:
    """Raise ValueError when persisted blob events count does not match expected count."""
    offline.transport.containers[TARGET.container_name] = {
        "telemetry/batches/test/events.jsonl": b'{"device_id": "sensor-01"}\n',
        "telemetry/batches/test/summary.json": json.dumps({"valid_events_count": 1}).encode(
            "utf-8"
        ),
    }
    client = experiment.BlobServiceClient(account_url=TARGET.storage_account_url)
    with pytest.raises(ValueError, match="Expected 10 events in storage, found 1"):
        experiment.verify_storage_persistence(
            client,
            TARGET.container_name,
            "telemetry/batches/test/events.jsonl",
            "telemetry/batches/test/summary.json",
            expected_valid_count=10,
        )
