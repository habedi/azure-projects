## Microsoft Azure Playground

[![Tests](https://img.shields.io/github/actions/workflow/status/habedi/azure-playground/tests.yml?label=tests&style=flat&labelColor=333333&logo=github&logoColor=white)](https://github.com/habedi/azure-playground/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-MIT-007ec6?style=flat&labelColor=333333&logo=open-source-initiative&logoColor=white)](LICENSE)

---

This is a playground for learning about and experimenting with Microsoft Azure services.

---

### Quickstart

Install Python 3.14 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and GNU Make.
Then install the dependencies and run the offline tests:

```bash
make install
make test
```

The `make test` target runs the full test suite in `tests/` and shows coverage with missing line numbers.
Run `make coverage` to also generate an HTML report at `htmlcov/index.html` and an XML report at `coverage.xml`.
Coverage measures local Python files, including the smoke tests. There are no application modules yet.
Coverage artifacts are ignored by Git and removed by `make clean`.
Run `make help` for the available development commands.

#### Nix Development Shell

On x86_64 or aarch64 Linux, Nix with flakes enabled can provide Python 3.14, uv, GNU Make,
Git, and Azure CLI:

```bash
nix develop
make install
make test
```

The shell sets `UV_PYTHON` to its Nix-provided Python interpreter. Python dependencies remain
managed by uv and `uv.lock`. Entering the shell does not sign in to Azure or create cloud resources.
Run `exit` to leave the shell.

#### Azure Authentication

Local checks do not need an Azure account. For experiments that access Azure, install the
[Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli), and sign in to a sandbox subscription:

```bash
az login
az account set --subscription "<subscription-id>"
az account show --output table
```

Use `DefaultAzureCredential` from `azure.identity` in Python experiments to use your CLI login locally
or a managed identity when running in Azure. Grant only the permissions each experiment needs.
For Blob Storage data access, use an appropriate data role, such as Storage Blob Data Reader for read-only access.

#### Azure MCP Server

Microsoft provides an official [Azure MCP Server](https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/).
The root [`.mcp.json`](.mcp.json) configures it for clients that accept the `mcpServers` format.
Import this configuration in your MCP client if it does not load project files automatically.
For PyCharm, configure an external MCP server in your assistant's MCP settings using this configuration.
The IDE process must have `uvx` and Azure CLI on its `PATH`.

The server runs locally over standard input and output, using Microsoft's `msmcp-azure` package pinned
to version 3.0.0. `uvx` downloads it on first use into an isolated tool environment, outside the project's
Python dependencies and `uv.lock`. No Node.js installation or API key is needed.
Follow the Azure authentication steps above before using Azure tools, and verify the sandbox subscription.
Grant only the required roles, such as Reader on the sandbox resource group for resource inspection;
data access may require separate data roles. The selected CLI subscription is not an access boundary:
the server may discover other subscriptions available to your identity.

The configuration enables `--read-only` and disables external proxy tools with `--disable-proxy-tools`.
Keep these safeguards enabled, review tool calls before approving them, and avoid sharing sensitive results.
Read-only access can still expose private data or incur query and data-transfer charges.
There is no cloud deployment to clean up. Stop or disable the server in your MCP client when finished;
use `az logout` if you also want to end your local Azure CLI session.

#### Local Configuration

Copy `.env.example` to `.env`, and fill in the settings needed by your experiment:

```bash
cp .env.example .env
```

The template contains configuration placeholders, not credentials. Python scripts can call
`load_dotenv()` from `dotenv` to load these settings. This does not happen automatically.
Keep credentials, access tokens, connection strings, and local `.env` files out of version control.
Prefer managed identities over client secrets for deployed workloads.

### Safe Experimentation

- Use a dedicated sandbox subscription or resource group, not production resources.
- Check the selected subscription and region before creating or deleting resources.
- Set budgets and alerts, and review service pricing before running an experiment. Budget alerts do not cap spending.
- Document each experiment's required permissions, expected costs, and cleanup steps.
- Delete only resources created for your experiment when finished. Data deletion can be irreversible.
- Keep automated tests offline by mocking Azure clients. Do not let tests create billable resources.

### Learning Resources

- [Azure Fundamentals](https://learn.microsoft.com/en-us/training/paths/azure-fundamentals-describe-cloud-concepts/)
- [Azure SDK for Python](https://learn.microsoft.com/en-us/azure/developer/python/sdk/azure-sdk-overview)
- [Azure Identity for Python](https://learn.microsoft.com/en-us/python/api/overview/azure/identity-readme)
- [Azure Blob Storage for Python](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python)

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

This project is licensed under the MIT License (see [LICENSE](LICENSE)).
