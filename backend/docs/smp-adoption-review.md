---
cursor:
  subagentId: "bc-283e9dcb-ff1e-5189-8a62-56c8d6e5fb99"
---

# SMP architecture review: what house-scanning-server should adopt

2026-09-26. Read-only review of `huntertcarver/Software-Manufacturing-Plant` (SMP, private, `main` at `38b696f`, local clone `~/Software-Manufacturing-Plant`, `git pull --ff-only` = already up to date) against `docs/server-pipeline-build-plan.md` (WS-00, WS-01, WS-11, WS-12), `docs/hosted-architecture.md` and `docs/project-context.md`. SMP paths below are relative to the SMP repo root. The server repo was read only through the GitHub API (`gh api repos/huntertcarver/house-scanning-server/...`): it has `pyproject.toml` (uv, ruff, pytest, Python 3.12) and one workflow, `.github/workflows/ci.yml` (lint + test), and no `server/`, Dockerfile, Modal app or Terraform yet. A previous agent's draft at this path was treated as untrusted; every claim below was re-checked against the files cited.

## 1. Summary verdict

- **Adopt SMP's GCP identity and state plumbing now; skip its factory.** Copy four small Terraform modules (GitHub WIF, GitHub service account, GCS state bucket, project APIs + budget) and the 21-line `auth-gcp-oidc` composite action. Write two modules SMP lacks (private capture bucket, Modal OIDC provider). Nothing else in SMP is on the demo's critical path.
- **SMP is Node/TypeScript only in practice.** Every scaffold topology, starter, Dockerfile and reusable delivery workflow assumes `npm` (`tools/render_product_scaffold.py`, `.github/workflows/*-default.yaml`). Python appears only in SMP's own tooling and one scheduled job. Keep the server's uv + ruff + pytest CI and add jobs to it; do not call SMP's reusable workflows.
- **Modal fits SMP's deploy stage, not its provisioning stage.** SMP's workflows already treat deploy as a free-form `deploy_command` behind a GitHub `environment:`, so `uv run modal deploy` slots in. Modal has no Terraform provider, so Terraform owns only the GCP side (bucket, service accounts, WIF, budget; later Secret Manager, Cloud Run, Pub/Sub). Modal containers can reach GCS keylessly via Modal OIDC + a second WIF provider.
- **Three SMP defaults conflict with the plan and need a written exception:** Spanner-first (`SMP-DEC-006`) vs the plan's Postgres; prod-only launch (`SMP-DEC-018`) vs a dev/prod split; GCP-native-only observability (`OBS-DEC-009`) vs the plan's Sentry. Recommendation: Cloud SQL Postgres at migration, `dev` + `prod` from day one (Modal environments are free), structured JSON logs now and OTel to Cloud Logging at migration with Sentry deferred.
- **Adopt at GCP migration:** `cloud_run_service` (needs GPU fields added), `pubsub_topology`, `artifact_registry_repo`, `build-publish-container`, `gsm_secret_namespace` + `service_identity_secret_access`, `logging_retention_baseline`. SMP's SLO alert call site is broken and should not be copied.
- **Skip outright:** product factory and scaffold renderer, shared product-serving baseline (Spanner, Redis, Tasks, Workflows), WorkOS, Stripe, API Gateway/LB/DNS/TLS edge stack, KMS, HLD/LLD governance and the 24 validators.

## 2. SMP overview (with paths)

**Purpose and shape.** SMP is an "architecture source of truth" plus a "shared reference implementation spine", explicitly not a product monorepo (`README.md` §1, §5; `AGENTS.md`). The parent HLD (`platform/software-manufacturing-plant/HLD.md`, v0.15.0, In-Review) sets the vision "develop software on any stack, package workloads as containers, and deploy ... on GCP" and 40 normative decisions `SMP-DEC-001..040`. 12 child HLDs and 38 LLDs sit under `platform/software-manufacturing-plant/<domain>/`. Repo: 430 commits since 2026-02-16; the last human change landed 2026-06-14 and every commit since is one automated daily job ("Persist ChatGraph X digest history", 171 commits), so the design has been static for about 3.5 months.

**How it manufactures software.**
- Golden path (`SMP-DEC-015`): product contract in repo -> scaffold from template catalog -> Terraform via GitHub Actions -> build/test -> deploy -> verify -> auto-promote.
- `tools/render_product_scaffold.py`: `TOPOLOGY_SOURCE_MAP` has three topologies (`flagship_multi_platform`, `web_only`, `frontend_spa`), all mapping to Node starters under `shared-assets/application/*` and `shared-assets/frontend/*`. It copies 23 `SHARED_ASSET_DEPENDENCIES` directories into the product repo, renders `templates/product-repo/common/*.tmpl` (README, runtime env contract, secret and Spanner wiring notes, `validate-factory-scaffold.workflow.yaml.tmpl`) and writes per-env `flagship-delivery-<env>.yaml` callers that `uses: huntertcarver/Software-Manufacturing-Plant/.github/workflows/flagship-multi-platform-default.yaml@main`. Its generated tfvars turn on shared Spanner, Redis, Pub/Sub, Tasks, Workflows and domain edge (`build_stack_tfvars`).
- `tools/factory_common.py`: `SHARED_ENVIRONMENTS = ["prod"]`, `DEFAULT_PRODUCT_ENVIRONMENTS = ["prod"]`, `SHARED_BACKEND_BUCKETS` (`smp-substrate-tfstate-{substrate,dev,stage,prod}`).
- `tools/bootstrap_product_factory.py --dry-run` checks factory readiness (GitHub App or PAT, disposable test repos, GSM secrets; `manifests/product-factory/product-factory.example.yaml`). `tools/bootstrap_github_actions.py` creates GitHub environments and upserts repo/environment variables.

