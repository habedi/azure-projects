"""Test Document Intelligence processing pipeline without credentials or cloud resources."""

import importlib
import json
from dataclasses import asdict
from datetime import date
from io import StringIO
from typing import Any

import pytest
from azure.ai.documentintelligence.models import (
    AnalyzedDocument,
    AnalyzeResult,
    CurrencyValue,
    DocumentField,
    DocumentSpan,
)
from azure.core.exceptions import AzureError

from tests.support import OfflineAzure

experiment = importlib.import_module("projects.06-document-processing.run")

TARGET = experiment.Target(
    subscription_id="12345678-1234-1234-1234-123456789abc",
    resource_group_name="rg-azpg-06-test",
    location="swedencentral",
    storage_account_url="https://azpg06teststore.blob.core.windows.net/",
    drop_container_name="documents",
    processed_container_name="processed",
    document_intelligence_endpoint="https://azpg06test-docintel.cognitiveservices.azure.com/",
    document_intelligence_model_id="prebuilt-invoice",
)


def build_mock_invoice_result(
    invoice_id: str,
    vendor: str,
    customer: str,
    subtotal: float,
    tax: float,
    total: float,
    items: list[dict[str, Any]],
    confidence: float = 0.98,
) -> AnalyzeResult:
    """Build a realistic AnalyzeResult for offline testing."""
    item_fields = [
        DocumentField(
            type="object",
            value_object={
                "Description": DocumentField(
                    type="string", value_string=it["description"], confidence=confidence
                ),
                "Quantity": DocumentField(
                    type="number", value_number=it["quantity"], confidence=confidence
                ),
                "UnitPrice": DocumentField(
                    type="currency",
                    value_currency=CurrencyValue(amount=it["unit_price"], currency_symbol="$"),
                    confidence=confidence,
                ),
                "Amount": DocumentField(
                    type="currency",
                    value_currency=CurrencyValue(amount=it["total_price"], currency_symbol="$"),
                    confidence=confidence,
                ),
            },
            confidence=confidence,
        )
        for it in items
    ]

    doc = AnalyzedDocument(
        doc_type="invoice",
        spans=[DocumentSpan(offset=0, length=100)],
        confidence=confidence,
        fields={
            "InvoiceId": DocumentField(
                type="string", value_string=invoice_id, confidence=confidence
            ),
            "VendorName": DocumentField(type="string", value_string=vendor, confidence=confidence),
            "CustomerName": DocumentField(
                type="string", value_string=customer, confidence=confidence
            ),
            "InvoiceDate": DocumentField(
                type="date", value_date=date(2026, 11, 6), confidence=confidence
            ),
            "SubTotal": DocumentField(
                type="currency",
                value_currency=CurrencyValue(amount=subtotal, currency_symbol="$"),
                confidence=confidence,
            ),
            "TotalTax": DocumentField(
                type="currency",
                value_currency=CurrencyValue(amount=tax, currency_symbol="$"),
                confidence=confidence,
            ),
            "InvoiceTotal": DocumentField(
                type="currency",
                value_currency=CurrencyValue(amount=total, currency_symbol="$"),
                confidence=confidence,
            ),
            "Items": DocumentField(type="array", value_array=item_fields, confidence=confidence),
        },
    )
    return AnalyzeResult(
        api_version="2024-02-29-preview",
        model_id="prebuilt-invoice",
        string_index_type="textElements",
        content="invoice content",
        pages=[],
        documents=[doc],
    )


class MockPoller:
    """Simulate asynchronous Azure LRO poller."""

    def __init__(self, result: AnalyzeResult | None, error: Exception | None = None) -> None:
        """Initialize with predefined result or failure."""
        self._result = result
        self._error = error

    def result(self) -> AnalyzeResult:
        """Return the result or raise error."""
        if self._error:
            raise self._error
        assert self._result is not None
        return self._result


class LocalDocumentIntelligenceClient:
    """In-memory DocumentIntelligenceClient double."""

    def __init__(self) -> None:
        """Initialize client state and result map."""
        self.closed = False
        self.call_error: Exception | None = None
        self.results: dict[bytes, AnalyzeResult] = {}

    def __enter__(self) -> LocalDocumentIntelligenceClient:
        """Enter client context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit client context."""
        self.closed = True

    def close(self) -> None:
        """Close client."""
        self.closed = True

    def begin_analyze_document(self, model_id: str, body: Any, **kwargs: Any) -> MockPoller:
        """Return poller with mock analysis result."""
        if self.call_error:
            return MockPoller(result=None, error=self.call_error)

        doc_bytes = getattr(body, "bytes_source", b"")
        result = self.results.get(doc_bytes)
        if not result and self.results:
            result = next(iter(self.results.values()))
        return MockPoller(result=result)


