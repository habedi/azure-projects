"""Test the RAG indexing and retrieval pipeline without credentials or cloud resources."""

import importlib
import json
from dataclasses import asdict
from io import StringIO
from typing import Any

import httpx2
import pytest
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI

from tests.rag_support import OpenAITransport, SearchTransport
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


@pytest.fixture(autouse=True)
def sandbox(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> tuple[SearchTransport, OpenAITransport]:
    """Use real SDK clients with local HTTP services and CLI programs."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)
    search_transport = SearchTransport()
    openai_transport = OpenAITransport()

    def index_client(**kwargs: Any) -> SearchIndexClient:
        return SearchIndexClient(transport=search_transport, retry_total=0, **kwargs)

    def search_client(**kwargs: Any) -> SearchClient:
        return SearchClient(transport=search_transport, retry_total=0, **kwargs)

    def openai_client(**kwargs: Any) -> AzureOpenAI:
        return AzureOpenAI(
            http_client=httpx2.Client(transport=openai_transport), max_retries=0, **kwargs
        )

    monkeypatch.setattr(experiment, "SearchIndexClient", index_client)
    monkeypatch.setattr(experiment, "SearchClient", search_client)
    monkeypatch.setattr(experiment, "AzureOpenAI", openai_client)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))
    return search_transport, openai_transport


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
    assert "Score: 0.8950" in out
    assert all(not blobs for blobs in offline.transport.containers.values())


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
    sandbox: tuple[SearchTransport, OpenAITransport], capsys: pytest.CaptureFixture[str]
) -> None:
    """Detect when model response fails to produce expected grounded citations."""
    sandbox[1].answer = "This answer contains no citations whatsoever."
    assert experiment.main() == 1
    err = capsys.readouterr().err
    assert "Expected citation [Source: doc-cost-control] was not found" in err


def test_azure_error_handling(
    sandbox: tuple[SearchTransport, OpenAITransport], capsys: pytest.CaptureFixture[str]
) -> None:
    """Handle AzureError during search or storage operations."""
    sandbox[0].failures["upload"] = 403
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


@pytest.mark.parametrize(
    "stage", ["raw", "embeddings", "create", "upload", "count", "search", "chat", "citation"]
)
def test_failure_cleanup(
    stage: str, offline: OfflineAzure, sandbox: tuple[SearchTransport, OpenAITransport]
) -> None:
    """Remove all attempted artifacts after failures at each pipeline stage."""
    search, openai = sandbox
    if stage == "raw":
        offline.transport.upload_failure_after = 1
    elif stage in {"embeddings", "chat"}:
        openai.failures[stage] = 403
    elif stage == "citation":
        openai.answer = "No citation."
    else:
        search.failures[stage] = 403

    assert experiment.main() == 1
    assert all(not blobs for blobs in offline.transport.containers.values())
    assert not search.indexes
    assert offline.transport.closed
    assert offline.credential.closed
    assert search.closed
    assert openai.closed


@pytest.mark.parametrize("artifact", ["blob", "index"])
def test_cleanup_failure(
    artifact: str,
    offline: OfflineAzure,
    sandbox: tuple[SearchTransport, OpenAITransport],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Attempt all deletions and return failure when an artifact remains."""
    if artifact == "blob":
        offline.transport.failures["delete_blob"] = 403
    else:
        sandbox[0].failures["delete"] = 403
    assert experiment.main() == 1
    output = capsys.readouterr()
    assert "Cleanup failed" in output.err
    assert "RAG pipeline execution succeeded" not in output.out
    assert "Cleaned up all temporary" not in output.out
    assert len([r for r in offline.transport.requests if r.method == "DELETE"]) == 3
    assert any(r.method == "DELETE" for r in sandbox[0].requests)


def test_partial_index_upload(
    sandbox: tuple[SearchTransport, OpenAITransport], capsys: pytest.CaptureFixture[str]
) -> None:
    """Treat per-document failures inside a successful HTTP response as failure."""
    sandbox[0].reject_document = True
    assert experiment.main() == 1
    assert "Search rejected one or more document uploads" in capsys.readouterr().err
    assert not sandbox[0].indexes


def test_indexing_timeout(
    sandbox: tuple[SearchTransport, OpenAITransport], offline: OfflineAzure
) -> None:
    """Raise when the indexing deadline expires instead of proceeding to retrieval."""
    with SearchClient(
        endpoint=TARGET.search_service_endpoint,
        index_name="empty",
        credential=offline.credential,
        transport=sandbox[0],
    ) as client:
        with pytest.raises(TimeoutError, match="indexing timed out"):
            experiment.wait_for_indexing(client, expected_count=1, timeout_seconds=0)


@pytest.mark.parametrize("failure", [401, 429, 500, httpx2.ConnectError("private-response")])
def test_openai_failure(
    failure: int | Exception,
    sandbox: tuple[SearchTransport, OpenAITransport],
    offline: OfflineAzure,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Handle actual OpenAI SDK exceptions without exposing response details."""
    sandbox[1].failures["embeddings"] = failure
    assert experiment.main() == 1
    output = capsys.readouterr()
    assert "Azure AI Search, OpenAI, or storage access failed" in output.err
    assert "private-response" not in output.out + output.err
    assert all(not blobs for blobs in offline.transport.containers.values())


def test_existing_index_preserved(sandbox: tuple[SearchTransport, OpenAITransport]) -> None:
    """Create and delete a unique run index without changing an existing index."""
    sandbox[0].indexes[TARGET.search_index_name] = {"name": TARGET.search_index_name}
    assert experiment.main() == 0
    assert sandbox[0].indexes == {TARGET.search_index_name: {"name": TARGET.search_index_name}}