**Layout.** `platform/` (HLD/LLD, guidebooks, asset catalog), `shared-assets/` (Terraform modules, Node starters, contracts), `manifests/` (schemas, contracts, `product-launch/runtime-env-contract.yaml`), `templates/`, `tools/` (24 validators under `tools/validators/`, bootstrap scripts, `x_digest/`), `examples/`, `products/`, `deployment-integrations/`, `reports/` (generated), `tests/fixtures/`.

**Terraform.**
- Versions: Terraform `>= 1.6.0`, `hashicorp/google` and `google-beta` `>= 5.0.0` (`shared-assets/delivery/terraform-root-stack-starter/versions.tf`); lock file pins provider `7.28.0` (`.terraform.lock.hcl`); CI pins Terraform `1.8.5` (`.github/actions/setup-toolchain/action.yaml`).
- State: empty `backend "gcs" {}` (`backend.tf`), configured at `terraform init -backend-config=bucket=... -backend-config=prefix=...` (`.github/actions/terraform-stack/action.yaml`). Conventions: bucket `smp-substrate-tfstate-<environment>`, key `<product>/<environment>`, host project `smp-shared-substrate` (`shared-assets/delivery/terraform-governance-pack/backend-conventions.yaml`). The scaffold actually renders prefix `products/<product>/<env>` (`render_product_scaffold.py` line 599), so the two conventions disagree.
- Root stacks: `shared-baseline-substrate` (state buckets, GitHub WIF pool/provider, GitHub SA in `smp-shared-substrate`); `shared-baseline-env` (one shared project per env with APIs, WIF, SA, Artifact Registry, budget; only `prod` active, `env/{dev,stage,prod}.tfvars`); `shared-baseline-bootstrap` (legacy); `product-serving/shared-baseline` (Spanner 100 PU, Memorystore Redis BASIC 1 GB, Pub/Sub, Tasks, Workflows, logging, SLO); `edge/shared-baseline`; `terraform-root-stack-starter` (per-product composition: four Cloud Run services named `${product_id}-${environment}-{web,api,worker,webhook}`, secrets, runtime SAs, KMS, Cloud Armor policy, log bucket, SLO alert, optional Spanner/Redis/GCS/Pub/Sub/Tasks/Workflows/API Gateway/global LB/DNS/TLS).
- Modules relevant here: `delivery/terraform-governance-pack/modules/{github_actions_workload_identity,github_actions_service_account,gcs_backend_baseline,gcp_project_env_baseline,artifact_registry_repo}`, `security/secret-handoff-baseline/modules/{gsm_secret_namespace,service_identity_secret_access,kms_keyring_key}`, `data/object-storage-cdn-baseline/modules/object_storage_cdn`, `runtime/cloud-run-baseline-template/modules/cloud_run_service`, `eventing/pubsub-contracts/modules/pubsub_topology`, `observability/{logging-retention-baseline,slo-alert-baseline}/modules/*`.

**Secrets and auth.**
- Naming contract `secret/<product>/<env>/<resource>` (`security-trust-platform/lld/secrets-and-kms-separation-LLD.md` line 61; `SCT-DEC-007`). `gsm_secret_namespace/locals.tf` flattens `/` to `--`, so IDs are `secret--<product>--<env>--<name>`, replication `auto`, labels `product`, `environment`, `managed_by`, `resource`. An optional `initial_value` creates a version through Terraform (`main.tf`), which puts the secret in state.
- `service_identity_secret_access` creates SAs, project role bindings and per-secret `roles/secretmanager.secretAccessor`. Cloud Run injects via `value_source.secret_key_ref` (`cloud_run_service/main.tf`); full resource names are the preferred reference style (`manifests/product-launch/runtime-env-contract.yaml`, `shared_rules`).
- CI auth: GitHub OIDC -> WIF -> SA impersonation with `google-github-actions/auth@v2` (`.github/actions/auth-gcp-oidc/action.yaml`). Provider condition `assertion.repository == "<repo>" && (assertion.ref == "refs/heads/main")`, mapping `google.subject`, `actor`, `aud`, `ref`, `repository` (`github_actions_workload_identity/{locals,main}.tf`); default `allowed_refs = ["refs/heads/main"]`, default `workload_identity_provider_id = "architecture-design-decisions"` (`variables.tf`). SA gets `roles/iam.workloadIdentityUser` for `principalSet://.../attribute.repository/<repo>` (`github_actions_service_account/main.tf`).
- Default GitHub SA roles are broad: substrate `roles/viewer`, `iam.serviceAccountAdmin`, `iam.workloadIdentityPoolAdmin` (`shared-baseline-substrate/variables.tf`); env adds `artifactregistry.admin`, `resourcemanager.projectIamAdmin`, `serviceusage.serviceUsageAdmin` (`shared-baseline-env/variables.tf`).
- Local: no `gcloud` anywhere; tooling reads `GOOGLE_CREDENTIALS` (SA JSON or path) else ADC `google.auth.default()` (`tools/x_digest/runtime_secrets.py`; `AGENTS.md` gotchas). No impersonation helper scripts. `DLV-DEC-002` forbids long-lived JSON keys for Terraform execution in CI.