@pytest.fixture(autouse=True)
def sandbox(
    offline: OfflineAzure, monkeypatch: pytest.MonkeyPatch
) -> LocalDocumentIntelligenceClient:
    """Configure offline test doubles for Document Intelligence and Storage."""
    outputs = {name: {"value": value} for name, value in asdict(TARGET).items()}
    metadata = {"location": TARGET.location, "endpoint": TARGET.storage_account_url}
    offline.cli.outputs.update(tofu=json.dumps(outputs), az=json.dumps(metadata))
    offline.cli.sync()
    offline.install(experiment)

    doc_client = LocalDocumentIntelligenceClient()
    for sample in experiment.SAMPLE_INVOICES:
        doc_client.results[sample["data"]] = build_mock_invoice_result(
            invoice_id=sample["invoice_id"],
            vendor=sample["vendor"],
            customer=sample["customer"],
            subtotal=sample["subtotal"],
            tax=sample["tax"],
            total=sample["total"],
            items=sample["items"],
        )

    monkeypatch.setattr(
        experiment, "DocumentIntelligenceClient", lambda *args, **kwargs: doc_client
    )
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("sys.stdin", StringIO(TARGET.resource_group_name + "\n"))

    return doc_client


def test_success(
    offline: OfflineAzure,
    capsys: pytest.CaptureFixture[str],
    sandbox: LocalDocumentIntelligenceClient,
) -> None:
    """Execute complete document upload, AI extraction, Pydantic validation, and cleanup."""
    assert experiment.main() == 0

    assert offline.cli.calls[0] == [
        "tofu",
        f"-chdir={experiment.INFRA_DIR}",
        "output",
        "-json",
    ]

    out = capsys.readouterr().out
    assert "Uploaded 2 files to 'documents'" in out
    assert "Submitting documents to Azure AI Document Intelligence..." in out
    assert "Validating extracted fields against Pydantic schema..." in out
    assert "Verified Invoice INV-2026-001" in out
    assert "Verified Invoice INV-2026-002" in out
    assert "Stored 2 structured files in 'processed'" in out
    assert "Verification successful: lakehouse records match validated invoices." in out
    assert "Cleaned up all temporary document artifacts." in out
    assert "Document processing pipeline execution succeeded." in out

    # Verify that test blobs were deleted
    assert all(not blobs for blobs in offline.transport.containers.values())
    assert sandbox.closed


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
        ("location", "Sweden Central"),
        ("storage_account_url", "http://insecure.blob.core.windows.net"),
        ("drop_container_name", "INVALID_DROP"),
        ("processed_container_name", "INVALID_PROCESSED"),
        ("document_intelligence_endpoint", "http://insecure.endpoint.azure.com"),
        ("document_intelligence_model_id", "invalid/model/id"),
    ],
)
def test_target_validation_errors(key: str, invalid_val: str, offline: OfflineAzure) -> None:
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


def test_docintel_api_failure(sandbox: LocalDocumentIntelligenceClient) -> None:
    """Handle Document Intelligence API failure during analysis."""
    sandbox.call_error = AzureError("Service unavailable")
    assert experiment.main() == 1


def test_low_confidence_failure(sandbox: LocalDocumentIntelligenceClient) -> None:
    """Reject analysis results when confidence falls below required threshold."""
    low_conf_result = build_mock_invoice_result(
        invoice_id="INV-LOW-001",
        vendor="Low Conf Vendor",
        customer="Low Conf Customer",
        subtotal=100.0,
        tax=0.0,
        total=100.0,
        items=[],
        confidence=0.45,
    )
    sandbox.results = {sample["data"]: low_conf_result for sample in experiment.SAMPLE_INVOICES}
    assert experiment.main() == 1


def test_empty_analysis_failure(sandbox: LocalDocumentIntelligenceClient) -> None:
    """Handle empty documents list in analysis result."""
    empty_result = AnalyzeResult(
        api_version="2024-02-29-preview",
        model_id="prebuilt-invoice",
        string_index_type="textElements",
        content="",
        pages=[],
        documents=[],
    )
    sandbox.results = {sample["data"]: empty_result for sample in experiment.SAMPLE_INVOICES}
    assert experiment.main() == 1


def test_existing_container(
    offline: OfflineAzure, sandbox: LocalDocumentIntelligenceClient
) -> None:
    """Handle pre-existing drop and processed blob containers without error."""
    offline.transport.containers[TARGET.drop_container_name] = {}
    offline.transport.containers[TARGET.processed_container_name] = {}
    assert experiment.main() == 0


def test_empty_blob_verification_failure(
    sandbox: LocalDocumentIntelligenceClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise error when verified processed file is empty or corrupted."""

    def mock_verify(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Processed file was empty.")

    monkeypatch.setattr(experiment, "verify_processed_records", mock_verify)
    assert experiment.main() == 1


def test_cleanup_failure(offline: OfflineAzure, sandbox: LocalDocumentIntelligenceClient) -> None:
    """Handle deletion error and report remaining artifacts."""
    offline.transport.failures["delete_blob"] = 403
    assert experiment.main() == 1


def test_extract_field_helpers() -> None:
    """Test string and numeric extraction helper edge cases."""
    assert experiment.extract_field_string(None, default="fallback") == "fallback"
    assert experiment.extract_field_number(None, default=42.0) == 42.0

    content_field = DocumentField(type="string", content="Just text")
    assert experiment.extract_field_string(content_field) == "Just text"

    number_content_field = DocumentField(type="number", content="$1,234.50")
    assert experiment.extract_field_number(number_content_field) == 1234.50

    invalid_number_field = DocumentField(type="number", content="Not a number")
    assert experiment.extract_field_number(invalid_number_field, default=9.9) == 9.9
