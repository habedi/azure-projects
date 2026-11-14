# Variables
PYTHON      ?= python3
PIP         ?= pip3
DEP_MNGR    ?= uv
DOCS_DIR   ?= docs
PROJECT    ?= 01-blob-storage
PROJECTS   ?= projects/[0-9][0-9]-*

# Directories and files to clean
CACHE_DIRS  = .mypy_cache .pytest_cache .ruff_cache
COVERAGE    = .coverage htmlcov coverage.xml
DIST_DIRS   = dist junit
TMP_DIRS   = site

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show help messages for all available targets
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' Makefile | \
	awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

# Setup and Installation
.PHONY: setup
setup: ## Install system dependencies and dependency manager
	sudo apt-get update
	sudo apt-get install -y python3-pip
	$(PIP) install --upgrade pip
	$(PIP) install $(DEP_MNGR)

.PHONY: install
install: ## Install Python dependencies
	$(DEP_MNGR) sync --all-groups --all-extras

# Azure Authentication and Configuration
.PHONY: login
login: ## Sign in to Azure CLI (use LOGIN_ARGS=--use-device-code if needed)
	az login $(LOGIN_ARGS)

.PHONY: check-config
check-config: ## Check .env settings and Azure access with read-only requests
	$(DEP_MNGR) run python scripts/check_config.py

# Quality and Testing
.PHONY: test
test: ## Run all tests
	$(DEP_MNGR) run pytest -n auto --cov=. --cov-report=term-missing

.PHONY: coverage
coverage: ## Run all tests and save coverage reports
	$(DEP_MNGR) run pytest -n auto --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml

.PHONY: format
format: ## Format Python source files
	$(DEP_MNGR) run ruff format

.PHONY: setup-hooks
setup-hooks: ## Install Git hooks (pre-commit and pre-push)
	$(DEP_MNGR) run pre-commit install --hook-type pre-commit
	$(DEP_MNGR) run pre-commit install --hook-type pre-push
	$(DEP_MNGR) run pre-commit install-hooks

.PHONY: test-hooks
test-hooks: ## Test Git hooks on all files
	$(DEP_MNGR) run pre-commit run --all-files

.PHONY: test-project
test-project: ## Run plan, up, run, and down for a project (PROJECT=01-blob-storage or PROJECT=all)
	@set -eu; \
	if [ "$(PROJECT)" = "all" ]; then \
		set -- projects/[0-9][0-9]-*; \
	elif [ -f "$(PROJECT)/Makefile" ]; then \
		set -- "$(PROJECT)"; \
	elif [ -f "projects/$(PROJECT)/Makefile" ]; then \
		set -- "projects/$(PROJECT)"; \
	else \
		echo "Project not found: $(PROJECT)" >&2; \
		exit 1; \
	fi; \
	for dir in "$$@"; do \
		( \
			set -eu; \
			echo "==> Testing project: $$dir"; \
			$(MAKE) --no-print-directory -C "$$dir" plan; \
			$(MAKE) --no-print-directory -C "$$dir" up; \
			trap 'rc=$$?; $(MAKE) --no-print-directory -C "$$dir" down || rc=$$?; exit $$rc' 0; \
			trap 'exit 130' INT; \
			trap 'exit 143' TERM; \
			$(MAKE) --no-print-directory -C "$$dir" run \
		); \
	done

.PHONY: destroy-all
destroy-all: ## Run down across all projects to destroy provisioned resources
	@set -eu; \
	status=0; \
	for dir in $(PROJECTS); do \
		if [ ! -f "$$dir/Makefile" ]; then \
			echo "Project not found: $$dir" >&2; \
			status=1; \
			continue; \
		fi; \
		echo "==> Destroying resources in: $$dir"; \
		$(MAKE) --no-print-directory -C "$$dir" down || status=$$?; \
	done; \
	exit $$status


# Maintenance
.PHONY: clean
clean: ## Remove caches and build artifacts
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf $(CACHE_DIRS) $(COVERAGE) $(DIST_DIRS) $(TMP_DIRS)
