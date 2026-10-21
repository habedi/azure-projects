## Contribution Guidelines

Thank you for considering contributing to this project!
Contributions are always welcome and appreciated.

### How to Contribute

Please check the [issue tracker](https://github.com/habedi/azure-projects/issues) to see if there is an issue
you would like to work on or if it has already been resolved.

#### Reporting Bugs

1. Open an issue on the [issue tracker](https://github.com/habedi/azure-projects/issues).
2. Include steps to reproduce the behavior, the Azure service involved, and relevant logs or screenshots.
3. Remove credentials, access tokens, connection strings, and personal data before sharing logs.

#### Suggesting Features

1. Open an issue on the [issue tracker](https://github.com/habedi/azure-projects/issues).
2. Provide details about the feature, its purpose, and potential implementation ideas.

### Submitting Pull Requests

- Make sure all tests pass before submitting a pull request.
- Write a clear description of the changes you made and the reasons behind them.

> [!IMPORTANT]
> It's assumed that by submitting a pull request, you agree to license your contributions under the project's license.

### Development Workflow

> [!IMPORTANT]
> If you're using an AI-assisted coding tool like Claude Code or Codex, make sure the AI follows the instructions in the [AGENTS.md](AGENTS.md) file.

#### Prerequisites

Install Python 3.14 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and GNU Make.

```bash
make install
```

Alternatively, you can use Nix to set up a development environment.

```bash
nix develop
```

#### Code Style

- Use the `make format` command to format the code.

#### Running Tests

- Use the `make test` command to run the full test suite with terminal coverage.
- Test discovery includes `tests/` and the test directories under `experiments/`.
- Use `make coverage` to also generate reports at `htmlcov/index.html` and `coverage.xml`.
- Keep tests deterministic and independent of Azure credentials, subscriptions, and network access.
- Prefer real parsing, temporary files, and real SDK clients over mocks.
- Use the shared in-memory Blob transport and temporary CLI programs for offline workflows.
  Small test doubles replace credential acquisition and external services, not SDK behavior.
  Network connections are blocked during tests. Never create cloud resources in automated tests.

#### Azure Experiments

- Document prerequisites, configuration, required roles, expected costs, and resource cleanup.
- Use a sandbox subscription and least-privilege access. Prefer `DefaultAzureCredential` and managed identities.
- Keep secrets in local environment variables or an appropriate secret store, never in tracked files or notebook outputs.
- Make resource creation repeatable, and make deletion explicit and limited to the experiment's resources.

#### See Available Commands

- Run `make help` to see all available commands for managing different tasks.

### Code of Conduct

We adhere to the project's [Code of Conduct](CODE_OF_CONDUCT.md).
