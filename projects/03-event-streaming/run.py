"""Ingest, process, and persist real-time telemetry events using Azure Event Hubs and Storage."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError
from azure.eventhub import EventData, EventHubConsumerClient, EventHubProducerClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

INFRA_DIR = Path(__file__).resolve().parent / "infra"

RAW_TELEMETRY_EVENTS: list[dict[str, Any]] = [
    {
        "device_id": "sensor-alpha",
        "timestamp": "2026-11-03T10:00:00Z",
        "reading_id": "rd-001",
        "temperature_c": 21.5,
        "humidity_pct": 45.2,
        "status": "normal",
    },
    {
        "device_id": "sensor-beta",
        "timestamp": "2026-11-03T10:00:15Z",
        "reading_id": "rd-002",
        "temperature_c": 24.1,
        "humidity_pct": 52.0,
        "status": "normal",
    },
    {
        "device_id": "sensor-alpha",
        "timestamp": "2026-11-03T10:01:00Z",
        "reading_id": "rd-003",
        "temperature_c": 22.0,
        "humidity_pct": 46.0,
        "status": "normal",
    },
    {
        "device_id": "sensor-beta",
        "timestamp": "2026-11-03T10:01:15Z",
        "reading_id": "rd-004",
        "temperature_c": 24.8,
        "humidity_pct": 51.5,
        "status": "normal",
    },
    {
        # Duplicate record to be filtered during stream processing
        "device_id": "sensor-alpha",
        "timestamp": "2026-11-03T10:00:00Z",
        "reading_id": "rd-001",
        "temperature_c": 21.5,
        "humidity_pct": 45.2,
        "status": "normal",
    },
    {
        # Corrupted record with invalid timestamp, invalid humidity, and negative reading
        "device_id": "sensor-corrupt",
        "timestamp": "invalid-timestamp",
        "reading_id": "rd-corrupt",
        "temperature_c": -999.0,
        "humidity_pct": 150.0,
        "status": "error",
    },
]


@dataclass(frozen=True)
class Target:
    """Identify the sandbox resources recorded in OpenTofu state."""

    subscription_id: str
    resource_group_name: str
    location: str
    eventhub_namespace_fqdn: str
    eventhub_name: str
    storage_account_url: str
    container_name: str


def load_target() -> Target:
    """Read and validate OpenTofu outputs from the infra directory."""
    result = subprocess.run(
        ["tofu", f"-chdir={INFRA_DIR}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        outputs = json.loads(result.stdout)
        values = {
            name: outputs[name]["value"]
            for name in (
                "subscription_id",
                "resource_group_name",
                "location",
                "eventhub_namespace_fqdn",
                "eventhub_name",
                "storage_account_url",
                "container_name",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("All OpenTofu outputs must be non-empty strings.")
        target = Target(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-03-[a-z0-9-]+", target.resource_group_name):
            raise ValueError("Invalid resource_group_name in target state.")
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError("Invalid location in target state.")
        if not re.fullmatch(
            r"azpg03[a-z0-9-]+\.servicebus\.windows\.net", target.eventhub_namespace_fqdn
        ):
            raise ValueError("Invalid eventhub_namespace_fqdn in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", target.eventhub_name):
            raise ValueError("Invalid eventhub_name in target state.")
        if not re.fullmatch(
            r"https://azpg03[a-z0-9]+\.blob\.core\.windows\.net/?", target.storage_account_url
        ):
            raise ValueError("Invalid storage_account_url in target state.")
        if not re.fullmatch(r"[a-z0-9-]+", target.container_name):
            raise ValueError("Invalid container_name in target state.")
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError("Invalid or missing OpenTofu outputs. Run make up first.") from err

    parts = urlsplit(target.storage_account_url)
    if parts.scheme != "https" or parts.path not in ("", "/"):
        raise ValueError("Storage account URL must use HTTPS and have no path.")

    return target


def verify_target(target: Target) -> None:
    """Verify storage account and region with a read-only Azure CLI request."""
    account = urlsplit(target.storage_account_url).netloc.split(".")[0]
    result = subprocess.run(
        [
            "az",
            "storage",
            "account",
            "show",
            "--subscription",
            target.subscription_id,
            "--resource-group",
            target.resource_group_name,
            "--name",
            account,
            "--query",
            "{location:location,endpoint:primaryEndpoints.blob}",
            "--output",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    metadata = json.loads(result.stdout)
    if (
        not isinstance(metadata, dict)
        or metadata.get("location") != target.location
        or not isinstance(metadata.get("endpoint"), str)
        or metadata.get("endpoint", "").rstrip("/") != target.storage_account_url.rstrip("/")
    ):
        raise ValueError(
            "Azure storage account scope, region, or endpoint differs from experiment state."
        )


def is_valid_event(event: dict[str, Any]) -> bool:
    """Validate telemetry event structure, schema types, and sensor ranges."""
    required_fields = {
        "device_id": str,
        "timestamp": str,
        "reading_id": str,
        "temperature_c": (int, float),
        "humidity_pct": (int, float),
        "status": str,
    }
    for field, expected_type in required_fields.items():
        if field not in event or not isinstance(event[field], expected_type):
            return False

    # Validate ISO 8601 timestamp
    try:
        ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            return False
    except ValueError, TypeError:
        return False

    # Validate physical telemetry sensor limits
    if not (-50.0 <= float(event["temperature_c"]) <= 80.0):
        return False
    if not (0.0 <= float(event["humidity_pct"]) <= 100.0):
        return False
    if event["status"] not in {"normal", "warning"}:
        return False

    return True


def process_telemetry_stream(
    raw_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Clean, deduplicate, and aggregate streaming telemetry events."""
    seen_reading_ids: set[str] = set()
    cleaned_events: list[dict[str, Any]] = []
    corrupted_count = 0
    duplicate_count = 0

    for event in raw_events:
        if not is_valid_event(event):
            corrupted_count += 1
            continue

        reading_id = event["reading_id"]
        if reading_id in seen_reading_ids:
            duplicate_count += 1
            continue

        seen_reading_ids.add(reading_id)
        cleaned_events.append(event)

    device_readings: dict[str, list[dict[str, Any]]] = {}
    for event in cleaned_events:
        device_id = event["device_id"]
        device_readings.setdefault(device_id, []).append(event)

    aggregations: dict[str, Any] = {}
    for device_id, readings in device_readings.items():
        temps = [r["temperature_c"] for r in readings]
        humidities = [r["humidity_pct"] for r in readings]
        aggregations[device_id] = {
            "record_count": len(readings),
            "avg_temperature_c": round(sum(temps) / len(temps), 2),
            "min_temperature_c": round(min(temps), 2),
            "max_temperature_c": round(max(temps), 2),
            "avg_humidity_pct": round(sum(humidities) / len(humidities), 2),
        }

    summary = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "total_raw_events": len(raw_events),
        "valid_events_count": len(cleaned_events),
        "corrupted_events_filtered": corrupted_count,
        "duplicate_events_filtered": duplicate_count,
        "devices": aggregations,
    }

    return cleaned_events, summary


