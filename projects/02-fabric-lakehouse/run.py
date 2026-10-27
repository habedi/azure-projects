"""Ingest, clean, aggregate, and verify data in a Microsoft Fabric Lakehouse."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

INFRA_DIR = Path(__file__).resolve().parent / "infra"

RAW_EVENTS: list[dict[str, Any]] = [
    {
        "transaction_id": "tx-1001",
        "timestamp": "2026-10-27T10:00:00Z",
        "customer_id": "cust-01",
        "amount": 120.50,
        "status": "completed",
        "category": "electronics",
    },
    {
        "transaction_id": "tx-1002",
        "timestamp": "2026-10-27T10:05:00Z",
        "customer_id": "cust-02",
        "amount": 45.00,
        "status": "completed",
        "category": "books",
    },
    {
        "transaction_id": "tx-1003",
        "timestamp": "2026-10-27T10:10:00Z",
        "customer_id": "cust-03",
        "amount": 89.99,
        "status": "refunded",
        "category": "clothing",
    },
    {
        # Duplicate record to be cleaned during Silver transformation
        "transaction_id": "tx-1001",
        "timestamp": "2026-10-27T10:00:00Z",
        "customer_id": "cust-01",
        "amount": 120.50,
        "status": "completed",
        "category": "electronics",
    },
    {
        # Corrupted record with invalid timestamp, status, and negative amount
        "transaction_id": "tx-corrupt",
        "timestamp": "invalid-iso-date",
        "customer_id": None,
        "amount": -10.00,
        "status": "unknown",
        "category": "invalid",
    },
    {
        "transaction_id": "tx-1004",
        "timestamp": "2026-10-27T10:15:00Z",
        "customer_id": "cust-04",
        "amount": 210.00,
        "status": "completed",
        "category": "electronics",
    },
]


@dataclass(frozen=True)
class Target:
    """Identify the Microsoft Fabric resources recorded in local state."""

    subscription_id: str
    resource_group_name: str
    location: str
    fabric_capacity_name: str
    workspace_name: str
    lakehouse_name: str
    onelake_endpoint: str


def load_target() -> Target:
    """Read and validate OpenTofu outputs without loading root environment variables."""
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
                "fabric_capacity_name",
                "workspace_name",
                "lakehouse_name",
                "onelake_endpoint",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError
        target = Target(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-02-[a-z0-9-]+", target.resource_group_name):
            raise ValueError
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError
        if not re.fullmatch(r"azpg02[a-z0-9]+", target.fabric_capacity_name):
            raise ValueError
        if not re.fullmatch(r"ws-azpg-02-[a-z0-9-]+", target.workspace_name):
            raise ValueError
        if not re.fullmatch(r"[a-zA-Z0-9_]+", target.lakehouse_name):
            raise ValueError
        parsed_url = urlsplit(target.onelake_endpoint)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError
    except ValueError, KeyError, TypeError:
        raise ValueError("Invalid project outputs. Review local state and run make up.") from None
    return target


def verify_target(target: Target) -> None:
    """Verify Fabric capacity scope, region, and operational state via Azure CLI."""
    result = subprocess.run(
        [
            "az",
            "fabric",
            "capacity",
            "show",
            "--subscription",
            target.subscription_id,
            "--resource-group",
            target.resource_group_name,
            "--capacity-name",
            target.fabric_capacity_name,
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
        or metadata.get("location", "").lower().replace(" ", "")
        != target.location.lower().replace(" ", "")
        or metadata.get("name") != target.fabric_capacity_name
    ):
        raise ValueError("Azure Fabric capacity scope or region differs from project state.")
    if metadata.get("state") == "Suspended":
        raise ValueError(
            f"Fabric capacity {target.fabric_capacity_name} is suspended. "
            "Run make resume before executing the pipeline."
        )


def transform_bronze_to_silver(
    raw_lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Validate, deduplicate, and clean raw events for the Silver layer."""
    cleaned_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    metrics = {"total_raw": len(raw_lines), "valid": 0, "corrupted": 0, "duplicates": 0}

    valid_statuses = {"completed", "refunded", "cancelled"}

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                metrics["corrupted"] += 1
                continue

            tx_id = record.get("transaction_id")
            timestamp = record.get("timestamp")
            customer_id = record.get("customer_id")
            amount = record.get("amount")
            status = record.get("status")
            category = record.get("category")

            if (
                not isinstance(tx_id, str)
                or not tx_id.startswith("tx-")
                or not isinstance(timestamp, str)
                or not isinstance(customer_id, str)
                or not isinstance(amount, (int, float))
                or amount <= 0
                or status not in valid_statuses
                or not isinstance(category, str)
            ):
                metrics["corrupted"] += 1
                continue

            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

            if tx_id in seen_ids:
                metrics["duplicates"] += 1
                continue

            seen_ids.add(tx_id)
            metrics["valid"] += 1
            cleaned_records.append(
                {
                    "transaction_id": tx_id,
                    "timestamp": timestamp,
                    "customer_id": customer_id,
                    "amount": round(float(amount), 2),
                    "status": status,
                    "category": category,
                }
            )
        except ValueError, KeyError, TypeError, json.JSONDecodeError:
            metrics["corrupted"] += 1

    return cleaned_records, metrics


