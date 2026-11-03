"""Ingest, index, retrieve, and generate grounded answers using Azure AI Search and Azure OpenAI."""

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
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

INFRA_DIR = Path(__file__).resolve().parent / "infra"

# Sample documentation library for grounded retrieval
KNOWLEDGE_DOCUMENTS: list[dict[str, str]] = [
    {
        "doc_id": "doc-cloud-safety",
        "title": "Azure Cloud Safety and Least Privilege",
        "text": (
            "Cloud safety requires using least-privilege access and dedicated sandbox resource groups. "
            "Never deploy experiments into shared or production subscriptions. "
            "Use Microsoft Entra ID authentication with DefaultAzureCredential instead of hardcoded keys. "
            "Always verify the target subscription UUID before applying infrastructure changes."
        ),
    },
    {
        "doc_id": "doc-cost-control",
        "title": "Azure Cost Control and Teardown",
        "text": (
            "Cloud cost control ensures experiments do not incur unexpected charges. "
            "Fabric compute capacities should be suspended immediately when idle using make suspend. "
            "Event Hubs Standard namespaces and AI Search services accrue hourly charges while provisioned. "
            "Always run make down to permanently destroy temporary sandbox resources after testing."
        ),
    },
    {
        "doc_id": "doc-storage-security",
        "title": "Azure Storage Security Policies",
        "text": (
            "Storage accounts should enforce HTTPS traffic and require minimum TLS version 1.2. "
            "Disable shared access keys and anonymous blob access across all sandbox accounts. "
            "Grant access through Azure RBAC role assignments such as Storage Blob Data Contributor. "
            "Keep storage account names globally unique with lowercase alphanumeric characters."
        ),
    },
]


@dataclass(frozen=True)
class Target:
    """Identify the sandbox resources recorded in OpenTofu state."""

    subscription_id: str
    resource_group_name: str
    location: str
    storage_account_url: str
    container_name: str
    search_service_endpoint: str
    search_service_name: str
    search_index_name: str
    openai_endpoint: str
    embedding_deployment_name: str
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
                "storage_account_url",
                "container_name",
                "search_service_endpoint",
                "search_service_name",
                "search_index_name",
                "openai_endpoint",
                "embedding_deployment_name",
                "chat_deployment_name",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("All OpenTofu outputs must be non-empty strings.")
        target = Target(**values)
        UUID(target.subscription_id)
        if not re.fullmatch(r"rg-azpg-04-[a-z0-9-]+", target.resource_group_name):
            raise ValueError("Invalid resource_group_name in target state.")
        if not re.fullmatch(r"[a-z0-9]+", target.location):
            raise ValueError("Invalid location in target state.")
        if not re.fullmatch(
            r"https://azpg04[a-z0-9]+\.blob\.core\.windows\.net/?", target.storage_account_url
        ):
            raise ValueError("Invalid storage_account_url in target state.")
        if not re.fullmatch(r"[a-z0-9-]+", target.container_name):
            raise ValueError("Invalid container_name in target state.")
        if not re.fullmatch(
            r"https://azpg04[a-z0-9-]+\.search\.windows\.net/?", target.search_service_endpoint
        ):
            raise ValueError("Invalid search_service_endpoint in target state.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", target.search_index_name):
            raise ValueError("Invalid search_index_name in target state.")
        if not re.fullmatch(
            r"https://azpg04[a-z0-9-]+\.openai\.azure\.com/?", target.openai_endpoint
        ):
            raise ValueError("Invalid openai_endpoint in target state.")
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


def chunk_documents(
    documents: list[dict[str, str]], max_chunk_words: int = 50
) -> list[dict[str, Any]]:
    """Segment document texts into indexed chunks with source metadata."""
    chunks: list[dict[str, Any]] = []
    for doc in documents:
        doc_id = doc["doc_id"]
        title = doc["title"]
        words = doc["text"].split()

        for idx in range(0, len(words), max_chunk_words):
            chunk_slice = words[idx : idx + max_chunk_words]
            chunk_content = " ".join(chunk_slice)
            chunk_id = f"{doc_id}-chunk-{idx // max_chunk_words}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "title": title,
                    "content": chunk_content,
                }
            )
    return chunks


def build_search_index_definition(index_name: str) -> SearchIndex:
    """Define schema for Azure AI Search hybrid vector and keyword index."""
    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.STRING, key=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.STRING, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.STRING),
        SearchableField(name="content", type=SearchFieldDataType.STRING),
        SearchField(
            name="content_vector",
            type="Collection(Edm.Single)",
            vector_search_dimensions=1536,
            vector_search_profile_name="rag-vector-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="rag-hnsw-algorithm")],
        profiles=[
            VectorSearchProfile(
                name="rag-vector-profile",
                algorithm_configuration_name="rag-hnsw-algorithm",
            )
        ],
    )

    return SearchIndex(name=index_name, fields=fields, vector_search=vector_search)


