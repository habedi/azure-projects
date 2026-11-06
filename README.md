## Microsoft Azure Projects

[![Tests](https://img.shields.io/github/actions/workflow/status/habedi/azure-projects/tests.yml?label=tests&style=flat&labelColor=333333&logo=github&logoColor=white)](https://github.com/habedi/azure-projects/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-Apache_2.0-007ec6?style=flat&labelColor=333333&logo=apache&logoColor=white)](LICENSE)
[![Azure Portal](https://img.shields.io/badge/Azure%20Portal-visit-0089D6?style=flat&labelColor=333333&logo=github&logoColor=white)](https://portal.azure.com/#home)

---

This repository contains projects for learning more about Microsoft Azure services, testing different use cases, and developing cloud applications
in Azure.

---

### Quickstart

To follow the steps below, you need to have Git and [Nix](https://nixos.org/download/) (with flakes enabled) installed on your machine.

#### 1. Clone the repository

```bash
git clone https://github.com/habedi/azure-projects.git
cd azure-projects
```

#### 2. Set up the development environment

```shell
nix develop
make install
```

#### 3. Check out the projects

Check out the contents of the [projects](projects) directory for the Azure projects and their documentation.

| #  | Project Name                                                   | Domain           | Primary Azure Services                            | Complexity   |
|----|----------------------------------------------------------------|------------------|---------------------------------------------------|--------------|
| 01 | [Blob storage round trip](projects/01-blob-storage)            | Data Engineering | Azure Blob Storage                                | Beginner     |
| 02 | [Medallion lakehouse](projects/02-fabric-lakehouse)            | Data Engineering | Microsoft Fabric, OneLake                         | Intermediate |
| 03 | [Event streaming ingestion](projects/03-event-streaming)       | Data Engineering | Azure Event Hubs, Data Lake Storage Gen2          | Intermediate |
| 04 | [Retrieval-augmented generation](projects/04-rag-ai-search)    | AI Engineering   | Azure AI Search, OpenAI, Blob Storage             | Intermediate |
| 05 | [Change data capture](projects/05-change-data-capture)         | Data Engineering | Cosmos DB, OpenAI, Table and Blob Storage         | Intermediate |
| 06 | [Document processing](projects/06-document-processing)         | AI Engineering   | Azure AI Document Intelligence, Blob Storage      | Intermediate |
| 07 | [Evaluation and observability](projects/07-eval-observability) | AI Engineering   | Azure OpenAI, Application Insights, Log Analytics | Advanced     |

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

This project is licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)).
