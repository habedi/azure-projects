### 01: Blob Storage Round Trip

Create sandbox storage with OpenTofu, upload and download a small blob with Python,
verify its contents, and remove the temporary private container.

#### Prerequisites

- Python 3.14 or newer, uv, GNU Make, Azure CLI, and OpenTofu 1.8 or newer.
- A non-production public Azure subscription, an allowed region, and access to
  Azure management, identity, and public Blob endpoints.
- Permission to create and delete resource groups, storage accounts, and role
  assignments. Contributor alone cannot manage role assignments; ask an administrator
  for least-privilege access.
- The `Microsoft.Storage` provider already registered. Automatic registration is disabled.

From the repository root, install dependencies and sign in. Optional `nix develop`
provides all required tools.

```bash
make install
make login
```

For device login, use `make login LOGIN_ARGS=--use-device-code`.

#### Configure the Sandbox

```bash
cd experiments/01-blob-storage
cp infra/terraform.tfvars.example infra/terraform.tfvars
az account list --query '[].{name:name,id:id}' --output table
az ad signed-in-user show --query id --output tsv
```

Replace every placeholder in `infra/terraform.tfvars`:

- `subscription_id` is the sandbox subscription UUID.
- `location` is an allowed region, such as `northeurope`.
- `resource_group_name` is a new, dedicated name starting with `rg-azpg-01-`.
- `storage_account_name` is globally unique, starts with `azpg01`, uses lowercase
  letters and digits, and contains at most 24 characters.
- `principal_object_id` is the running identity's Entra object ID, not its client ID.
  The last command above returns it for the signed-in user.

Check provider registration with your subscription UUID. The result must be
`Registered`; otherwise, ask your administrator to register it.

```bash
az provider show --subscription YOUR_SUBSCRIPTION_ID \
  --namespace Microsoft.Storage --query registrationState --output tsv
```

The root `.env` is not used. Use the same CLI identity for deployment and Python.
`ARM_*` settings and `DefaultAzureCredential` may select other identities, so check
that they match `principal_object_id`. The runner also needs Reader access.

#### Run and Clean Up

```bash
make plan
make up
make run
make down
```

- `make plan` previews a dedicated resource group, Standard LRS storage account, and
  account-scoped Storage Blob Data Contributor assignment. Anonymous Blob access
  and shared-key authentication are disabled; HTTPS and TLS 1.2 or newer are required.
- `make up` previews changes and requires `yes`. Review the subscription, group,
  region, account, identity, and every change before approval.
- `make run` verifies the target against local state and asks for the resource group
  name before writing. Success prints `Blob round trip succeeded` and
  `Temporary container removed`.
- `make down` previews destruction and requires `yes`. It permanently removes the
  experiment infrastructure and remaining data. Never add unrelated resources to its group.

Storage and request charges may apply. Python removes only its temporary container,
not infrastructure, and cleanup is not guaranteed after failures or interruption.
Always run `make down` and verify the resource group is gone. If cleanup fails,
resolve the error and retry; inspect only the temporary container named by the runner.

Keep Git-ignored `infra/terraform.tfstate` and variable files secure. State can contain
secrets; retain it until teardown succeeds, and never share it or commit credentials.
Do not run concurrent infrastructure commands against the same resources. Keep
`infra/.terraform.lock.hcl` and the shared root `uv.lock` in version control.

#### Tests and Troubleshooting

From the repository root, `make test` runs all offline tests with coverage.
`make coverage` also generates HTML and XML reports. Neither command contacts Azure.

- Missing outputs usually mean `make up` has not completed or local state is missing.
- Storage `403` errors require checking identity, role propagation, and network rules.
- Role assignment errors require permission to manage assignments.
- Account name conflicts require a new globally unique name.