def generate_embeddings(
    openai_client: AzureOpenAI,
    model_name: str,
    texts: list[str],
) -> list[list[float]]:
    """Generate normalized vector embeddings for a list of text passages."""
    response = openai_client.embeddings.create(input=texts, model=model_name)
    return [item.embedding for item in response.data]


def upload_raw_documents(
    blob_service: BlobServiceClient,
    container_name: str,
    batch_id: str,
    documents: list[dict[str, str]],
) -> list[str]:
    """Upload source documents to blob storage for persistent archival."""
    container_client = blob_service.get_container_client(container_name)
    try:
        container_client.create_container()
    except AzureError as err:
        if getattr(err, "status_code", None) not in (
            403,
            409,
        ) and "ContainerAlreadyExists" not in str(err):
            raise

    uploaded_blobs: list[str] = []
    for doc in documents:
        blob_name = f"sources/{batch_id}/{doc['doc_id']}.json"
        data = json.dumps(doc, indent=2).encode("utf-8")
        container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        uploaded_blobs.append(blob_name)
    return uploaded_blobs


def wait_for_indexing(
    search_client: SearchClient, expected_count: int, timeout_seconds: float = 15.0
) -> None:
    """Wait for Azure AI Search to index uploaded vectorized documents."""
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_seconds:
        try:
            if search_client.get_document_count() >= expected_count:
                time.sleep(2.0)
                return
        except Exception:
            pass
        time.sleep(1.0)


def perform_hybrid_retrieval(
    search_client: SearchClient,
    query: str,
    query_vector: list[float],
    top_k: int = 2,
    max_retries: int = 5,
) -> list[dict[str, Any]]:
    """Execute hybrid search combining text keywords and cosine vector similarity with retry."""
    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=top_k,
        fields="content_vector",
    )
    for attempt in range(max_retries):
        results = search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            select=["chunk_id", "doc_id", "title", "content"],
            top=top_k,
        )
        retrieved: list[dict[str, Any]] = []
        for item in results:
            retrieved.append(
                {
                    "chunk_id": item["chunk_id"],
                    "doc_id": item["doc_id"],
                    "title": item["title"],
                    "content": item["content"],
                    "score": getattr(item, "@search.score", 0.0),
                }
            )
        if retrieved or attempt == max_retries - 1:
            return retrieved
        time.sleep(2.0)
    return []


