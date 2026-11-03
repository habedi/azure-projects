"""Test the Event Streaming pipeline without credentials, network requests, or cloud resources."""

import importlib
import json
from dataclasses import asdict
from io import StringIO
from threading import Event
from typing import Any

import pytest
from azure.core.exceptions import AzureError
from azure.eventhub import EventData

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


class LocalEventBatch:
    """Collect real SDK event payloads without an AMQP connection."""

    def __init__(self) -> None:
        """Start an empty batch."""
        self.events: list[EventData] = []

    def add(self, event_data: EventData) -> None:
        """Add an event to the batch."""
        self.events.append(event_data)


class LocalEventHub:
    """Retain sent events between runs, as the real service does."""

    def __init__(self) -> None:
        """Start with no retained events or injected failures."""
        self.events: list[EventData] = []
        self.send_error: Exception | None = None
        self.receive_error: Exception | None = None
        self.callback_error: Exception | None = None


class LocalProducer:
    """Send actual EventData payloads into the local event log."""

    def __init__(self, hub: LocalEventHub, **kwargs: Any) -> None:
        """Connect to the per-test event log."""
        self.hub = hub

    def __enter__(self) -> LocalProducer:
        """Enter the producer context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit without network resources."""

    def create_batch(self) -> LocalEventBatch:
        """Create an event batch."""
        return LocalEventBatch()

    def send_batch(self, batch: LocalEventBatch) -> None:
        """Retain the emitted payloads or fail at the service boundary."""
        if self.hub.send_error:
            raise self.hub.send_error
        self.hub.events.extend(batch.events)


class LocalConsumer:
    """Deliver retained events and block until explicitly closed."""

    def __init__(self, hub: LocalEventHub, **kwargs: Any) -> None:
        """Prepare a consumer that can be stopped from another thread."""
        self.hub = hub
        self.closed = Event()

    def __enter__(self) -> LocalConsumer:
        """Enter the consumer context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Stop consumption on context exit."""
        self.close()

    def close(self) -> None:
        """Unblock the receiving thread."""
        self.closed.set()

    def receive_batch(self, on_event_batch: Any, on_error: Any, **kwargs: Any) -> None:
        """Deliver actual produced bytes rather than regenerate sample data."""
        if self.hub.receive_error:
            raise self.hub.receive_error
        if self.hub.callback_error:
            on_error(None, self.hub.callback_error)
        else:
            on_event_batch(None, self.hub.events)
        self.closed.wait()


@pytest.fixture(autouse=True)
def sandbox(offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch) -> LocalEventHub:
    """Use a local event log and real Blob SDK requests."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)
    hub = LocalEventHub()
    monkeypatch.setattr(
        experiment, "EventHubProducerClient", lambda **kwargs: LocalProducer(hub, **kwargs)
    )
    monkeypatch.setattr(
        experiment, "EventHubConsumerClient", lambda **kwargs: LocalConsumer(hub, **kwargs)
    )
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))
    return hub


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
    assert "Verification successful: telemetry events and summary counts match" in out
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
    """Reject missing fields, bad types, and out-of-range sensor values."""
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


def test_azure_error_handling(sandbox: LocalEventHub, capsys: pytest.CaptureFixture[str]) -> None:
    """Handle AzureError during streaming pipeline gracefully."""
    sandbox.send_error = AzureError("Event Hub send failed")
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


@pytest.mark.parametrize("mode", ["raised", "callback"])
def test_receive_failure(
    mode: str, sandbox: LocalEventHub, offline: OfflineAzure, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail before storage access for both SDK receive error paths."""
    error = AzureError("private-response")
    if mode == "raised":
        sandbox.receive_error = error
    else:
        sandbox.callback_error = error
    assert experiment.main() == 1
    assert not offline.transport.requests
    output = capsys.readouterr()
    assert "Streaming ingestion and validation succeeded" not in output.out
    assert "private-response" not in output.out + output.err


@pytest.mark.parametrize("received_count", [0, 1])
def test_receive_timeout(received_count: int) -> None:
    """Stop even an idle consumer and reject incomplete batches."""
    hub = LocalEventHub()
    hub.events = [EventData(json.dumps({"batch_id": "current"}))] * received_count
    consumer = LocalConsumer(hub)
    with pytest.raises(TimeoutError, match="Expected 2 batch events"):
        experiment.consume_telemetry_batch(consumer, 2, "current", timeout_seconds=0.01)
    assert consumer.closed.is_set()


def test_incorrect_batch_payloads(
    sandbox: LocalEventHub,
    offline: OfflineAzure,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject a complete event count when duplicate delivery replaces an emitted event."""

    class DuplicateConsumer(LocalConsumer):
        def receive_batch(self, on_event_batch: Any, on_error: Any, **kwargs: Any) -> None:
            self.hub.events[-1] = self.hub.events[0]
            super().receive_batch(on_event_batch, on_error, **kwargs)

    monkeypatch.setattr(
        experiment, "EventHubConsumerClient", lambda **kwargs: DuplicateConsumer(sandbox, **kwargs)
    )
    assert experiment.main() == 1
    assert "differs from the emitted batch" in capsys.readouterr().err
    assert not offline.transport.requests


def test_repeated_runs(
    sandbox: LocalEventHub,
    offline: OfflineAzure,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ignore retained events from earlier runs and reconcile only newly sent bytes."""
    sandbox.events.extend([EventData("not-json"), EventData("[]")])
    monkeypatch.setattr("builtins.input", lambda _: TARGET.resource_group_name)
    assert experiment.main() == 0
    assert experiment.main() == 0
    assert len(sandbox.events) == 14
    batch_ids = {json.loads(event.body_as_str())["batch_id"] for event in sandbox.events[2:]}
    assert len(batch_ids) == 2
    assert capsys.readouterr().out.count("Consumed 6 events from stream") == 2
    assert all(not blobs for blobs in offline.transport.containers.values())


@pytest.mark.parametrize("stage", ["upload", "verify"])
def test_failure_cleanup(stage: str, offline: OfflineAzure) -> None:
    """Remove partial uploads and both artifacts after verification errors."""
    if stage == "upload":
        offline.transport.upload_failure_after = 1
    else:
        offline.transport.download_override = b""
    assert experiment.main() == 1
    assert all(not blobs for blobs in offline.transport.containers.values())
    assert len([r for r in offline.transport.requests if r.method == "DELETE"]) == 2
    assert offline.transport.closed


def test_cleanup_failure(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Return failure and attempt every deletion when batch cleanup fails."""
    offline.transport.failures["delete_blob"] = 403
    assert experiment.main() == 1
    output = capsys.readouterr()
    assert "Cleanup failed" in output.err
    assert "Streaming ingestion and validation succeeded" not in output.out
    assert len([r for r in offline.transport.requests if r.method == "DELETE"]) == 2
    assert len(offline.transport.containers[TARGET.container_name]) == 2