**CI/CD.**
- Composite actions: `auth-gcp-oidc`, `build-publish-container` (docker build/push, tag `:${{ github.sha }}`, outputs digest-pinned `image_ref`), `setup-toolchain` (Python 3.12 + pip validators, Terraform 1.8.5), `terraform-stack` (`fmt -check`, `init -backend=false`, `validate`; with `run-apply=true`: `init` with backend config, `plan -out=tfplan`, `apply -auto-approve tfplan`, `output -json`), `run-validators`.
- Reusable `workflow_call` archetype workflows `core-api-default`, `worker-default`, `web-app-default`, `payment-webhook-default`, `frontend-spa-default`, composed by `flagship-multi-platform-default`. Stages: `validate` (`setup-node` + `install_command` default `npm ci` + `test_command` default `npm run typecheck`), `build` (`npm run build`, optional container publish), `deploy` (gated by `environment: ${{ inputs.environment }}`, optional `auth-gcp-oidc`, free-form `deploy_command`), `verify` (free-form `verify_command`), `post-deploy-compliance` (writes image ref/digest to `$GITHUB_STEP_SUMMARY`).
- Terraform: `_shared-terraform-bootstrap-template.yaml` (validators -> optional WIF auth -> `terraform-stack` -> upload `tfplan`, `tfplan.json`, `terraform-outputs.json` -> summary; concurrency `shared-bootstrap-<env>`, `cancel-in-progress: false`); `bootstrap-product-infra.yaml` wraps it with `run_apply`; `validate-shared-*.yaml` run on `pull_request` with `run-apply: "false"`; `bootstrap-shared-baseline-prod.yaml` runs on `workflow_run` after `bootstrap-shared-substrate` or `workflow_dispatch`.
- `promote-release.yaml` / `rollback-release.yaml`: free-form `deploy_command` / `rollback_command` plus `verify_command`, `environment: <target>`.
- The one Python job, `chatgraph-x-daily-digest.yaml`: cron + dispatch, `environment: prod`, `setup-toolchain` -> `pip install -r tools/x_digest/requirements.txt` -> checks `vars.GCP_WIF_PROVIDER` / `vars.GCP_SERVICE_ACCOUNT_EMAIL` -> `auth-gcp-oidc` -> Python reads GSM -> commits a history file back to `main`. Closest precedent for a Python service; it has run daily for months, which also shows GitHub environments work on Hunter's private repos.
- Governance: no manual production approval by default (`DLV-DEC-005`, `delivery-control-plane-LLD.md`); human gates only for billing, destructive infra, domain cutover, exception activation, break-glass (`manifests/onboarding/approval-gates.yaml`). Mandatory gates listed in `DLV-DEC-007` (tests, IaC policy, vuln check, health verify, observability smoke) are not implemented by the workflows beyond `validate`/`verify`.