def generate_grounded_response(
    openai_client: AzureOpenAI,
    model_name: str,
    user_query: str,
    retrieved_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Synthesize an answer strictly constrained by retrieved passages with citations."""
    context_blocks = [
        f"[Source: {chunk['doc_id']}] ({chunk['title']})\n{chunk['content']}"
        for chunk in retrieved_chunks
    ]
    context_text = "\n\n".join(context_blocks)

    system_prompt = (
        "You are an assistant answering questions strictly based on the provided source passages. "
        "Every claim must reference its source document as [Source: doc_id]. "
        "If the context does not contain enough information, state that clearly."
    )
    user_prompt = f"Context passages:\n{context_text}\n\nUser Question: {user_query}"

    response = openai_client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    answer_text = response.choices[0].message.content or ""
    citations = list(set(re.findall(r"\[Source:\s*([^\]]+)\]", answer_text)))

    return {
        "query": user_query,
        "answer": answer_text,
        "citations": sorted(citations),
        "context_passages_count": len(retrieved_chunks),
    }


def run_pipeline(target: Target) -> None:
    """Execute complete RAG document ingestion, indexing, retrieval, and evaluation."""
    batch_id = f"azpg-04-{uuid4().hex}"
    print(f"RAG execution batch: {batch_id}")

    with DefaultAzureCredential() as credential:
        # Step 1: Upload raw documents to storage
        print("1. Uploading source documents to Azure Blob Storage...")
        with BlobServiceClient(
            account_url=target.storage_account_url, credential=credential
        ) as blob_service:
            uploaded_blobs = upload_raw_documents(
                blob_service, target.container_name, batch_id, KNOWLEDGE_DOCUMENTS
            )
            print(f"   Uploaded {len(uploaded_blobs)} source documents to container.")

            # Step 2: Chunk documents and compute embeddings
            print("2. Chunking documents and generating text embeddings via Azure OpenAI...")
            chunks = chunk_documents(KNOWLEDGE_DOCUMENTS)
            print(f"   Generated {len(chunks)} text chunks across source documents.")

            token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
            openai_client = AzureOpenAI(
                azure_endpoint=target.openai_endpoint,
                azure_ad_token_provider=token_provider,
                api_version="2024-06-01",
            )

            chunk_texts = [chunk["content"] for chunk in chunks]
            vectors = generate_embeddings(
                openai_client, target.embedding_deployment_name, chunk_texts
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk["content_vector"] = vector
            print(f"   Computed {len(vectors)} embeddings ({len(vectors[0])} dimensions).")

            # Step 3: Create index and upload vectors to Azure AI Search
            print("3. Creating hybrid vector index in Azure AI Search...")
            search_index_client = SearchIndexClient(
                endpoint=target.search_service_endpoint, credential=credential
            )
            index_def = build_search_index_definition(target.search_index_name)
            search_index_client.create_or_update_index(index_def)
            print(f"   Configured search index: {target.search_index_name}")

            search_client = SearchClient(
                endpoint=target.search_service_endpoint,
                index_name=target.search_index_name,
                credential=credential,
            )
            search_client.upload_documents(documents=chunks)
            print(f"   Uploaded {len(chunks)} vectorized passages to search index.")
            print("   Waiting for Azure AI Search to index documents...")
            wait_for_indexing(search_client, expected_count=len(chunks))

            try:
                # Step 4: Hybrid retrieval
                query = "How do we manage cloud costs and resource teardown in our experiments?"
                print(f"4. Performing hybrid search for query: '{query}'...")
                query_vector = generate_embeddings(
                    openai_client, target.embedding_deployment_name, [query]
                )[0]
                retrieved = perform_hybrid_retrieval(search_client, query, query_vector, top_k=2)
                print(f"   Retrieved {len(retrieved)} relevant passages from AI Search.")
                for r in retrieved:
                    print(f"   - Match: [{r['doc_id']}] {r['title']} (Score: {r['score']:.4f})")

                # Step 5: Grounded answer generation
                print("5. Generating grounded response with citations via GPT-6-Luna...")
                result = generate_grounded_response(
                    openai_client, target.chat_deployment_name, query, retrieved
                )
                print("\n--- Model Response ---")
                print(result["answer"])
                print("----------------------\n")
                print(f"   Citations extracted: {result['citations']}")

                # Validation assertion
                if "doc-cost-control" not in result["citations"]:
                    raise ValueError(
                        "Expected citation [Source: doc-cost-control] was not found in response."
                    )
                print(
                    "   Verification successful: grounded response generated with valid citations."
                )

            finally:
                # Step 6: Cleanup
                print("6. Cleaning up search index documents and storage artifacts...")
                container_client = blob_service.get_container_client(target.container_name)
                for blob_name in uploaded_blobs:
                    try:
                        container_client.delete_blob(blob_name)
                    except AzureError:
                        print(f"   Warning: Cleanup failed for {blob_name}.", file=sys.stderr)
                try:
                    search_index_client.delete_index(target.search_index_name)
                    print(f"   Removed search index: {target.search_index_name}")
                except AzureError:
                    print(
                        f"   Warning: Failed to delete index {target.search_index_name}.",
                        file=sys.stderr,
                    )
                print("   Cleaned up all temporary RAG artifacts.")

    print("RAG pipeline execution succeeded.")


def main() -> int:
    """Confirm the verified target and run the RAG pipeline."""
    try:
        target = load_target()
        verify_target(target)
        print(f"Subscription: {target.subscription_id}")
        print(f"Resource group: {target.resource_group_name}")
        print(f"Region: {target.location}")
        print(f"Storage account: {target.storage_account_url}")
        print(f"Search endpoint: {target.search_service_endpoint}")
        print(f"Search index: {target.search_index_name}")
        print(f"OpenAI endpoint: {target.openai_endpoint}")
        print(f"Embedding model: {target.embedding_deployment_name}")
        print(f"Chat model: {target.chat_deployment_name}")
        print(
            "This runs the RAG indexing and retrieval pipeline in Azure AI Search and OpenAI. "
            "Charges may apply to AI Search and Azure OpenAI."
        )
        confirmation = input("Type the resource group name to continue: ")
        if confirmation != target.resource_group_name:
            print("Cancelled. No AI Search or OpenAI data was changed.")
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
            "Azure AI Search, OpenAI, or storage access failed. Check DefaultAzureCredential, "
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