def aggregate_silver_to_gold(silver_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate cleaned records into business metrics for the Gold layer."""
    total_transactions = len(silver_records)
    completed_amounts: list[float] = []
    category_revenue: defaultdict[str, float] = defaultdict(float)
    status_counts: Counter[str] = Counter()

    for item in silver_records:
        status = item["status"]
        status_counts[status] += 1
        if status == "completed":
            amount = item["amount"]
            completed_amounts.append(amount)
            category_revenue[item["category"]] += amount

    total_revenue = round(sum(completed_amounts), 2)
    average_amount = round(total_revenue / len(completed_amounts), 2) if completed_amounts else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_transactions": total_transactions,
        "completed_count": status_counts.get("completed", 0),
        "refunded_count": status_counts.get("refunded", 0),
        "cancelled_count": status_counts.get("cancelled", 0),
        "total_revenue": total_revenue,
        "average_transaction_amount": average_amount,
        "revenue_by_category": {cat: round(rev, 2) for cat, rev in category_revenue.items()},
    }


def run_pipeline(target: Target) -> None:
    """Execute the Bronze-Silver-Gold pipeline and clean up temporary Lakehouse files."""
    batch_id = f"azpg-02-{uuid4().hex}"
    print(f"Pipeline execution batch: {batch_id}")

    bronze_blob = f"{target.lakehouse_name}.Lakehouse/Files/bronze/{batch_id}/transactions.jsonl"
    silver_blob = f"{target.lakehouse_name}.Lakehouse/Files/silver/{batch_id}/transactions.json"
    gold_blob = f"{target.lakehouse_name}.Lakehouse/Files/gold/{batch_id}/summary.json"

    raw_payload = "\n".join(json.dumps(event) for event in RAW_EVENTS).encode("utf-8")

    with DefaultAzureCredential() as credential:
        with BlobServiceClient(
            account_url=target.onelake_endpoint,
            credential=credential,
            connection_timeout=10,
            read_timeout=30,
            retry_total=0,
        ) as service:
            container = service.get_container_client(target.workspace_name)
            try:
                container.create_container()
            except AzureError as err:
                if getattr(err, "status_code", None) != 409 and "ContainerAlreadyExists" not in str(
                    err
                ):
                    raise

            try:
                # 1. Bronze stage: Ingest raw data
                print("1. Ingesting raw JSONL records to Bronze layer...")
                container.upload_blob(name=bronze_blob, data=raw_payload, overwrite=False)

                # 2. Silver stage: Read Bronze, clean, deduplicate, validate, write Silver
                print("2. Reading Bronze and transforming to Silver layer...")
                bronze_bytes = container.download_blob(bronze_blob).readall()
                bronze_lines = bronze_bytes.decode("utf-8").splitlines()
                silver_records, metrics = transform_bronze_to_silver(bronze_lines)
                print(
                    f"   Processed {metrics['total_raw']} raw records: "
                    f"{metrics['valid']} valid, "
                    f"{metrics['corrupted']} corrupted filtered, "
                    f"{metrics['duplicates']} duplicate filtered."
                )
                silver_payload = json.dumps(silver_records, indent=2).encode("utf-8")
                container.upload_blob(name=silver_blob, data=silver_payload, overwrite=False)

                # 3. Gold stage: Read Silver, aggregate business metrics, write Gold
                print("3. Reading Silver and computing Gold business aggregations...")
                silver_data_bytes = container.download_blob(silver_blob).readall()
                loaded_silver = json.loads(silver_data_bytes.decode("utf-8"))
                if not isinstance(loaded_silver, list):
                    raise ValueError("Invalid Silver records format in Lakehouse.")
                gold_summary = aggregate_silver_to_gold(loaded_silver)
                gold_payload = json.dumps(gold_summary, indent=2).encode("utf-8")
                container.upload_blob(name=gold_blob, data=gold_payload, overwrite=False)

                # 4. Verification stage: Assert metrics reconciliation
                print("4. Reconciling Gold metrics...")
                gold_downloaded_bytes = container.download_blob(gold_blob).readall()
                reconciled = json.loads(gold_downloaded_bytes.decode("utf-8"))
                if reconciled["total_transactions"] != 4:
                    raise ValueError(
                        f"Expected 4 valid transactions, found {reconciled['total_transactions']}."
                    )
                if reconciled["total_revenue"] != 375.50:
                    raise ValueError(
                        f"Expected total revenue 375.50, found {reconciled['total_revenue']}."
                    )
                if reconciled["completed_count"] != 3 or reconciled["refunded_count"] != 1:
                    raise ValueError("Status distribution mismatch in Gold layer.")

                print("   Reconciliation successful: all metrics match expected values.")
                print(f"   Total valid transactions: {reconciled['total_transactions']}")
                print(f"   Total revenue: ${reconciled['total_revenue']}")
                print(f"   Revenue by category: {reconciled['revenue_by_category']}")

            finally:
                # 5. Cleanup stage: Delete test batch blobs
                print("5. Cleaning up pipeline batch artifacts...")
                for blob_name in (gold_blob, silver_blob, bronze_blob):
                    try:
                        container.delete_blob(blob_name)
                    except AzureError:
                        print(f"   Warning: Cleanup failed for {blob_name}.", file=sys.stderr)
                print("Pipeline batch artifacts removed.")


def main() -> int:
    """Confirm the verified target and run the Medallion pipeline."""
    try:
        target = load_target()
        verify_target(target)
        print(f"Subscription: {target.subscription_id}")
        print(f"Resource group: {target.resource_group_name}")
        print(f"Region: {target.location}")
        print(f"Fabric capacity: {target.fabric_capacity_name}")
        print(f"Workspace: {target.workspace_name}")
        print(f"Lakehouse: {target.lakehouse_name}")
        print(f"OneLake endpoint: {target.onelake_endpoint}")
        print(
            "This runs the Bronze-Silver-Gold pipeline in Microsoft Fabric OneLake. "
            "Charges may apply to the Fabric capacity."
        )
        confirmation = input("Type the resource group name to continue: ")
        if confirmation != target.resource_group_name:
            print("Cancelled. No Fabric data was changed.")
            return 1
        run_pipeline(target)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        print(
            "OpenTofu or Azure CLI failed. Check tool installation, make up, az login, "
            "and Microsoft.Fabric capacity state.",
            file=sys.stderr,
        )
        return 1
    except AzureError:
        print(
            "OneLake storage access failed. Check DefaultAzureCredential, Fabric permissions, "
            "and network rules. Run make down when finished.",
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
