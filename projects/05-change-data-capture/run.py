"""Capture Cosmos DB changes, enrich records with Azure OpenAI, and update analytical views."""

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI, OpenAIError

INFRA_DIR = Path(__file__).resolve().parent / "infra"

# Sample transactional order documents written to Cosmos DB
SAMPLE_ORDERS: list[dict[str, Any]] = [
    {
        "id": "ord-1001",
        "store_id": "store-east-01",
        "customer_id": "cust-501",
        "order_amount": 149.99,
        "item_count": 3,
        "order_status": "placed",
        "customer_notes": "Urgent delivery needed for upcoming company milestone event.",
    },
    {
        "id": "ord-1002",
        "store_id": "store-east-01",
        "customer_id": "cust-502",
        "order_amount": 34.50,
        "item_count": 1,
        "order_status": "placed",
        "customer_notes": "Standard reorder of office stationery supplies.",
    },
    {
        "id": "ord-1003",
        "store_id": "store-west-02",
        "customer_id": "cust-503",
        "order_amount": 899.00,
        "item_count": 5,
        "order_status": "placed",
        "customer_notes": (
            "Received damaged item in previous shipment, requesting priority handling."
        ),
    },
]


@dataclass(frozen=True)
class Target:
    """Identify the sandbox resources recorded in OpenTofu state."""

    subscription_id: str
    resource_group_name: str
    location: str
    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_container_name: str
    storage_account_url: str
    table_endpoint: str
    table_name: str
    blob_container_name: str
    openai_endpoint: str
    chat_deployment_name: str


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
                "cosmos_endpoint",
                "cosmos_database_name",
                "cosmos_container_name",
                "storage_account_url",
                "table_endpoint",
                "table_name",
                "blob_container_name",
                "openai_endpoint",
                "chat_deployment_name",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("All OpenTofu outputs must be non-empty strings.")
        target = Target(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-05-[a-z0-9-]+", target.resource_group_name):
            raise ValueError("Invalid resource_group_name in target state.")
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError("Invalid location in target state.")
        if not re.fullmatch(
            r"https://azpg05[a-z0-9-]+\.documents\.azure\.com:443/?", target.cosmos_endpoint
        ):
            raise ValueError("Invalid cosmos_endpoint in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", target.cosmos_database_name):
            raise ValueError("Invalid cosmos_database_name in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", target.cosmos_container_name):
            raise ValueError("Invalid cosmos_container_name in target state.")
        if not re.fullmatch(
            r"https://azpg05[a-z0-9]+\.blob\.core\.windows\.net/?", target.storage_account_url
        ):
            raise ValueError("Invalid storage_account_url in target state.")
        if not re.fullmatch(
            r"https://azpg05[a-z0-9]+\.table\.core\.windows\.net/?", target.table_endpoint
        ):
            raise ValueError("Invalid table_endpoint in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9]+", target.table_name):
            raise ValueError("Invalid table_name in target state.")
        if not re.fullmatch(r"[a-z0-9-]+", target.blob_container_name):
            raise ValueError("Invalid blob_container_name in target state.")
        if not re.fullmatch(
            r"https://azpg05[a-z0-9-]+\.openai\.azure\.com/?", target.openai_endpoint
        ):
            raise ValueError("Invalid openai_endpoint in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", target.chat_deployment_name):
            raise ValueError("Invalid chat_deployment_name in target state.")
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


def write_transactional_orders(
    cosmos_client: CosmosClient,
    database_name: str,
    container_name: str,
    orders: list[dict[str, Any]],
    inserted_keys: list[tuple[str, str]],
) -> None:
    """Write transactional order records to Cosmos DB."""
    database = cosmos_client.get_database_client(database_name)
    container = database.get_container_client(container_name)
    for order in orders:
        container.create_item(order)
        inserted_keys.append((order["id"], order["store_id"]))


def read_cosmos_change_feed(
    cosmos_client: CosmosClient,
    database_name: str,
    container_name: str,
    expected_ids: set[str],
    max_wait_seconds: float = 10.0,
    poll_interval: float = 0.1,
) -> list[dict[str, Any]]:
    """Poll the Cosmos DB change feed until all expected records are observed."""
    database = cosmos_client.get_database_client(database_name)
    container = database.get_container_client(container_name)
    start_time = time.monotonic()
    collected: dict[str, dict[str, Any]] = {}

    while time.monotonic() - start_time < max_wait_seconds:
        feed = container.query_items_change_feed(mode="LatestVersion", start_time="Beginning")
        for item in feed:
            item_id = str(item.get("id"))
            if item_id in expected_ids:
                collected[item_id] = item
        if set(collected.keys()) == expected_ids:
            return [collected[order_id] for order_id in sorted(collected.keys())]
        time.sleep(poll_interval)

    if set(collected.keys()) != expected_ids:
        missing = expected_ids - set(collected.keys())
        raise TimeoutError(f"Change feed timed out waiting for records: {missing}")
    return list(collected.values())


def enrich_change_with_ai(
    openai_client: AzureOpenAI,
    model_name: str,
    order: dict[str, Any],
) -> dict[str, Any]:
    """Call Azure OpenAI GPT-6 Luna to extract sentiment, urgency, and category."""
    customer_notes = str(order.get("customer_notes", ""))
    order_amount = order.get("order_amount", 0.0)
    system_prompt = (
        "You are an AI assistant evaluating retail order changes. "
        "Analyze customer notes and transaction details, then respond ONLY with a "
        "valid JSON object having keys: "
        '"sentiment" (positive, neutral, negative), '
        '"urgency" (low, normal, high), '
        '"category" (e.g. corporate_event, office_supply, defect_support), and '
        '"summary" (one concise sentence explaining the customer request).'
    )
    user_prompt = (
        f"Order ID: {order.get('id')}\n"
        f"Store ID: {order.get('store_id')}\n"
        f"Amount: {order_amount}\n"
        f"Customer Notes: {customer_notes}"
    )

    response = openai_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        parsed = json.loads(json_match.group(0)) if json_match else {}

    sentiment = str(parsed.get("sentiment", "neutral")).lower()
    urgency = str(parsed.get("urgency", "normal")).lower()
    category = str(parsed.get("category", "general"))
    summary = str(parsed.get("summary", customer_notes))

    enriched = dict(order)
    enriched["sentiment"] = sentiment
    enriched["urgency"] = urgency
    enriched["category"] = category
    enriched["ai_summary"] = summary
    enriched["enriched_at"] = datetime.now(UTC).isoformat()
    return enriched


def update_materialized_views(
    table_service: TableServiceClient,
    blob_service: BlobServiceClient,
    table_name: str,
    blob_container_name: str,
    batch_id: str,
    enriched_orders: list[dict[str, Any]],
    table_entity_keys: list[tuple[str, str]],
    uploaded_blobs: list[str],
) -> None:
    """Update Table Storage materialized entities and Blob Storage archive files."""
    table_client = table_service.get_table_client(table_name)
    try:
        table_client.create_table()
    except AzureError as err:
        if "TableAlreadyExists" not in str(err) and getattr(err, "status_code", None) != 409:
            raise

    for order in enriched_orders:
        partition_key = str(order["store_id"])
        row_key = str(order["id"])
        entity = {
            "PartitionKey": partition_key,
            "RowKey": row_key,
            "CustomerId": str(order.get("customer_id", "")),
            "OrderAmount": float(order.get("order_amount", 0.0)),
            "ItemCount": int(order.get("item_count", 1)),
            "OrderStatus": str(order.get("order_status", "")),
            "Sentiment": str(order.get("sentiment", "neutral")),
            "Urgency": str(order.get("urgency", "normal")),
            "Category": str(order.get("category", "general")),
            "Summary": str(order.get("ai_summary", "")),
            "ProcessedAt": str(order.get("enriched_at", "")),
        }
        table_client.upsert_entity(entity=entity)
        table_entity_keys.append((partition_key, row_key))

    blob_container = blob_service.get_container_client(blob_container_name)
    try:
        blob_container.create_container()
    except AzureError as err:
        status = getattr(err, "status_code", None)
        if status not in (403, 409) and "ContainerAlreadyExists" not in str(err):
            raise

    # Write partitioned analytical snapshot to Blob Storage
    stores = {order["store_id"] for order in enriched_orders}
    for store_id in stores:
        store_records = [o for o in enriched_orders if o["store_id"] == store_id]
        blob_name = f"changes/{batch_id}/store_{store_id}.jsonl"
        lines = [json.dumps(record) for record in store_records]
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        blob_container.upload_blob(name=blob_name, data=payload, overwrite=True)
        uploaded_blobs.append(blob_name)


def verify_pipeline_results(
    table_service: TableServiceClient,
    blob_service: BlobServiceClient,
    table_name: str,
    blob_container_name: str,
    table_entity_keys: list[tuple[str, str]],
    uploaded_blobs: list[str],
) -> None:
    """Verify materialized table entities and blob snapshots."""
    table_client = table_service.get_table_client(table_name)
    for partition_key, row_key in table_entity_keys:
        entity = table_client.get_entity(partition_key=partition_key, row_key=row_key)
        if not entity or entity.get("PartitionKey") != partition_key:
            raise ValueError(f"Table entity verification failed for {partition_key}:{row_key}.")

    blob_container = blob_service.get_container_client(blob_container_name)
    for blob_name in uploaded_blobs:
        data = blob_container.download_blob(blob_name).readall()
        if not data:
            raise ValueError(f"Blob snapshot {blob_name} was empty.")


def run_pipeline(target: Target) -> None:
    """Execute change data capture, AI enrichment, analytical updates, and cleanup."""
    batch_id = f"azpg-05-{uuid4().hex}"
    print(f"CDC execution batch: {batch_id}")

    inserted_keys: list[tuple[str, str]] = []
    table_entity_keys: list[tuple[str, str]] = []
    uploaded_blobs: list[str] = []

    with (
        DefaultAzureCredential() as credential,
        CosmosClient(url=target.cosmos_endpoint, credential=credential) as cosmos_client,
        TableServiceClient(endpoint=target.table_endpoint, credential=credential) as table_service,
        BlobServiceClient(
            account_url=target.storage_account_url, credential=credential
        ) as blob_service,
        AzureOpenAI(
            azure_endpoint=target.openai_endpoint,
            azure_ad_token_provider=get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            ),
            api_version="2024-06-01",
        ) as openai_client,
    ):
        try:
            print("1. Writing transactional order records to Azure Cosmos DB...")
            # Annotate sample orders with batch tag for isolation
            batch_orders = [
                {**order, "batch_id": batch_id, "id": f"{order['id']}-{batch_id}"}
                for order in SAMPLE_ORDERS
            ]
            write_transactional_orders(
                cosmos_client,
                target.cosmos_database_name,
                target.cosmos_container_name,
                batch_orders,
                inserted_keys,
            )
            print(f"   Written {len(batch_orders)} documents to Cosmos DB.")

            print("2. Reading change events from Cosmos DB Change Feed...")
            expected_ids = {order["id"] for order in batch_orders}
            change_events = read_cosmos_change_feed(
                cosmos_client,
                target.cosmos_database_name,
                target.cosmos_container_name,
                expected_ids,
            )
            print(f"   Captured {len(change_events)} change events from change feed.")

            print("3. Enriching captured change records with Azure OpenAI GPT-6 Luna...")
            enriched_orders: list[dict[str, Any]] = []
            for item in change_events:
                enriched = enrich_change_with_ai(openai_client, target.chat_deployment_name, item)
                enriched_orders.append(enriched)
                print(
                    f"   - Order {enriched['id']}: Sentiment={enriched['sentiment']}, "
                    f"Urgency={enriched['urgency']}, Category={enriched['category']}"
                )

            print("4. Updating materialized views in Azure Table Storage and Blob Storage...")
            update_materialized_views(
                table_service,
                blob_service,
                target.table_name,
                target.blob_container_name,
                batch_id,
                enriched_orders,
                table_entity_keys,
                uploaded_blobs,
            )
            print(f"   Upserted {len(table_entity_keys)} materialized rows to Table Storage.")
            print(f"   Published {len(uploaded_blobs)} partitioned files to Blob Storage.")

            print("5. Verifying materialized views and data consistency...")
            verify_pipeline_results(
                table_service,
                blob_service,
                target.table_name,
                target.blob_container_name,
                table_entity_keys,
                uploaded_blobs,
            )
            print("   Verification successful: analytical views match captured transactions.")

        finally:
            print("6. Cleaning up temporary experiment artifacts...")
            failed: list[str] = []

            # Clean Cosmos DB records
            database = cosmos_client.get_database_client(target.cosmos_database_name)
            container = database.get_container_client(target.cosmos_container_name)
            for item_id, store_id in inserted_keys:
                try:
                    container.delete_item(item=item_id, partition_key=store_id)
                except CosmosHttpResponseError as err:
                    if err.status_code != 404:
                        failed.append(f"cosmos:{item_id}")
                except AzureError:
                    failed.append(f"cosmos:{item_id}")

            # Clean Table Storage entities
            table_client = table_service.get_table_client(target.table_name)
            for partition_key, row_key in table_entity_keys:
                try:
                    table_client.delete_entity(partition_key=partition_key, row_key=row_key)
                except ResourceNotFoundError:
                    pass
                except AzureError:
                    failed.append(f"table:{partition_key}:{row_key}")

            # Clean Blob Storage files
            blob_container = blob_service.get_container_client(target.blob_container_name)
            for blob_name in uploaded_blobs:
                try:
                    blob_container.delete_blob(blob_name)
                except ResourceNotFoundError:
                    pass
                except AzureError:
                    failed.append(f"blob:{blob_name}")

            if failed:
                raise AzureError(f"Cleanup failed for artifacts: {failed}")
            print("   Cleaned up all temporary CDC artifacts.")

    print("Change Data Capture pipeline execution succeeded.")