**Shared-asset conventions.**
- Identity: WorkOS (`shared-assets/identity/{auth-middleware,workos-org-model,sso-scim-onboarding}`; `SMP-DEC-010`). Entitlements: `tenant-entitlements/{canonical-contracts,enforcement-adapter}`. Payments: Stripe (`payments/*`).
- Observability: `observability/telemetry-bootstrap/src/index.ts` only builds resource attributes `product`, `service.name`, `deployment.environment` (env typed `dev|stage|prod`); `manifests/observability/telemetry-config.example.yaml` sets `sampling_rate: 0.1`; `logging_retention_baseline` creates a log bucket with `retention_days` default 30; `slo_alert_baseline` is a generic `google_monitoring_alert_policy` wrapper. Backends are GCP-native only, external backends exception-only (`OBS-DEC-001..004`, `OBS-DEC-009`). Sentry is never mentioned; Datadog was the v0.6.0 default and was removed in v0.7.0 (`HLD.md` change log).
- Environment naming: every service type requires `SMP_PRODUCT_ID`, `SMP_ENVIRONMENT`, `SMP_SERVICE_NAME` (`manifests/product-launch/runtime-env-contract.yaml`); starters read them (`shared-assets/application/*/src/server.ts`); the root stack injects `SMP_SERVICE_NAME` (`services.tf`). Labels: `product`, `env`, `owner`, `data_class` are mandatory for migration readiness (`HLD.md`, Graduation Triggers); modules add `managed_by = "terraform"`; Cloud Run template metadata requires `product, environment, owner, runbook_url` (`runtime/cloud-run-baseline-template/template.yaml`). Resource names follow `${product_id}-${environment}-<surface>`.
- Local dev: there are no `local:env` or `local:stack` scripts (repo-wide grep including hidden dirs: 0 hits). Starters only expose `npm run build|start|typecheck|dev`. Local verification is `python3 tools/validators/run_all.py` plus `terraform fmt -check && terraform init -backend=false && terraform validate` per stack (`AGENTS.md`).
- Coding standards: `AGENTS.md` (toolchain, validators, gotchas) and the `README.md` document contract (metadata header, doc types, status lifecycle, agreed-vs-discussion separation, change acceptance gate). No code linters or formatters are configured (`tools/linters/` holds only a README).
- Testing: starter `test` scripts are placeholders (`AGENTS.md`); the only unit tests are `unittest` in `tools/x_digest/tests/test_pipeline.py`. Grep for `pytest`, `ruff`, `uv`, `pyproject`: 0 hits.
- Language support: "any stack" in the HLD, but Node 22/TypeScript everywhere a service is built or deployed; Python 3.12 is used only for validators, bootstrap tools and the digest job. No Python starter, Dockerfile or workflow exists.
- Containers and Cloud Run: starters are `node:22-alpine` multi-stage images exposing 8080 (`shared-assets/application/worker-task-starter/Dockerfile`). `cloud_run_service` defaults: 1 CPU, 512Mi, 0–3 instances, 300 s timeout, ingress `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`, latest-revision traffic; no GPU, `node_selector`, VPC, volumes, Cloud SQL or startup-probe fields (`variables.tf`, `main.tf`). Runtime policy: Cloud Run default, GKE/GCE by exception, workloads classified first (`RTM-DEC-001/002/005`); the rubric and exception LLDs never mention GPUs (grep `gpu|nvidia|accelerator`: 0 hits).
- Cost controls: `gcp_project_env_baseline` creates a `google_billing_budget` with 50% and 90% threshold rules and no notification channel (`main.tf`); per-env monthly amounts live in `shared-baseline-env/env/*.tfvars`; cost labels (`SMP-DEC-033`); shared-first tenancy (`SMP-DEC-035/036`); prod-only launch (`SMP-DEC-018`); pricing baselines in `reports/cloud-resource-pricing-*.md`.

## 3. Practice-by-practice fit

"Now" = hackathon on Modal (WS-00/01/11/12). "Migration" = the GCP move (Cloud Run, Pub/Sub, Postgres). "Skip" = do not adopt.