def send_telemetry_batch(
    producer: EventHubProducerClient,
    events: list[dict[str, Any]],
) -> int:
    """Send a batch of JSON telemetry events to Azure Event Hubs."""
    event_batch = producer.create_batch()
    sent_count = 0
    for event in events:
        payload = json.dumps(event).encode("utf-8")
        event_batch.add(EventData(payload))
        sent_count += 1
    producer.send_batch(event_batch)
    return sent_count


def consume_telemetry_batch(
    consumer: EventHubConsumerClient,
    expected_count: int,
    timeout_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    """Receive event payloads from Event Hub until expected count is reached or timeout expires."""
    received: list[dict[str, Any]] = []
    start_time = time.monotonic()

    def on_event_batch(partition_context: Any, event_batch: list[EventData]) -> None:
        for event in event_batch:
            try:
                payload = json.loads(event.body_as_str())
                if isinstance(payload, dict):
                    received.append(payload)
            except Exception:
                continue
        if len(received) >= expected_count or (time.monotonic() - start_time) >= timeout_seconds:
            consumer.close()

    try:
        consumer.receive_batch(
            on_event_batch=on_event_batch,
            max_batch_size=expected_count,
            max_wait_time=2.0,
            starting_position="-1",
        )
    except Exception:
        # Consumer closed gracefully from inside callback
        pass

    return received


def persist_events_and_summary(
    blob_service: BlobServiceClient,
    container_name: str,
    batch_id: str,
    events: list[dict[str, Any]],
    summary: dict[str, Any],
) -> tuple[str, str]:
    """Persist cleaned events and aggregation summary to ADLS Gen2 / blob storage."""
    container_client = blob_service.get_container_client(container_name)
    try:
        container_client.create_container()
    except AzureError as err:
        if getattr(err, "status_code", None) not in (
            403,
            409,
        ) and "ContainerAlreadyExists" not in str(err):
            raise

    events_blob_name = f"telemetry/batches/{batch_id}/events.jsonl"
    summary_blob_name = f"telemetry/batches/{batch_id}/summary.json"

    events_data = "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")
    summary_data = json.dumps(summary, indent=2).encode("utf-8")

    container_client.upload_blob(name=events_blob_name, data=events_data, overwrite=True)
    container_client.upload_blob(name=summary_blob_name, data=summary_data, overwrite=True)

    return events_blob_name, summary_blob_name