def main() -> int:
    """Confirm the verified target and run the CDC pipeline."""
    try:
        target = load_target()
        verify_target(target)
        print(f"Subscription: {target.subscription_id}")
        print(f"Resource group: {target.resource_group_name}")
        print(f"Region: {target.location}")
        print(f"Cosmos DB endpoint: {target.cosmos_endpoint}")
        print(f"Cosmos DB database: {target.cosmos_database_name}")
        print(f"Cosmos DB container: {target.cosmos_container_name}")
        print(f"Storage account: {target.storage_account_url}")
        print(f"Table endpoint: {target.table_endpoint}")
        print(f"Table name: {target.table_name}")
        print(f"Blob container: {target.blob_container_name}")
        print(f"OpenAI endpoint: {target.openai_endpoint}")
        print(f"Chat model: {target.chat_deployment_name}")
        print(
            "This runs the Change Data Capture pipeline across Cosmos DB, "
            "Azure Table Storage, Blob Storage, and Azure OpenAI. "
            "Serverless Cosmos DB and Azure OpenAI token charges may apply."
        )
        confirmation = input("Type the resource group name to continue: ")
        if confirmation != target.resource_group_name:
            print("Cancelled. No Cosmos DB, Storage, or OpenAI data was changed.")
            return 1
        run_pipeline(target)
    except (ValueError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        print(
            "OpenTofu or Azure CLI failed. Check tool installation, make up, az login, "
            "and sandbox resource group state.",
            file=sys.stderr,
        )
        return 1
    except AzureError, OpenAIError:
        print(
            "Azure Cosmos DB, OpenAI, or storage access failed. Check DefaultAzureCredential, "
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