| SMP practice (path) | Verdict | Reason |
|---|---|---|
| Terraform-first with a GCS backend (`SMP-DEC-003`, `backend.tf`, `backend-conventions.yaml`) | **Now** | The bucket, runtime SA and WIF are needed for signed URLs anyway; codifying them makes the GCP move config-only. Keep it to one root stack. |
| `github_actions_workload_identity` module | **Now** | Keyless `terraform plan/apply` and E2E from Actions. Override `workload_identity_provider_id` (default is `architecture-design-decisions`). |
| `github_actions_service_account` module | **Now**, narrow roles | Pattern fits; SMP's default role sets (`roles/viewer`, IAM admin roles) are too broad for this repo. |
| `gcs_backend_baseline` module | **Now** | Versioned, public-access-prevented state bucket. Use as is. |
| `gcp_project_env_baseline` (`create_project = false`) | **Now** | Enables APIs and sets a budget with 50/90% alerts. The only SMP cost control that applies to GCS/egress spend. |
| `auth-gcp-oidc` composite action | **Now** (copy) | 21 lines, stack-neutral. |
| `terraform-stack` action, validate step only | **Now** | `fmt -check`, `init -backend=false`, `validate` on every PR. Its `apply -auto-approve` path stays manual-dispatch only. |
| Second WIF provider for Modal OIDC (not in SMP) | **Now**, write it | Modal injects `MODAL_IDENTITY_TOKEN` (issuer `https://oidc.modal.com`, `aud` `oidc.modal.com`, claims `workspace_id`, `environment_name`, `app_name`, `function_name`). No SA key in Modal Secrets. Fallback if it eats more than an hour: SA key in a Modal Secret, documented as debt. |
| `object_storage_cdn` bucket module | **Skip**; write `capture_bucket` | Forces versioning (deleted captures persist as noncurrent versions), defaults `enable_cdn = true`, only a Delete rule. Captures need no versioning, Nearline at 30 d, 7-day delete for unfinalized prefixes, CORS, `data_class = pii`. |
| Secret naming `secret/<product>/<env>/<name>` (`SCT-DEC-007`, `gsm_secret_namespace/locals.tf`) | **Now** (names only) | Costs nothing; migration becomes a copy, not a rename. |
| `gsm_secret_namespace` + `service_identity_secret_access` | **Migration** | Modal Secrets are the runtime store now (plan WS-12); running both doubles work. Never pass `initial_value` (secret lands in state). |
| `SMP_PRODUCT_ID` / `SMP_ENVIRONMENT` / `SMP_SERVICE_NAME` contract | **Now** | Free, gives `config.py` a real contract and consistent log keys. Rename trivially if Base owns the repo later. |
| Labels `product, env, owner, data_class` + `managed_by` | **Now** | Budget attribution and migration readiness; captures are personal data. |
| Reusable archetype workflows (`*-default.yaml`) | **Skip**, copy the stage shape | `validate` is Node-only; cross-repo `uses:` of a private repo needs SMP's Actions access setting and pins the server to `@main` of another repo. Mirror validate / deploy / verify / summary locally. |
| GitHub `environment:` gating (`dev`, `prod`) | **Now** | Holds `MODAL_TOKEN_*` per environment; proven on Hunter's private repos by the digest job. |
| Promote/rollback by immutable ref (`promote-release.yaml`, `RTM-DEC-008`) | **Now**, lightweight | `modal deploy` of a pinned SHA via `workflow_dispatch` is the rollback; record SHA and app URL in the step summary. |
| Prod-only default (`SMP-DEC-018`, `RTM-DEC-006`, `factory_common.py`) | **Skip** (exception) | Modal environments are free; builders iterate on `dev` while the demo stays on `prod`. SMP is itself inconsistent here (`DLV-DEC-005` says `dev -> stage -> prod`). |
| Prod/non-prod never co-mingled (`HLD.md`, Tenancy guardrail) | **Now** with exception | One GCP project, env-suffixed buckets and SAs for the hackathon; separate projects only if Base requires it at migration. |
| Spanner-first, shared product-serving baseline (`SMP-DEC-006`, `product-serving/shared-baseline`) | **Skip** | Relational state is small and the plan says Postgres; Spanner 100 PU and Redis have monthly floors. Write a deployment-integration exception like `deployment-integrations/chatgraph_data_plane_on_software-manufacturing-plant.md` does for Neo4j. |
| `cloud_run_service` module | **Migration**, extend | Fits the API and reviewer. The pipeline needs `nvidia.com/gpu` limit, `node_selector { accelerator = "nvidia-l4" }`, `gpu_zonal_redundancy_disabled`, >= 4 CPU / 16Gi, longer timeout, `cpu_idle = false`, a Cloud SQL volume. None exist in the module. |
| `artifact_registry_repo` + `build-publish-container` | **Migration** | Modal builds its own image now. Needed when Cloud Run pulls from Artifact Registry; the digest-pinned output matches `DLV-DEC-008`. |
| `pubsub_topology` (DLQ, push) | **Migration** | Matches the plan's stage topics with dead-letter; stage table stays in Postgres. |
| OTel + GCP-native backends (`OBS-DEC-001..004`, `telemetry-bootstrap`) | **Migration**; JSON logs **now** | Now: one JSON line per event with `capture_id`, `run_id`, `stage`, `service.name`, `deployment.environment`. OTel exporter to Cloud Trace/Logging at migration. Sentry needs an `OBS-DEC-009` exception note. |
| `logging_retention_baseline` (30 d) | **Migration** | One resource; set retention to what Base's data-retention answer says. |
| `slo_alert_baseline` | **Migration**, rewrite call site | The root stack's only policy filters `resource.type="cloud_run_revision"` with no `metric.type` and HCL-escaped quotes (`observability.tf`); it is a sketch. Alert on time-to-verdict p95, stage failures, VLM cost per house instead. |
| Product factory, scaffold renderer, GitHub App bootstrap, validators, HLD/LLD metadata rules | **Skip** | Built for Node web products with WorkOS and Stripe; hours of setup with no benefit to a Python pipeline. |
| WorkOS, Stripe, API Gateway / global LB / Cloud Armor / DNS / TLS / KMS (`edge.tf`, `security.tf`) | **Skip** (edge at most at migration) | Demo auth is a static bearer token; production auth is Base's IdP JWT (hosted-architecture §1 row 8). |
| Doc governance: change log + exception notes | **Now**, minimal | Keep `docs/components/deploy.md` with an "SMP exceptions" list (Postgres, dev/prod, Sentry, GPU runtime). Skip metadata headers and validators. |

## 4. Concrete adoption recipe for `house-scanning-server`

The server repo already has uv, ruff (line length 120, py312) and pytest in `.github/workflows/ci.yml`; keep all of it. Everything below is additive and maps to a workstream. Copy SMP modules into the repo (SMP is private; a `git::` module source would make `terraform init` need a PAT). Put a line `SOURCE: huntertcarver/Software-Manufacturing-Plant@38b696f <path>` in each copied module's README.

### 4.1 Files to add

