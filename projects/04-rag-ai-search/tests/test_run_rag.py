"""Test the RAG indexing and retrieval pipeline without credentials or cloud resources."""

from dataclasses import asdict
import importlib
from io import StringIO
import json
from types import SimpleNamespace
from typing import Any
import pytest
from azure.core.exceptions import AzureError

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.04-rag-ai-search.run")

TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-04-test",
    location="norwayeast",
    storage_account_url="https://azpg04test.blob.core.windows.net/",
    container_name="documents",
    search_service_endpoint="https://azpg04test.search.windows.net",
    search_service_name="azpg04test",
    search_index_name="azpg-04-rag-index",
    openai_endpoint="https://azpg04test.openai.azure.com/",
    embedding_deployment_name="text-embedding-3-small",
    chat_deployment_name="gpt-6-luna",
)


class MockSearchIndexClient:
    """In-memory test double for SearchIndexClient."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.indexes: dict[str, Any] = {}

    def create_or_update_index(self, index: Any) -> Any:
        self.indexes[index.name] = index
        return index

    def delete_index(self, index_name: str) -> None:
        self.indexes.pop(index_name, None)


class MockSearchClient:
    """In-memory test double for SearchClient."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.documents: list[dict[str, Any]] = []

    def upload_documents(self, documents: list[dict[str, Any]]) -> None:
        self.documents.extend(documents)

    def get_document_count(self) -> int:
        return len(self.documents)

    def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        # Return mock search matches
        return [
            {
                "chunk_id": "doc-cost-control-chunk-0",
                "doc_id": "doc-cost-control",
                "title": "Azure Cost Control and Teardown",
                "content": "Cloud cost control ensures experiments do not incur unexpected charges.",
                "@search.score": 0.895,
            },
            {
                "chunk_id": "doc-cloud-safety-chunk-0",
                "doc_id": "doc-cloud-safety",
                "title": "Azure Cloud Safety and Least Privilege",
                "content": "Cloud safety requires using least-privilege access and dedicated sandbox groups.",
                "@search.score": 0.742,
            },
        ]


class MockOpenAIEmbeddings:
    """In-memory embeddings generator test double."""

    def create(self, input: list[str], model: str) -> Any:
        vectors = [SimpleNamespace(embedding=[0.05] * 1536) for _ in input]
        return SimpleNamespace(data=vectors)


class MockOpenAIChatCompletions:
    """In-memory chat completions generator test double."""

    def __init__(self, answer: str | None = None) -> None:
        self.answer = (
            answer
            or "To manage cloud costs, run make down to destroy temporary resources [Source: doc-cost-control]."
        )

    def create(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))]
        )


class MockAzureOpenAI:
    """In-memory test double for AzureOpenAI client."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.embeddings = MockOpenAIEmbeddings()
        self.chat = SimpleNamespace(completions=MockOpenAIChatCompletions())


@pytest.fixture(autouse=True)
def sandbox(offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read local CLI output and confirmation while mocking Azure and OpenAI services."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {
        "location": TARGET.location,
        "endpoint": TARGET.storage_account_url,
    }
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)
    monkeypatch.setattr(experiment, "SearchIndexClient", MockSearchIndexClient)
    monkeypatch.setattr(experiment, "SearchClient", MockSearchClient)
    monkeypatch.setattr(experiment, "AzureOpenAI", MockAzureOpenAI)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))


def test_success(offline: OfflineAzure, capsys: pytest.CaptureFixture[str]) -> None:
    """Execute complete RAG document ingestion, hybrid search, answer generation, and cleanup."""
    assert experiment.main() == 0

    assert offline.cli.calls[0] == [
        "tofu",
        f"-chdir={experiment.INFRA_DIR}",
        "output",
        "-json",
    ]

    out = capsys.readouterr().out
    assert "Uploaded 3 source documents to container" in out
    assert "Configured search index: azpg-04-rag-index" in out
    assert "Retrieved 2 relevant passages from AI Search" in out
    assert "Citations extracted: ['doc-cost-control']" in out
    assert "Verification successful: grounded response generated with valid citations" in out
    assert "RAG pipeline execution succeeded" in out


def test_invalid_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject mismatched confirmation input and exit cleanly."""
    monkeypatch.setattr("sys.stdin", StringIO("wrong-name\n"))
    assert experiment.main() == 1


def test_invalid_target_outputs(offline: OfflineAzure) -> None:
    """Handle missing or corrupted OpenTofu outputs gracefully."""
    offline.cli.outputs.update(tofu=json.dumps({"invalid": "data"}))
    offline.cli.sync()
    assert experiment.main() == 1


def test_verify_target_mismatch(offline: OfflineAzure) -> None:
    """Detect differences between Azure CLI metadata and local OpenTofu state."""
    offline.cli.outputs.update(
        az=json.dumps({"location": "westeurope", "endpoint": TARGET.storage_account_url})
    )
    offline.cli.sync()
    assert experiment.main() == 1


def test_chunk_documents() -> None:
    """Verify document chunking segments words and attaches source metadata."""
    doc = [{"doc_id": "doc-test", "title": "Test Title", "text": "word " * 120}]
    chunks = experiment.chunk_documents(doc, max_chunk_words=50)
    assert len(chunks) == 3
    assert chunks[0]["chunk_id"] == "doc-test-chunk-0"
    assert chunks[0]["title"] == "Test Title"
    assert len(chunks[0]["content"].split()) == 50


def test_missing_citation_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Detect when model response fails to produce expected grounded citations."""

    class MissingCitationOpenAI(MockAzureOpenAI):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.chat = SimpleNamespace(
                completions=MockOpenAIChatCompletions(
                    answer="This answer contains no citations whatsoever."
                )
            )

    monkeypatch.setattr(experiment, "AzureOpenAI", MissingCitationOpenAI)
    assert experiment.main() == 1
    err = capsys.readouterr().err
    assert "Expected citation [Source: doc-cost-control] was not found" in err


def test_azure_error_handling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handle AzureError during search or storage operations."""

    class FailingSearchClient(MockSearchClient):
        def upload_documents(self, documents: list[dict[str, Any]]) -> None:
            raise AzureError("Index write rejected")

    monkeypatch.setattr(experiment, "SearchClient", FailingSearchClient)
    assert experiment.main() == 1
    err = capsys.readouterr().err
    assert "Azure AI Search, OpenAI, or storage access failed" in err


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
