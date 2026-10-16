TOFU       ?= tofu
DEP_MNGR   ?= uv

.DEFAULT_GOAL := help
.NOTPARALLEL:

.PHONY: help plan up run down
help: ## Show help messages for all available targets
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' Makefile | \
	awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

plan: ## Initialize OpenTofu and preview the experiment resources
	$(TOFU) -chdir=infra init -input=false
	$(TOFU) -chdir=infra plan -input=false

up: ## Review a saved plan and approve resource creation with exactly yes
down: ## Review a saved destruction plan and approve cleanup with exactly yes
up down:
	@set -eu; \
	case "$@" in \
		up) plan=apply.tfplan; destroy=; action='Create or update';; \
		down) plan=destroy.tfplan; destroy=-destroy; action='Destroy';; \
	esac; \
	trap 'rm -f -- "infra/$$plan"' 0; \
	trap 'exit 130' INT; \
	trap 'exit 143' TERM; \
	$(TOFU) -chdir=infra init -input=false; \
	$(TOFU) -chdir=infra plan -input=false $$destroy -out="$$plan"; \
	$(TOFU) -chdir=infra show -no-color "$$plan"; \
	$(TOFU) -chdir=infra show -json "$$plan" | \
		$(DEP_MNGR) run --project ../.. python -c 'import json, sys; p = json.load(sys.stdin)["variables"]; print("\nSaved plan scope:"); [print(k + ": " + p[k]["value"]) for k in ("subscription_id", "resource_group_name", "location", "storage_account_name", "principal_object_id")]'; \
	printf '\n%s the resources in this saved plan? Type exactly yes: ' "$$action"; \
	if ! IFS= read -r answer || [ "$$answer" != yes ]; then \
		printf '\nApproval not given. No saved plan was applied.\n'; \
		exit 1; \
	fi; \
	$(TOFU) -chdir=infra apply -input=false "$$plan"

run: ## Run the Blob round-trip using the shared root Python environment
	$(DEP_MNGR) run --project ../.. python run.py
