## Contribution Guidelines

Thank you for considering contributing to this project!
Contributions are always welcome and appreciated.

### How to Contribute

Please check the [issue tracker](https://github.com/habedi/azure-playground/issues) to see if there is an issue
you would like to work on or if it has already been resolved.

#### Reporting Bugs

1. Open an issue on the [issue tracker](https://github.com/habedi/azure-playground/issues).
2. Include steps to reproduce the behavior, the Azure service involved, and relevant logs or screenshots.
3. Remove credentials, access tokens, connection strings, and personal data before sharing logs.

#### Suggesting Features

1. Open an issue on the [issue tracker](https://github.com/habedi/azure-playground/issues).
2. Provide details about the feature, its purpose, and potential implementation ideas.

### Submitting Pull Requests

- Make sure all tests pass before submitting a pull request.
- Write a clear description of the changes you made and the reasons behind them.

> [!IMPORTANT]
> It's assumed that by submitting a pull request, you agree to license your contributions under the project's license.

### Development Workflow

#### Prerequisites

Install Python 3.14 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and GNU Make.
Install the Azure CLI only if your experiment needs access to Azure.

```bash
make install
make setup-hooks
```

Use `uv add PACKAGE` for runtime dependencies and `uv add --dev PACKAGE` for development tools.
Include both `pyproject.toml` and `uv.lock` when changing dependencies.

#### Code Style

- Use the `make format` command to format the code.

#### Running Tests

- Use the `make test` command to run the full test suite with terminal coverage.
- Use `make coverage` to also generate reports at `htmlcov/index.html` and `coverage.xml`.
- Keep tests deterministic and independent of Azure credentials, subscriptions, and network access.
- Mock Azure clients rather than creating cloud resources in automated tests.

#### Azure Experiments

- Document prerequisites, configuration, required roles, expected costs, and resource cleanup.
- Use a sandbox subscription and least-privilege access. Prefer `DefaultAzureCredential` and managed identities.
- Keep secrets in local environment variables or an appropriate secret store, never in tracked files or notebook outputs.
- Make resource creation repeatable, and make deletion explicit and limited to the experiment's resources.

#### See Available Commands

- Run `make help` to see all available commands for managing different tasks.

### Code of Conduct

We adhere to the project's [Code of Conduct](CODE_OF_CONDUCT.md).