| Path in server repo | Source | WS |
|---|---|---|
| `infra/terraform/modules/github_wif/` | copy `shared-assets/delivery/terraform-governance-pack/modules/github_actions_workload_identity/*` | WS-12 |
| `infra/terraform/modules/github_sa/` | copy `.../modules/github_actions_service_account/*` | WS-12 |
| `infra/terraform/modules/state_bucket/` | copy `.../modules/gcs_backend_baseline/*` | WS-12 |
| `infra/terraform/modules/project_baseline/` | copy `.../modules/gcp_project_env_baseline/*` | WS-12 |
| `infra/terraform/modules/capture_bucket/` | **new** (shape of `object_storage_cdn`, no CDN, no versioning) | WS-01 |
| `infra/terraform/modules/modal_wif/` | **new**: pool + OIDC provider + runtime SA bindings | WS-01/12 |
| `infra/terraform/{versions,backend,providers,variables,locals,main,outputs}.tf` | root stack in the file layout of `terraform-root-stack-starter` | WS-12 |
| `infra/terraform/env/{dev,prod}.tfvars` | new | WS-12 |
| `.github/actions/auth-gcp-oidc/action.yaml` | copy verbatim | WS-11/12 |
| `.github/workflows/{infra,deploy,gpu-bench,e2e}.yml` and additions to `ci.yml` | new, section 4.3 | WS-11/12 |
| `server/common/config.py` | new: reads section 4.4's contract, raises on missing required vars | WS-00 |
| `docs/components/deploy.md` | rebuild commands, GitHub/Modal config names, SMP exceptions list | WS-12 |

### 4.2 Terraform root: resources and variables

One GCP project (Hunter names it; `hss` is the resource prefix assumed below), region `us-central1`, `environment` in `{dev, prod}`, one state prefix per env.

