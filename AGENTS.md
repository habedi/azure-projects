# AGENTS.md

This file provides guidance to coding agents working on Azure playground.

## Mission

Azure playground is a place for learning about and experimenting with Microsoft Azure services.
It provides a Python environment for exploring Azure authentication, storage, and other cloud workflows.

Priorities, in order:

1. Reproducible cloud experiments: document prerequisites, configuration, required roles, and cleanup steps.
2. Cloud safety and cost control: protect credentials, use least-privilege access, and avoid unintended resource creation or deletion.
3. Accessible experimentation: clear notebooks, modular Python code, and automated tests.
4. Small, clear changes: code edits fit the existing Python environment and repository structure.

## Core Rules

- Read affected code and nearby tests before editing. Prefer focused changes, existing helpers, and standard library features.
- Use English for code, comments, documentation, and tests.
- Support Python 3.14 or newer. Development uses Python 3.14, as specified in `.python-version`. Add type annotations to functions and follow code
  quality settings.
- Use uv and `uv.lock` for Python dependencies. Run `uv add PACKAGE` or `uv add --dev PACKAGE`, and update both `pyproject.toml` and `uv.lock` when
  changing dependencies. Use `uv run` for development commands.
- Keep notebooks clean and stripped of large binary outputs or sensitive data before committing.
- Keep comments focused on behavior that the code does not make clear.
- Azure playground source is MIT-licensed. Dependencies and Azure services retain their own licenses and terms. Keep license disclosures
  accurate, and do not commit credentials, access tokens, or connection strings.

## Writing Style

- Write in simple, plain English. Use short sentences and everyday words.
- Use Oxford commas in inline lists: "a, b, and c" not "a, b, c".
- Do not use em dashes. Restructure the sentence, or use a colon or semicolon instead.
- Avoid colorful adjectives and adverbs. Write "adjacency query" not "blazing adjacency query".
- Prefer noun phrases for checklist items over imperative verbs. Write "temp directory teardown" not "tear down the temp directory".
- Headings in Markdown files must be in title case: "Build from Source" not "Build from source". Minor words stay lowercase unless they are the first
  word: the articles (a, an, the), the coordinating conjunctions (and, but, or, nor, so, yet, for), and the short prepositions (in, on, at, to, by,
  of, up, as, from, with, into, over).
- Do not bold the lead-in of a list item. Write "Vector and set similarity: ..." not "**Vector and set similarity**: ...".
- Use sentence case for the lead-in of a list item. Write "Seed selection: ..." not "Seed Selection: ...". Proper nouns keep their capitals.
- Capitalize only the first part of a hyphenated compound: "Full-text Search" in a heading, "Breadth-first" at the start of a sentence, and
  "breadth-first search" elsewhere. Never write "Breadth-First".
- Start each sentence with a capital letter, capitalize proper nouns (Rust, Cypher, LMDB), and leave common nouns lowercase in the middle of a
  sentence.
- Write correct and complete sentences. Avoid made-up words.
- Do not use a colon in place of a verb. A colon may join two clauses inside a complete sentence, introduce the gloss of a list item, or introduce an
  enumeration. It must not turn a sentence into a label and a definition: write "Merges vector search seeds with text search seeds, then expands via
  BFS" rather than "Hybrid retrieval: merges vector search seeds with text search seeds".
- Use participial phrases and abbreviations sparingly.

## Repository Layout

- `tests/` contains automated unit tests and test suites.
- `pyproject.toml` defines project metadata, dependencies, and package configuration.
- `uv.lock` pins the Python dependency tree.
- `Makefile` provides development commands for installation, formatting, linting, testing, and hook management.
- `.pre-commit-config.yaml` defines Git hooks for formatting and quality checks before commits and pushes.
- `.github/workflows/tests.yml` runs automated tests across supported Python versions on continuous integration.
- `.env.example` documents local configuration without secrets.
- `README.md` documents the project overview, setup, safe experimentation, and learning resources.
- `CONTRIBUTING.md` describes contribution guidelines and the development workflow.

## Behavior to Preserve

- Keep credentials, local Azure CLI state, deployment outputs, and downloaded data out of version control.
- Prefer `DefaultAzureCredential` for Python authentication and managed identities for deployed workloads.
- Verify the target subscription, resource group, and region before cloud operations. Never deploy to production by default.
- Document potential charges and explicit cleanup steps. Limit deletion to resources created by the experiment.
- Close Azure clients and credentials promptly, using context managers where supported.
- Keep dependencies synchronized across `pyproject.toml` and `uv.lock`. Run dependency updates through uv.
- Support Python 3.14 or newer across shared utilities and test suites.
- Preserve Git hygiene by excluding `.venv/`, `.pytest_cache/`, `.ruff_cache/`, build artifacts, and local secrets.

## Development Workflow

Use the existing Make targets:

```bash
make install       # Install Python dependencies.
make test          # Run tests.
make format        # Format code with Ruff.
make setup-hooks   # Install Git hooks for pre-commit and pre-push.
make test-hooks    # Run Git hooks on all files.
make clean         # Remove caches and build artifacts.
```

Run checks relevant to the change. For application code changes, run formatting, linting, type checking, and the relevant tests before declaring the
work done.
Update `README.md` when behavior or setup changes. Report any checks that could not run.

## Testing Expectations

- Keep automated tests deterministic and independent of Azure credentials, live services, and network access. Use test doubles
  or mocks for Azure clients and authentication.
- Use temporary directories for tests that create or modify configuration files, downloaded blobs, or other artifacts.
- Cover configuration validation, authentication failures, Azure API errors, and resource cleanup when modifying those behaviors.
- Automated checks must not create billable resources or modify a real Azure subscription.
- Keep `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, coverage files, Jupyter checkpoints, and build artifacts out of commits and package
  distributions.