def verify_storage_persistence(
    blob_service: BlobServiceClient,
    container_name: str,
    events_blob_name: str,
    summary_blob_name: str,
    expected_valid_count: int,
) -> None:
    """Assert persisted events and summary match transformed metrics."""
    container_client = blob_service.get_container_client(container_name)

    events_client = container_client.get_blob_client(events_blob_name)
    downloaded_events = events_client.download_blob().readall().decode("utf-8")
    parsed_events = [json.loads(line) for line in downloaded_events.splitlines() if line.strip()]

    if len(parsed_events) != expected_valid_count:
        raise ValueError(
            f"Expected {expected_valid_count} events in storage, found {len(parsed_events)}"
        )

    summary_client = container_client.get_blob_client(summary_blob_name)
    downloaded_summary = json.loads(summary_client.download_blob().readall().decode("utf-8"))

    if downloaded_summary.get("valid_events_count") != expected_valid_count:
        raise ValueError("Summary valid_events_count does not match expected count.")


def cleanup_storage_batch(
    blob_service: BlobServiceClient,
    container_name: str,
    blob_names: tuple[str, ...],
) -> int:
    """Delete the blobs associated with the current batch."""
    container_client = blob_service.get_container_client(container_name)
    deleted_count = 0
    for blob_name in blob_names:
        try:
            container_client.delete_blob(blob_name)
            deleted_count += 1
        except AzureError:
            print(f"   Warning: Cleanup failed for {blob_name}.", file=sys.stderr)
    return deleted_count