- **Bootstrap order:** Hunter runs the first `terraform apply` from his Mac with ADC (project owner), because the GitHub WIF pool does not exist yet. State starts local, then `terraform init -migrate-state -backend-config=bucket=hss-tfstate -backend-config=prefix=house-scanning/<env>`. After that, `infra.yml` applies via WIF. (Alternative: reuse `smp-substrate-tfstate-prod` with prefix `house-scanning/<env>`; needs a bucket IAM change in SMP's substrate stack.)
- **`project_baseline`:** `create_project = false`, `environment`, `required_apis = {storage, iam, iamcredentials, sts, secretmanager, billingbudgets, cloudresourcemanager, serviceusage}.googleapis.com`, `monthly_budget_amount = 50`, `billing_account` from tfvars. Budget thresholds 50% and 90% come from the module. The Modal $200 cap is set in the Modal workspace settings, not here.
- **`state_bucket`:** `bucket_name = "hss-tfstate"`, `location = "US"`, `admin_members = [GitHub SA]` (module default versioning on).
- **`capture_bucket`** (new) creates `hss-captures-<env>`: regional `us-central1`, uniform access, `public_access_prevention = "enforced"`, versioning off, lifecycle `SetStorageClass NEARLINE` at age 30 d and `Delete` at age 7 d with `matches_prefix = ["captures/"]` for unfinalized captures (the app moves finalized captures under a second prefix or sets `custom_time`; hosted-architecture §2.2 Limits), CORS for the reviewer origin, labels `{product = "house-scanning", env, owner = "huntertcarver", data_class = "pii", managed_by = "terraform"}`.
- **`github_wif`:** `repository = "huntertcarver/house-scanning-server"`, `workload_identity_pool_id = "github-actions"`, `workload_identity_provider_id = "house-scanning-server"`, `allowed_refs = ["refs/heads/main"]`.
- **`github_sa`:** `service_account_id = "hss-gha-<env>"`, `project_roles = {roles/storage.admin, roles/iam.serviceAccountAdmin, roles/iam.workloadIdentityPoolAdmin, roles/serviceusage.serviceUsageAdmin, roles/secretmanager.admin}` and `roles/billing.costsManager` on the billing account only if the budget is Terraform-managed from CI. No `roles/viewer`, no `projectIamAdmin`.
- **`modal_wif`** (new): pool `modal`, provider `modal-<env>` with `issuer_uri = "https://oidc.modal.com"`, `allowed_audiences = ["oidc.modal.com"]` (Modal's `aud` is fixed, so the GCP default audience must be overridden), mapping `google.subject = assertion.sub`, `attribute.workspace_id`, `attribute.environment_name`, `attribute.app_name`; condition `attribute.workspace_id == "<from modal token info>" && attribute.environment_name == "<env>"`. Runtime SA `hss-runtime-<env>` with bucket-level `roles/storage.objectAdmin` on `hss-captures-<env>`, `roles/iam.workloadIdentityUser` for `principalSet://.../attribute.environment_name/<env>`, and `roles/iam.serviceAccountTokenCreator` on itself (V4 signed URLs from federated credentials sign through IAM `signBlob`; there is no private key).
- **Outputs** (become GitHub variables and Modal config): `github_wif_provider_name`, `github_sa_email`, `modal_wif_audience` (the `//iam.googleapis.com/projects/.../providers/modal-<env>` string), `runtime_sa_email`, `capture_bucket`.

### 4.3 CI workflows

- **`ci.yml` (extend):** keep `lint` and `test`; add `fixtures` (`uv run python -m server.eval.cli validate` on the golden fixtures, WS-00 "done when"), `terraform` (Terraform 1.8.5 via `hashicorp/setup-terraform@v3`, then the "Terraform validate" step of `.github/actions/terraform-stack/action.yaml` in `infra/terraform`), and `modal-check` (`uv run python -c "import server.modal_app"` to catch image and decorator errors without deploying).
- **`infra.yml`:** `workflow_dispatch` with inputs `env` (`dev|prod`) and `apply` (bool); `environment: ${{ inputs.env }}`; `permissions: id-token: write, contents: read`; `./.github/actions/auth-gcp-oidc` with `vars.GCP_WIF_PROVIDER` and `vars.GCP_SERVICE_ACCOUNT_EMAIL`; `plan -out=tfplan`, upload `tfplan.json`; `apply tfplan` only when `apply == true`; concurrency `infra-${{ inputs.env }}`, `cancel-in-progress: false` (as in `_shared-terraform-bootstrap-template.yaml`).
- **`deploy.yml`:** push to `main` touching `server/**`, `pyproject.toml`, `uv.lock` deploys `dev`; `workflow_dispatch` with `env=prod` and `ref=<sha>` is promotion and rollback. Steps: `astral-sh/setup-uv@v6`, `uv sync --frozen`, `uv run modal deploy -m server.modal_app -e ${{ inputs.env }}` with `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` from the GitHub environment; verify with `curl -fsS $CAPTURE_API_URL/v1/healthz` then the replay client on one small fixture with a 10-minute cap (WS-11); summary writes env, SHA and app URL (mirrors `post-deploy-compliance`); concurrency `deploy-${{ env }}`.
- **`gpu-bench.yml`:** `workflow_dispatch` only, inputs `gpu` (`L4|A10G|A100-40GB`), `fixture`, `warm`; runs `uv run modal run server/bench.py ...` against `dev` and uploads `runs/bench.jsonl`. Never on push (the $200 cap; `bench.py` already stops at a $160 rate-card estimate, `internal/modal-pipeline/README.md`).
- **`e2e.yml`:** `workflow_dispatch` plus nightly cron; replays three golden packets against `dev`, then `score-geometry` and `score-verdicts`; fails on any unsafe pass (WS-11 gates). Goes through the public API with `CAPTURE_API_TOKEN`; no GCP auth needed.

GitHub configuration Hunter sets (names only): repo variables `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT_EMAIL`; environments `dev` and `prod`, each with secrets `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `CAPTURE_API_TOKEN` and variable `CAPTURE_API_URL`.

### 4.4 Env layout and secret names

- **Modal:** `modal environment create dev` and `prod`; one app deploy, one volume `house-capture-pipeline` and one Secret set per environment. `SMP_ENVIRONMENT` equals the Modal environment name.
- **Runtime env contract** (`server/common/config.py`):

| Group | Variables |
|---|---|
| SMP contract (required) | `SMP_PRODUCT_ID=house-scanning`, `SMP_ENVIRONMENT=dev\|prod`, `SMP_SERVICE_NAME=capture-api\|early-worker\|pipeline\|sweeper` |
| GCP | `GCP_PROJECT_ID`, `GCS_BUCKET=hss-captures-<env>`, `GCP_RUNTIME_SA_EMAIL`, `GCP_WIF_AUDIENCE` |
| Backends | `STATE_BACKEND=modal_dict\|postgres`, `STORAGE_BACKEND=gcs\|r2\|volume` |
| Secrets | `OPENAI_API_KEY`, `XAI_API_KEY`, `CAPTURE_API_TOKENS`, `WEBHOOK_HMAC_SECRET` |
| Later | `DATABASE_URL`, `APNS_KEY_P8`, `APNS_KEY_ID`, `APNS_TEAM_ID` |

- **Modal Secret names now:** `hss-<env>-vlm` (OpenAI, xAI), `hss-<env>-api` (device tokens, webhook HMAC), `hss-<env>-gcp` (non-secret WIF audience and SA email). No service-account JSON anywhere unless the WIF fallback in section 3 is taken.
- **GSM names at migration** (from `gsm_secret_namespace/locals.tf`): `secret--house-scanning--<env>--{openai-api-key,xai-api-key,capture-api-tokens,webhook-hmac-secret,database-url,apns-key}`, created without `initial_value`; versions added out of band; Cloud Run reads them by full resource name.
- **Logging now:** one JSON line per event with `capture_id`, `run_id`, `stage`, `service.name`, `deployment.environment`, `duration_ms`, `vlm_cost_usd`. These are `telemetry-bootstrap`'s resource attributes, so the OTel exporter at migration adds no renaming.

### 4.5 At GCP migration (WS-12 notes)

- Copy `cloud_run_service`, `artifact_registry_repo`, `pubsub_topology`, `logging_retention_baseline`, `gsm_secret_namespace`, `service_identity_secret_access` and the `build-publish-container` action.
- Extend `cloud_run_service` with `gpu` (`nvidia-l4`), `node_selector`, `gpu_zonal_redundancy_disabled`, `cpu_idle`, `startup_cpu_boost`, `volumes` (Cloud SQL) and `vpc_access`. Consider `google_cloud_run_v2_job` for the pipeline stage, since Cloud Run services cap requests at 60 minutes.
- Write a `cloud_sql_postgres` module (SMP has none) and record the Spanner exception in `docs/components/deploy.md`.
- Replace `slo_alert_baseline`'s call site with alerts on time-to-verdict p95, stage failure count and VLM spend per house; wire a notification channel.
- The Cloud Run API service account signs URLs with its own identity; drop the Modal WIF provider after cutover.

### 4.6 What a Grok 4.7 builder must be told for each block

- The exact SMP commit (`38b696f`) and source paths from section 4.1; "copy the files, do not reference SMP as a module source".
- The variable values from section 4.2, including the provider ID override, `allowed_audiences` for Modal, and the narrowed role lists; "no `roles/viewer`, no `projectIamAdmin`".
- "Never set `initial_value` in the secrets module; no CDN or versioning on the capture bucket; no Spanner, Redis, WorkOS, Stripe, KMS, API Gateway; no SMP validators."
- Workflow names, triggers, the GitHub variable and secret names from section 4.3, and "GPU runs are dispatch-only".
- The contract table in section 4.4 and that `config.py` raises on any missing required variable.
- Signed URLs: `google-cloud-storage` `blob.generate_signed_url(version="v4", service_account_email=..., access_token=...)` so federated credentials sign through IAM; bind `content_md5` and `content_type` (hosted-architecture §2.2).
- Validation per block: `terraform fmt -check && terraform init -backend=false && terraform validate` green in CI; `infra.yml` plan artifact attached to the PR; `deploy.yml` verify passes on `dev`; one replay fixture reaches `processing`.

## 5. Risks and hackathon-speed concerns

- **The factory would slow the build.** `render_product_scaffold.py` and `bootstrap_product_factory.py` assume Node topologies, a GitHub App or PAT, disposable repos and SMP's shared projects. Do not run them.
- **Cost floors in the shared baseline.** `product-serving/shared-baseline` provisions Spanner (100 PU) and Memorystore Redis; both bill monthly regardless of use. Never apply it for this project.
- **The root stack starter is not opt-in enough.** It always instantiates `network_security` (Cloud Armor) and `logging_retention` (`security.tf`, `observability.tf`) and needs four service images. Borrow its file layout, not the stack.
- **Auto-approved applies.** `terraform-stack` runs `apply -auto-approve` right after `plan`. Keep applies on manual dispatch with the `apply` input.
- **First apply needs a human.** WIF cannot bootstrap itself; someone with project owner runs `terraform apply` locally with ADC once. If the GCP project or billing is not ready when WS-01 starts, WS-01 must use the R2 or Modal-volume storage backend from hosted-architecture §4 rather than wait.
- **WIF is main-only by default**, so PR jobs cannot reach GCP. Intended; E2E on PRs goes through the API with a token.
- **Keyless signed URLs** need `roles/iam.serviceAccountTokenCreator` and the IAM Credentials API; forgetting either is a common first-deploy failure. Attribute conditions that are wrong fail closed, which is safe but blocks uploads: decode a live `MODAL_IDENTITY_TOKEN` before writing the condition.
- **Broad CI roles.** SMP's GitHub SA defaults include `roles/viewer` and project IAM admin (`shared-baseline-env/variables.tf`). Narrow per section 4.2.
- **Governance mismatch inside SMP.** `DLV-DEC-005` promotion `dev -> stage -> prod` vs `SMP-DEC-018` prod-only, and backend key `<product>/<environment>` vs rendered `products/<product>/<env>`. Pick `dev` + `prod` and `house-scanning/<env>`, and write them down once.
- **The SLO call site is broken as written** (`terraform-root-stack-starter/observability.tf`); copying it will fail or alert on nothing.
- **Double secret stores.** Keeping Modal Secrets and GSM in sync during the hackathon is wasted effort and drift risk; GSM comes at migration.
- **Cross-repo reusable workflows** would pin the server to SMP `@main` (a private repo whose `main` changes daily) and require SMP's Actions access setting; copying the stage shape avoids both.
- **Modal Team plan** is unrelated to SMP but is the real scaling cliff (Starter caps at 10 GPUs; hosted-architecture §1 row 4).

## 6. Open questions for Hunter

1. Which GCP project and billing account: a new `house-scanning-*` project, or the SMP `smp-shared-prod` project as an incubation product? The recipe assumes a new project.
2. Terraform state: standalone `hss-tfstate` in that project, or SMP's `smp-substrate-tfstate-prod` with prefix `house-scanning/<env>` (requires an IAM change in SMP's substrate stack)?
3. Approve `dev` + `prod` in one GCP project as an exception to `SMP-DEC-018` and the prod/non-prod co-mingling guardrail?
4. Approve Cloud SQL Postgres at migration as an exception to `SMP-DEC-006`, and should it be recorded as an SMP `deployment-integrations/` doc like the chatgraph one?
5. Sentry at migration (needs an `OBS-DEC-009` exception) or Cloud Error Reporting only?
6. Modal OIDC to GCS now (keyless, section 4.2) or a service-account key in a Modal Secret for the hackathon with WIF deferred?
7. Who runs the first `terraform apply` and when? It needs project owner locally, before WIF exists; ideally before WS-01 starts.
8. Is the $200 Modal cap set as a workspace spend limit in the Modal dashboard? It cannot be enforced from this repo.
9. Should the `SMP_*` env variable names stay if Base ends up owning the server repo, or be renamed at migration?
