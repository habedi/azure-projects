"""Process documents with Document Intelligence, validate schemas, and persist lakehouse JSON."""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeDocumentRequest,
    AnalyzeResult,
    DocumentField,
)
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from pydantic import BaseModel, Field, ValidationError

INFRA_DIR = Path(__file__).resolve().parent / "infra"


class InvoiceItem(BaseModel):
    """Line item extracted from an invoice document."""

    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    total_price: float = 0.0


class ProcessedInvoice(BaseModel):
    """Structured and validated invoice record."""

    invoice_id: str
    vendor_name: str
    customer_name: str
    invoice_date: str
    subtotal: float = 0.0
    total_tax: float = 0.0
    total_amount: float
    items: list[InvoiceItem] = Field(default_factory=list)
    average_confidence: float = Field(ge=0.0, le=1.0)
    source_blob: str
    processed_at: str


# Minimal valid PDF payload templates for sample invoices
def create_sample_pdf(invoice_text: str) -> bytes:
    """Generate a minimal valid PDF byte sequence containing text."""
    stream_content = f"BT /F1 12 Tf 50 700 Td ({invoice_text}) Tj ET".encode("latin1")
    stream_len = len(stream_content)
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Resources<<>>/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length "
        + str(stream_len).encode("ascii")
        + b">>stream\n"
        + stream_content
        + b"\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000052 00000 n \n"
        b"0000000101 00000 n \n"
        b"0000000192 00000 n \n"
        b"trailer<</Size 5/Root 1 0 R>>\n"
        b"startxref\n293\n%%EOF\n"
    )
    return pdf


SAMPLE_INVOICES: list[dict[str, Any]] = [
    {
        "filename": "invoice-contoso-1001.pdf",
        "data": create_sample_pdf("INVOICE INV-2026-001 Contoso Cloud Solutions Total: $1250.00"),
        "invoice_id": "INV-2026-001",
        "vendor": "Contoso Cloud Solutions",
        "customer": "Fabrikam Industries",
        "date": "2026-11-06",
        "subtotal": 1250.0,
        "tax": 0.0,
        "total": 1250.0,
        "items": [
            {
                "description": "Cloud Architecture Consulting",
                "quantity": 10.0,
                "unit_price": 125.0,
                "total_price": 1250.0,
            }
        ],
    },
    {
        "filename": "invoice-northwind-1002.pdf",
        "data": create_sample_pdf("INVOICE INV-2026-002 Northwind Traders Total: $420.00"),
        "invoice_id": "INV-2026-002",
        "vendor": "Northwind Traders",
        "customer": "Woodgrove Bank",
        "date": "2026-11-06",
        "subtotal": 400.0,
        "tax": 20.0,
        "total": 420.0,
        "items": [
            {
                "description": "Office Hardware Supply",
                "quantity": 2.0,
                "unit_price": 200.0,
                "total_price": 400.0,
            }
        ],
    },
]