def run_pipeline(target: Target) -> None:
    """Execute end-to-end event streaming ingest, processing, storage, and validation."""
    batch_id = f"azpg-03-{uuid4().hex}"
    print(f"Streaming execution batch: {batch_id}")

    with DefaultAzureCredential() as credential:
        # Step 1: Ingest raw telemetry into Event Hub
        print("1. Emitting telemetry events to Azure Event Hubs...")
        with EventHubProducerClient(
            fully_qualified_namespace=target.eventhub_namespace_fqdn,
            eventhub_name=target.eventhub_name,
            credential=credential,
        ) as producer:
            sent_count = send_telemetry_batch(producer, RAW_TELEMETRY_EVENTS)
            print(f"   Emitted {sent_count} events to {target.eventhub_name}.")

        # Step 2: Stream consumption
        print("2. Consuming streaming events from Event Hub...")
        with EventHubConsumerClient(
            fully_qualified_namespace=target.eventhub_namespace_fqdn,
            eventhub_name=target.eventhub_name,
            consumer_group="$Default",
            credential=credential,
        ) as consumer:
            received_events = consume_telemetry_batch(
                consumer, expected_count=len(RAW_TELEMETRY_EVENTS)
            )
            print(f"   Consumed {len(received_events)} events from stream.")

        # Step 3: Stream processing and transformation
        print(
            "3. Validating schema, filtering corrupted events, and computing device aggregations..."
        )
        cleaned_events, summary = process_telemetry_stream(received_events)
        print(
            f"   Processed {len(received_events)} records: {len(cleaned_events)} valid, "
            f"{summary['corrupted_events_filtered']} corrupted filtered, "
            f"{summary['duplicate_events_filtered']} duplicate filtered."
        )

        # Step 4: Persist to Azure Data Lake Storage Gen2
        print("4. Persisting cleaned telemetry and summary aggregations to storage...")
        with BlobServiceClient(
            account_url=target.storage_account_url, credential=credential
        ) as blob_service:
            events_blob, summary_blob = persist_events_and_summary(
                blob_service, target.container_name, batch_id, cleaned_events, summary
            )
            print(f"   Stored records in: {events_blob}")
            print(f"   Stored metrics in: {summary_blob}")

            # Step 5: Verification
            print("5. Verifying persisted storage records and business summaries...")
            verify_storage_persistence(
                blob_service,
                target.container_name,
                events_blob,
                summary_blob,
                expected_valid_count=len(cleaned_events),
            )
            print(
                "   Verification successful: all telemetry events and summary metrics match expected values."
            )
            for dev_id, dev_metrics in summary["devices"].items():
                print(
                    f"   Device '{dev_id}': {dev_metrics['record_count']} readings, "
                    f"Avg Temp: {dev_metrics['avg_temperature_c']}C, "
                    f"Avg Humidity: {dev_metrics['avg_humidity_pct']}%"
                )

            # Step 6: Artifact cleanup
            print("6. Cleaning up streaming batch storage artifacts...")
            deleted_count = cleanup_storage_batch(
                blob_service, target.container_name, (events_blob, summary_blob)
            )
            print(f"   Removed {deleted_count} batch artifacts from storage.")

        print("Streaming ingestion and validation succeeded.")


def main() -> int:
    """Confirm the verified target and run the event streaming pipeline."""
    try:
        target = load_target()
        verify_target(target)
        print(f"Subscription: {target.subscription_id}")
        print(f"Resource group: {target.resource_group_name}")
        print(f"Region: {target.location}")
        print(f"Event Hubs namespace: {target.eventhub_namespace_fqdn}")
        print(f"Event Hub: {target.eventhub_name}")
        print(f"Storage account: {target.storage_account_url}")
        print(f"Storage container: {target.container_name}")
        print(
            "This runs the telemetry streaming pipeline in Azure Event Hubs and Storage. "
            "Charges may apply to the Event Hubs namespace."
        )
        confirmation = input("Type the resource group name to continue: ")
        if confirmation != target.resource_group_name:
            print("Cancelled. No streaming data was changed.")
            return 1
        run_pipeline(target)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        print(
            "OpenTofu or Azure CLI failed. Check tool installation, make up, az login, "
            "and sandbox resource group state.",
            file=sys.stderr,
        )
        return 1
    except AzureError:
        print(
            "Azure Event Hubs or storage access failed. Check DefaultAzureCredential, "
            "role assignments, and network rules. Run make down when finished.",
            file=sys.stderr,
        )
        return 1
    except EOFError, KeyboardInterrupt:
        print(
            "Interrupted. Check cleanup output and run make down when finished.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
