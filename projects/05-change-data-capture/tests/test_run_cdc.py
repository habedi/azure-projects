"""Test Change Data Capture pipeline without credentials, network requests, or cloud resources."""

import importlib
import json
from dataclasses import asdict
from io import StringIO
from typing import Any

import httpx2
import pytest
from azure.core.exceptions import AzureError, HttpResponseError
from azure.cosmos.exceptions import CosmosHttpResponseError
from openai import AzureOpenAI

from tests.rag_support import OpenAITransport
from tests.support import OfflineAzure

experiment = importlib.import_module("projects.05-change-data-capture.run")

TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-05-test",
    location="norwayeast",
    cosmos_endpoint="https://azpg05test-cosmos.documents.azure.com:443/",
    cosmos_database_name="azpg-05-cdc-db",
    cosmos_container_name="orders",
    storage_account_url="https://azpg05teststore.blob.core.windows.net/",
    table_endpoint="https://azpg05teststore.table.core.windows.net/",
    table_name="ordersummaries",
    blob_container_name="cdc-analytics",
    openai_endpoint="https://azpg05test-openai.openai.azure.com/",
    chat_deployment_name="gpt-6-luna",
)


class LocalCosmosContainer:
    """In-memory Cosmos DB container double."""

    def __init__(self) -> None:
        """Initialize in-memory items and error states."""
        self.items: dict[str, dict[str, Any]] = {}
        self.upsert_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.empty_feed: bool = False

    def upsert_item(self, body: dict[str, Any]) -> None:
        """Store item or raise injected failure."""
        if self.upsert_error:
            raise self.upsert_error
        self.items[body["id"]] = dict(body)

    def query_items_change_feed(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return changed items or empty list."""
        if self.empty_feed:
            return []
        return list(self.items.values())

    def delete_item(self, item: str, partition_key: str) -> None:
        """Delete item or raise injected failure."""
        if self.delete_error:
            raise self.delete_error
        self.items.pop(item, None)


class LocalCosmosDatabase:
    """In-memory Cosmos DB database double."""

    def __init__(self, container: LocalCosmosContainer) -> None:
        """Attach container double."""
        self.container = container

    def get_container_client(self, container: str) -> LocalCosmosContainer:
        """Return container double."""
        return self.container


class LocalCosmosClient:
    """In-memory CosmosClient double."""

    def __init__(self, container: LocalCosmosContainer, **kwargs: Any) -> None:
        """Initialize client context."""
        self.container = container
        self.closed = False

    def __enter__(self) -> LocalCosmosClient:
        """Enter client context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit client context."""
        self.closed = True

    def get_database_client(self, database: str) -> LocalCosmosDatabase:
        """Return database double."""
        return LocalCosmosDatabase(self.container)


class LocalTableClient:
    """In-memory Azure Table storage client double."""

    def __init__(self) -> None:
        """Initialize in-memory entities and failure flags."""
        self.entities: dict[tuple[str, str], dict[str, Any]] = {}
        self.upsert_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.create_table_error: Exception | None = None
        self.corrupt_verification: bool = False

    def create_table(self) -> None:
        """Simulate table creation or raise configured error."""
        if self.create_table_error:
            raise self.create_table_error

    def upsert_entity(self, entity: dict[str, Any]) -> None:
        """Upsert entity in memory or raise error."""
        if self.upsert_error:
            raise self.upsert_error
        self.entities[(str(entity["PartitionKey"]), str(entity["RowKey"]))] = dict(entity)

    def get_entity(self, partition_key: str, row_key: str) -> dict[str, Any]:
        """Fetch entity by key."""
        if self.corrupt_verification:
            return {}
        return self.entities.get((partition_key, row_key), {})

    def delete_entity(self, partition_key: str, row_key: str) -> None:
        """Delete entity by key or raise error."""
        if self.delete_error:
            raise self.delete_error
        self.entities.pop((partition_key, row_key), None)


class LocalTableServiceClient:
    """In-memory TableServiceClient double."""

    def __init__(self, table_client: LocalTableClient, **kwargs: Any) -> None:
        """Initialize service client context."""
        self.table_client = table_client
        self.closed = False

    def __enter__(self) -> LocalTableServiceClient:
        """Enter service context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit service context."""
        self.closed = True

    def get_table_client(self, table_name: str) -> LocalTableClient:
        """Return table client double."""
        return self.table_client


@pytest.fixture(autouse=True)
def sandbox(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport]:
    """Configure offline test doubles for Cosmos DB, Table Storage, and OpenAI."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)

    cosmos_container = LocalCosmosContainer()
    table_client = LocalTableClient()
    openai_transport = OpenAITransport()
    openai_transport.answer = json.dumps(
        {
            "sentiment": "positive",
            "urgency": "normal",
            "category": "retail_order",
            "summary": "Customer order placed for standard fulfillment.",
        }
    )

    monkeypatch.setattr(
        experiment,
        "CosmosClient",
        lambda *args, **kwargs: LocalCosmosClient(cosmos_container, **kwargs),
    )
    monkeypatch.setattr(
        experiment,
        "TableServiceClient",
        lambda *args, **kwargs: LocalTableServiceClient(table_client, **kwargs),
    )

    def create_openai_client(**kwargs: Any) -> AzureOpenAI:
        if "azure_ad_token_provider" not in kwargs and "api_key" not in kwargs:
            kwargs["api_key"] = "mock-key"
        kwargs.setdefault("api_version", "2024-06-01")
        return AzureOpenAI(
            http_client=httpx2.Client(transport=openai_transport), max_retries=0, **kwargs
        )

    monkeypatch.setattr(experiment, "AzureOpenAI", create_openai_client)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))

    return cosmos_container, table_client, openai_transport