@dataclass(frozen=True)
class Target:
    """Identify the sandbox resources recorded in OpenTofu state."""

    subscription_id: str
    resource_group_name: str
    location: str
    storage_account_url: str
    drop_container_name: str
    processed_container_name: str
    document_intelligence_endpoint: str
    document_intelligence_model_id: str


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
                "storage_account_url",
                "drop_container_name",
                "processed_container_name",
                "document_intelligence_endpoint",
                "document_intelligence_model_id",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("All OpenTofu outputs must be non-empty strings.")
        target = Target(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-06-[a-z0-9-]+", target.resource_group_name):
            raise ValueError("Invalid resource_group_name in target state.")
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError("Invalid location in target state.")
        if not re.fullmatch(
            r"https://azpg06[a-z0-9]+\.blob\.core\.windows\.net/?", target.storage_account_url
        ):
            raise ValueError("Invalid storage_account_url in target state.")
        if not re.fullmatch(r"[a-z0-9-]+", target.drop_container_name):
            raise ValueError("Invalid drop_container_name in target state.")
        if not re.fullmatch(r"[a-z0-9-]+", target.processed_container_name):
            raise ValueError("Invalid processed_container_name in target state.")
        if not re.fullmatch(
            r"https://azpg06[a-z0-9-]+\.cognitiveservices\.azure\.com/?",
            target.document_intelligence_endpoint,
        ):
            raise ValueError("Invalid document_intelligence_endpoint in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", target.document_intelligence_model_id):
            raise ValueError("Invalid document_intelligence_model_id in target state.")
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError("Invalid or missing OpenTofu outputs. Run make up first.") from err

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


def upload_incoming_documents(
    blob_service: BlobServiceClient,
    container_name: str,
    batch_id: str,
    documents: list[dict[str, Any]],
    uploaded_blobs: list[str],
) -> list[str]:
    """Upload raw invoice PDFs to the drop zone container."""
    container_client = blob_service.get_container_client(container_name)
    try:
        container_client.create_container()
    except AzureError as err:
        status = getattr(err, "status_code", None)
        if status not in (403, 409) and "ContainerAlreadyExists" not in str(err):
            raise

    drop_paths: list[str] = []
    for doc in documents:
        blob_name = f"incoming/{batch_id}/{doc['filename']}"
        data = doc["data"]
        container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        uploaded_blobs.append(blob_name)
        drop_paths.append(blob_name)
    return drop_paths


def extract_field_string(field: DocumentField | None, default: str = "") -> str:
    """Extract string value from a DocumentField safely."""
    if not field:
        return default
    if hasattr(field, "value_string") and field.value_string:
        return str(field.value_string)
    if hasattr(field, "content") and field.content:
        return str(field.content)
    return default


def extract_field_number(field: DocumentField | None, default: float = 0.0) -> float:
    """Extract numeric value from currency or number DocumentField."""
    if not field:
        return default
    if hasattr(field, "value_currency") and field.value_currency:
        return float(getattr(field.value_currency, "amount", 0.0))
    if hasattr(field, "value_number") and field.value_number is not None:
        return float(field.value_number)
    if hasattr(field, "content") and field.content:
        cleaned = re.sub(r"[^\d.]", "", str(field.content))
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def extract_and_validate_invoice(
    result: AnalyzeResult,
    source_blob: str,
    min_confidence: float = 0.70,
) -> ProcessedInvoice:
    """Extract key-value pairs from analysis result and validate against Pydantic schema."""
    if not result.documents:
        raise ValueError(f"No documents were detected in analysis for {source_blob}.")

    doc = result.documents[0]
    fields = getattr(doc, "fields", {}) or {}

    confidences: list[float] = []
    if hasattr(doc, "confidence") and doc.confidence is not None:
        confidences.append(float(doc.confidence))

    invoice_id_field = fields.get("InvoiceId")
    if invoice_id_field and getattr(invoice_id_field, "confidence", None):
        confidences.append(float(invoice_id_field.confidence))
    invoice_id = extract_field_string(invoice_id_field, default=f"INV-{uuid4().hex[:6].upper()}")

    vendor_field = fields.get("VendorName")
    if vendor_field and getattr(vendor_field, "confidence", None):
        confidences.append(float(vendor_field.confidence))
    vendor_name = extract_field_string(vendor_field, default="Unknown Vendor")

    customer_field = fields.get("CustomerName")
    if customer_field and getattr(customer_field, "confidence", None):
        confidences.append(float(customer_field.confidence))
    customer_name = extract_field_string(customer_field, default="Unknown Customer")

    date_field = fields.get("InvoiceDate")
    if date_field and getattr(date_field, "confidence", None):
        confidences.append(float(date_field.confidence))
    invoice_date = extract_field_string(date_field, default=datetime.now(UTC).strftime("%Y-%m-%d"))

    subtotal = extract_field_number(fields.get("SubTotal"))
    total_tax = extract_field_number(fields.get("TotalTax"))
    total_amount = extract_field_number(fields.get("InvoiceTotal"))
    if total_amount == 0.0 and subtotal > 0.0:
        total_amount = subtotal + total_tax

    items: list[InvoiceItem] = []
    items_field = fields.get("Items")
    if items_field and hasattr(items_field, "value_array") and items_field.value_array:
        for item_entry in items_field.value_array:
            item_obj = getattr(item_entry, "value_object", {}) or {}
            desc = extract_field_string(item_obj.get("Description"), default="General Item")
            qty = extract_field_number(item_obj.get("Quantity"), default=1.0)
            unit_price = extract_field_number(item_obj.get("UnitPrice"), default=0.0)
            amount = extract_field_number(item_obj.get("Amount"), default=qty * unit_price)
            items.append(
                InvoiceItem(
                    description=desc,
                    quantity=qty,
                    unit_price=unit_price,
                    total_price=amount,
                )
            )

    avg_conf = sum(confidences) / len(confidences) if confidences else 1.0
    if avg_conf < min_confidence:
        raise ValueError(
            f"Document confidence {avg_conf:.2f} is below minimum threshold {min_confidence:.2f}."
        )

    return ProcessedInvoice(
        invoice_id=invoice_id,
        vendor_name=vendor_name,
        customer_name=customer_name,
        invoice_date=invoice_date,
        subtotal=subtotal,
        total_tax=total_tax,
        total_amount=total_amount,
        items=items,
        average_confidence=round(avg_conf, 4),
        source_blob=source_blob,
        processed_at=datetime.now(UTC).isoformat(),
    )


def persist_processed_records(
    blob_service: BlobServiceClient,
    container_name: str,
    batch_id: str,
    invoices: list[ProcessedInvoice],
    uploaded_blobs: list[str],
) -> list[str]:
    """Store validated structured JSON records into the lakehouse folder."""
    container_client = blob_service.get_container_client(container_name)
    try:
        container_client.create_container()
    except AzureError as err:
        status = getattr(err, "status_code", None)
        if status not in (403, 409) and "ContainerAlreadyExists" not in str(err):
            raise

    saved_paths: list[str] = []
    for invoice in invoices:
        blob_name = f"invoices/{batch_id}/{invoice.invoice_id}.json"
        data = invoice.model_dump_json(indent=2).encode("utf-8")
        container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        uploaded_blobs.append(blob_name)
        saved_paths.append(blob_name)
    return saved_paths


def verify_processed_records(
    blob_service: BlobServiceClient,
    container_name: str,
    saved_blobs: list[str],
    expected_count: int,
) -> None:
    """Verify persisted JSON files in the processed lakehouse folder."""
    if len(saved_blobs) != expected_count:
        raise ValueError(
            f"Expected {expected_count} processed files, but found {len(saved_blobs)}."
        )

    container_client = blob_service.get_container_client(container_name)
    for blob_name in saved_blobs:
        data = container_client.download_blob(blob_name).readall()
        if not data:
            raise ValueError(f"Processed file {blob_name} was empty.")
        parsed = json.loads(data.decode("utf-8"))
        if "invoice_id" not in parsed or "total_amount" not in parsed:
            raise ValueError(f"File {blob_name} is missing essential invoice fields.")


def run_pipeline(target: Target) -> None:
    """Execute document upload, AI analysis, Pydantic validation, and cleanup."""
    batch_id = f"azpg-06-{uuid4().hex}"
    print(f"Document processing execution batch: {batch_id}")

    drop_uploaded_blobs: list[str] = []
    processed_uploaded_blobs: list[str] = []

    with (
        DefaultAzureCredential() as credential,
        DocumentIntelligenceClient(
            endpoint=target.document_intelligence_endpoint, credential=credential
        ) as doc_client,
        BlobServiceClient(
            account_url=target.storage_account_url, credential=credential
        ) as blob_service,
    ):
        try:
            print("1. Uploading raw PDF documents to Blob Storage drop zone...")
            drop_paths = upload_incoming_documents(
                blob_service,
                target.drop_container_name,
                batch_id,
                SAMPLE_INVOICES,
                drop_uploaded_blobs,
            )
            drop_msg = f"   Uploaded {len(drop_paths)} files to '{target.drop_container_name}'."
            print(drop_msg)

            print("2. Submitting documents to Azure AI Document Intelligence...")
            drop_container = blob_service.get_container_client(target.drop_container_name)
            processed_invoices: list[ProcessedInvoice] = []

            for blob_name, _sample in zip(drop_paths, SAMPLE_INVOICES, strict=True):
                doc_bytes = drop_container.download_blob(blob_name).readall()
                poller = doc_client.begin_analyze_document(
                    model_id=target.document_intelligence_model_id,
                    body=AnalyzeDocumentRequest(bytes_source=doc_bytes),
                )
                analysis_result: AnalyzeResult = poller.result()

                print("3. Validating extracted fields against Pydantic schema...")
                validated_invoice = extract_and_validate_invoice(
                    analysis_result, source_blob=blob_name
                )
                processed_invoices.append(validated_invoice)
                print(
                    f"   - Verified Invoice {validated_invoice.invoice_id}: "
                    f"Vendor='{validated_invoice.vendor_name}', "
                    f"Total=${validated_invoice.total_amount:.2f}, "
                    f"Confidence={validated_invoice.average_confidence:.2f}"
                )

            print("4. Persisting validated JSON records into lakehouse folder...")
            saved_paths = persist_processed_records(
                blob_service,
                target.processed_container_name,
                batch_id,
                processed_invoices,
                processed_uploaded_blobs,
            )
            saved_msg = (
                f"   Stored {len(saved_paths)} structured files in "
                f"'{target.processed_container_name}'."
            )
            print(saved_msg)

            print("5. Verifying structured records and schema conformity...")
            verify_processed_records(
                blob_service,
                target.processed_container_name,
                saved_paths,
                expected_count=len(SAMPLE_INVOICES),
            )
            print("   Verification successful: lakehouse records match validated invoices.")

        finally:
            print("6. Cleaning up temporary document processing artifacts...")
            failed: list[str] = []

            drop_container = blob_service.get_container_client(target.drop_container_name)
            for blob_name in drop_uploaded_blobs:
                try:
                    drop_container.delete_blob(blob_name)
                except ResourceNotFoundError:
                    pass
                except AzureError:
                    failed.append(f"drop:{blob_name}")

            processed_container = blob_service.get_container_client(target.processed_container_name)
            for blob_name in processed_uploaded_blobs:
                try:
                    processed_container.delete_blob(blob_name)
                except ResourceNotFoundError:
                    pass
                except AzureError:
                    failed.append(f"processed:{blob_name}")

            if failed:
                raise AzureError(f"Cleanup failed for artifacts: {failed}")
            print("   Cleaned up all temporary document artifacts.")

    print("Document processing pipeline execution succeeded.")


def main() -> int:
    """Confirm the verified target and run the document processing pipeline."""
    try:
        target = load_target()
        verify_target(target)
        print(f"Subscription: {target.subscription_id}")
        print(f"Resource group: {target.resource_group_name}")
        print(f"Region: {target.location}")
        print(f"Storage account: {target.storage_account_url}")
        print(f"Drop container: {target.drop_container_name}")
        print(f"Processed container: {target.processed_container_name}")
        print(f"Document Intelligence endpoint: {target.document_intelligence_endpoint}")
        print(f"Analysis model: {target.document_intelligence_model_id}")
        print(
            "This runs the Multimodal Document Processing pipeline in Azure AI Document "
            "Intelligence and Azure Blob Storage. Document Intelligence per-page charges may apply."
        )
        confirmation = input("Type the resource group name to continue: ")
        if confirmation != target.resource_group_name:
            print("Cancelled. No documents or storage data were changed.")
            return 1
        run_pipeline(target)
    except (ValueError, TimeoutError, ValidationError) as error:
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
            "Azure AI Document Intelligence or storage access failed. "
            "Check DefaultAzureCredential, role assignments, and network rules. "
            "Run make down when finished.",
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
