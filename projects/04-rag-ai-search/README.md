### 04: Retrieval-augmented Generation with Azure AI Search and Azure OpenAI

Ingest built-in sample documents, generate vector embeddings using Azure OpenAI, create a hybrid vector and keyword index in Azure AI Search, and generate answers with source citations using Python.

#### Architecture

```mermaid
flowchart TD
    subgraph Local["Local Environment"]
        User["Developer or CI Runner"]
        Tofu["OpenTofu (infra/)"]
        PythonRunner["RAG Pipeline (run.py)"]
    end

    subgraph AzureControlPlane["Azure Control Plane"]
        Entra["Microsoft Entra ID"]
        ARM["Azure Resource Manager"]
        RG["Resource Group (rg-azpg-04-*)"]
        RoleAssignSearch["Roles: Search Service Contributor and Search Index Data Contributor"]
        RoleAssignOpenAI["Role Assignment: Cognitive Services OpenAI User"]
        RoleAssignStorage["Role Assignment: Storage Blob Data Contributor"]
    end

    subgraph AzureDataPlane["Azure AI and Storage Data Plane"]
        SearchService["Azure AI Search (Hybrid Vector Index)"]
        OpenAIService["Azure OpenAI (Embeddings and Chat)"]
        StorageAccount["Storage Account (Raw Document Store)"]
    end

    User -->|" Runs tofu commands "| Tofu
    Tofu -->|" Provisions via ARM "| RG
    RG -->|" Contains "| SearchService
    RG -->|" Contains "| OpenAIService
    RG -->|" Contains "| StorageAccount
    Tofu -->|" Assigns roles to principal "| RoleAssignSearch
    Tofu -->|" Assigns roles to principal "| RoleAssignOpenAI
    Tofu -->|" Assigns roles to principal "| RoleAssignStorage
    Tofu -->|" Supplies state outputs "| PythonRunner
    User -->|" Runs make run "| PythonRunner
    PythonRunner -->|" Acquires token "| Entra
    PythonRunner -->|" Uploads raw documents "| StorageAccount
    PythonRunner -->|" Generates text embeddings "| OpenAIService
    PythonRunner -->|" Indexes and retrieves chunks "| SearchService
    PythonRunner -->|" Generates grounded response "| OpenAIService
```

#### Retrieval-augmented Generation Lifecycle Flow

The Python runner executes a complete, self-contained RAG indexing, query, evaluation, and cleanup cycle:

```mermaid
flowchart LR
    subgraph Step1["1. Chunking"]
        ChunkDocs["Parse and Chunk Documents"]
    end

    subgraph Step2["2. Vectorization"]
        EmbedChunks["Generate Embeddings via OpenAI"]
    end

    subgraph Step3["3. Indexing"]
        UploadIndex["Upload Vectors and Text to AI Search"]
    end

    subgraph Step4["4. Retrieval"]
        HybridSearch["Perform Hybrid Vector and Keyword Search"]
    end

    subgraph Step5["5. Synthesis"]
        GenerateAnswer["Generate Grounded Answer with Citations"]
    end

    subgraph Step6["6. Teardown"]
        CleanArtifacts["Delete Test Index and Storage Blobs"]
    end

    ChunkDocs --> EmbedChunks
    EmbedChunks --> UploadIndex
    UploadIndex --> HybridSearch
    HybridSearch --> GenerateAnswer
    GenerateAnswer --> CleanArtifacts
```

#### Prerequisites

- Python 3.14 or newer, uv, GNU Make, Azure CLI, and OpenTofu 1.8 or newer.
- A non-production Azure subscription with access to Azure OpenAI Service and Azure AI Search.
- Allowed deployment region supporting both Azure OpenAI and Azure AI Search (such as `norwayeast`, `swedencentral`, or `westeurope`).
- Role assignment permissions on the resource group to assign `Search Service Contributor`, `Search Index Data Contributor`, `Cognitive Services OpenAI User`, and `Storage Blob Data Contributor`.
- The `Microsoft.Search`, `Microsoft.CognitiveServices`, and `Microsoft.Storage` resource providers registered on your subscription.

From the repository root, install dependencies and sign in:

```bash
make install
make login
```

#### Configure the Sandbox

Navigate to the project directory and prepare configuration:

```bash
cd projects/04-rag-ai-search
cp infra/terraform.tfvars.example infra/terraform.tfvars
az account list --query '[].{name:name,id:id}' --output table
az ad signed-in-user show --query id --output tsv
```

Update each field in `infra/terraform.tfvars`:

- `subscription_id`: The target Azure subscription identifier.
- `location`: An Azure region where both Azure OpenAI and Azure AI Search are supported, such as `norwayeast` or `swedencentral`.
- `resource_group_name`: A dedicated name starting with `rg-azpg-04-`.
- `storage_account_name`: A globally unique name starting with `azpg04`, containing only lowercase alphanumeric characters.
- `search_service_name`: A globally unique name starting with `azpg04`, containing only lowercase alphanumeric characters and hyphens.
- `openai_account_name`: A globally unique Cognitive Services account name starting with `azpg04`.
- `principal_object_id`: The object identifier of your signed-in identity.

The search service uses the billable Basic tier. The Free tier does not support this experiment's Microsoft Entra authentication.
Remove `search_service_sku` from older local variable files; it is no longer configurable.

Verify resource provider registrations:

```bash
az provider show --subscription YOUR_SUBSCRIPTION_ID \
  --namespace Microsoft.Search --query registrationState --output tsv
az provider show --subscription YOUR_SUBSCRIPTION_ID \
  --namespace Microsoft.CognitiveServices --query registrationState --output tsv
az provider show --subscription YOUR_SUBSCRIPTION_ID \
  --namespace Microsoft.Storage --query registrationState --output tsv
```

All commands must return `Registered`.

#### Run and Clean Up

```bash
make plan
make up
make run
make down
```

- `make plan` displays an execution plan for the storage account, Azure AI Search service, Azure OpenAI account, model deployments (`text-embedding-3-small` and `gpt-6-luna`), and role assignments.
- `make up` requires entering `yes` before provisioning infrastructure.
- `make run` prompts for confirmation, uploads sample documentation to storage, computes chunk embeddings, creates a hybrid vector index in Azure AI Search, runs a grounded retrieval query, and validates citations.
- `make down` requires entering `yes` and destroys all sandbox resources created for this project.

The runner archives the sample documents as JSON and creates a unique search index for each run.
The `search_index_name` output is the prefix for these temporary index names.
Document upload failures and indexing timeouts stop the run. Cleanup covers partial uploads and later failures.
Failed deletions produce a failed exit and identify remaining artifacts. Always run `make down` when finished.
The runner does not read Markdown files or configure a semantic re-ranker.

#### Common Issues and Troubleshooting

- Missing provider registration: If OpenTofu returns `MissingSubscriptionRegistration`, run `az provider register --namespace Microsoft.Search` and wait until the status changes to `Registered`.
- OpenAI access approval: If Azure rejects cognitive account creation, ensure your subscription has been granted access to Azure OpenAI models.
- Search index permission errors: The running identity needs both `Search Service Contributor` to manage the index and `Search Index Data Contributor` to upload and retrieve documents.
- Role propagation delay: Azure RBAC role assignments can take several minutes to propagate. If `run.py` fails with authorization errors immediately after `make up`, wait three to five minutes and retry `make run`.