def test_success(
    offline: OfflineAzure,
    capsys: pytest.CaptureFixture[str],
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Execute complete change capture, AI enrichment, analytical updates, and cleanup."""
    cosmos_container, table_client, _ = sandbox
    assert experiment.main() == 0

    assert offline.cli.calls[0] == [
        "tofu",
        f"-chdir={experiment.INFRA_DIR}",
        "output",
        "-json",
    ]

    out = capsys.readouterr().out
    assert "Written 3 documents to Cosmos DB." in out
    assert "Captured 3 change events from change feed." in out
    assert "Enriching captured change records with Azure OpenAI GPT-6 Luna..." in out
    assert "Upserted 3 materialized rows to Table Storage." in out
    assert "Published 2 partitioned files to Blob Storage." in out
    assert "Verification successful: analytical views match captured transactions." in out
    assert "Cleaned up all temporary CDC artifacts." in out
    assert "Change Data Capture pipeline execution succeeded." in out

    # Verify that all temporary artifacts were cleaned up
    assert len(cosmos_container.items) == 0
    assert len(table_client.entities) == 0
    assert all(not blobs for blobs in offline.transport.containers.values())


def test_invalid_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject mismatched confirmation input and exit cleanly."""
    monkeypatch.setattr("sys.stdin", StringIO("wrong-rg-name\n"))
    assert experiment.main() == 1


def test_invalid_target_outputs(offline: OfflineAzure) -> None:
    """Handle missing or corrupted OpenTofu outputs gracefully."""
    offline.cli.outputs.update(tofu=json.dumps({"invalid": "data"}))
    offline.cli.sync()
    assert experiment.main() == 1


@pytest.mark.parametrize(
    ("key", "invalid_val"),
    [
        ("subscription_id", "not-a-uuid"),
        ("resource_group_name", "invalid_rg"),
        ("location", "Norway East"),
        ("cosmos_endpoint", "http://insecure.documents.azure.com"),
        ("cosmos_database_name", "invalid/database"),
        ("cosmos_container_name", "invalid/container"),
        ("storage_account_url", "http://insecure.blob.core.windows.net"),
        ("table_endpoint", "http://insecure.table.core.windows.net"),
        ("table_name", "invalid-table-name!"),
        ("blob_container_name", "INVALID_UPPERCASE"),
        ("openai_endpoint", "http://insecure.openai.azure.com"),
        ("chat_deployment_name", "invalid/name"),
    ],
)
def test_target_validation_error(
    key: str, invalid_val: str, offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject invalid fields in OpenTofu output state."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    outputs[key] = {"value": invalid_val}
    offline.cli.outputs.update(tofu=json.dumps(outputs))
    offline.cli.sync()
    assert experiment.main() == 1


def test_verify_target_mismatch(offline: OfflineAzure) -> None:
    """Detect differences between Azure CLI metadata and local OpenTofu state."""
    offline.cli.outputs.update(
        az=json.dumps({"location": "westeurope", "endpoint": TARGET.storage_account_url})
    )
    offline.cli.sync()
    assert experiment.main() == 1


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


def test_openai_enrichment_failure(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle Azure OpenAI API errors during record enrichment."""
    _, _, openai_transport = sandbox
    openai_transport.failures["chat"] = 500
    assert experiment.main() == 1


def test_cosmos_write_failure(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle Cosmos DB write failures gracefully."""
    cosmos_container, _, _ = sandbox
    cosmos_container.upsert_error = CosmosHttpResponseError(status_code=500)
    assert experiment.main() == 1


def test_table_storage_failure(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle Table Storage write failures gracefully."""
    _, table_client, _ = sandbox
    table_client.upsert_error = HttpResponseError("Table failure", status_code=500)
    assert experiment.main() == 1


def test_cleanup_failure(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle teardown errors and report remaining artifacts."""
    _, table_client, _ = sandbox
    table_client.delete_error = HttpResponseError("Delete entity failed", status_code=500)
    assert experiment.main() == 1


def test_cosmos_cleanup_failure(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle Cosmos DB item deletion errors during teardown."""
    cosmos_container, _, _ = sandbox
    cosmos_container.delete_error = CosmosHttpResponseError(status_code=500)
    assert experiment.main() == 1


def test_existing_table_and_container(
    offline: OfflineAzure,
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle pre-existing storage table and blob container without error."""
    _, table_client, _ = sandbox
    table_client.create_table_error = AzureError("TableAlreadyExists")
    # Container already exists in Blob transport
    offline.transport.containers[TARGET.blob_container_name] = {}
    assert experiment.main() == 0


def test_empty_blob_verification_failure(
    offline: OfflineAzure,
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise error when verified blob is empty."""

    def mock_verify_results(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Blob snapshot was empty.")

    monkeypatch.setattr(experiment, "verify_pipeline_results", mock_verify_results)
    assert experiment.main() == 1


def test_change_feed_timeout(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle change feed timeout when expected records are missing."""
    cosmos_container, _, _ = sandbox
    cosmos_container.empty_feed = True
    ticks = iter([0.0, 0.0, 20.0])
    monkeypatch.setattr("time.monotonic", lambda: next(ticks, 30.0))
    assert experiment.main() == 1


def test_verification_failure(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Handle verification failure when table entities are missing or invalid."""
    _, table_client, _ = sandbox
    table_client.corrupt_verification = True
    assert experiment.main() == 1


def test_enrich_change_with_ai_regex_fallback(
    sandbox: tuple[LocalCosmosContainer, LocalTableClient, OpenAITransport],
) -> None:
    """Parse JSON when OpenAI response contains conversational prose wrapper."""
    _, _, openai_transport = sandbox
    openai_transport.answer = (
        'Here is the analysis:\n{"sentiment": "negative", "urgency": "high", '
        '"category": "defect_support", "summary": "Damaged goods reported."}\nThank you!'
    )
    with experiment.AzureOpenAI(azure_endpoint=TARGET.openai_endpoint) as client:
        result = experiment.enrich_change_with_ai(
            client, TARGET.chat_deployment_name, experiment.SAMPLE_ORDERS[2]
        )
        assert result["sentiment"] == "negative"
        assert result["urgency"] == "high"
        assert result["category"] == "defect_support"
        assert result["ai_summary"] == "Damaged goods reported."
