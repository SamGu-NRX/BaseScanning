# house-scanning Terraform root stack

Shared substrate path (Hunter, 2026-09-26): state in `gs://smp-substrate-tfstate-prod` under prefix `house-scanning/dev`. SD-77: that is the only stack. There is no `env/prod.tfvars` and no prod state prefix. Project-specific resources live in GCP project `house-scanning`.

This checkout does not run `terraform apply` and does not create keys. `module "modal_wif"` and `module "capture_bucket"` are WS-01 PR 34's modules, called with no `count`.

## Applied dev state (2026-09-27)

State: `gs://smp-substrate-tfstate-prod` prefix `house-scanning/dev`. Project `house-scanning` (`365941653634`). Billing is linked. `env/dev.tfvars` is the applied config: `manage_project_resources=true`, `github_wif_mode=own`, `monthly_budget_amount=50`, `modal_workspace_id=ac-EB54gLJQudckj2p3SOd5QI`. Do not apply `manage_project_resources=false` against this prefix; that destroys the GitHub pool, `hss-gha`, `module.modal_wif`, `module.capture_bucket`, and `hss-fixtures`.

In that state: pool `modal-dev`, runtime SA `hss-runtime-dev`, `hss-captures-dev`, `hss-fixtures`, Secret Manager, and the $50 budget. Names stay as they are. `hss-gha` has four project roles and no billing-account role. Retention is 30 days. The Modal provider condition is workspace id and environment name, matching the applied provider.

## Derive every placeholder

Run these on a machine that already has ADC. The applied values are already in `env/dev.tfvars`. Do not add a prod tfvars file.

```bash
gcloud billing projects describe house-scanning --format='value(billingAccountName)'
# billing_account is that value without the billingAccounts/ prefix.

gcloud projects describe smp-shared-substrate --format='value(projectNumber)'
# SUBSTRATE_PROJECT_NUMBER

gcloud iam workload-identity-pools providers list \
  --workload-identity-pool=github-actions \
  --location=global \
  --project=smp-shared-substrate \
  --format='value(name)'
# github_wif_provider_name is that full resource name.
# github_wif_pool_name is the same value with /providers/<PROVIDER_ID> removed.

# modal_workspace_id comes from `uv run modal token info` (workspace id only; never commit the token).
```

The project id is `house-scanning`. Bucket names stay `hss-captures-dev` and `hss-fixtures`.

## Bootstrap (shared path)

Hunter, from a Mac with ADC (`gcloud auth application-default login`), project owner on `house-scanning`, owner on `smp-shared-substrate`, billing admin. PR S (`infra/smp/pr-s-substrate-onboarding.diff`) must be applied after step 2 and before CI can read the state bucket. Do not create a service-account key.

1. Create the project and enable APIs:

```bash
gcloud projects create house-scanning --name=house-scanning
gcloud billing projects link house-scanning --billing-account=BILLING_ACCOUNT_ID
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com storage.googleapis.com secretmanager.googleapis.com billingbudgets.googleapis.com --project=house-scanning
```

2. The only state prefix is `house-scanning/dev`. There is no prod apply.

```bash
cd infra/terraform
terraform init -backend-config=bucket=smp-substrate-tfstate-prod -backend-config=prefix=house-scanning/dev
terraform apply -var-file=env/dev.tfvars
```

Then mirror fixtures once from a machine that has the store and ADC:

```bash
gcloud storage rsync -r "$S/internal/data/packets/archives" gs://hss-fixtures/data/packets/archives
```

Repeat for each GT PLY directory listed in the fixture manifest, keeping the `$S/internal`-relative path.

4. Apply PR S (below) so `hss-gha` can use the state prefix and the substrate provider accepts this repo. The service account from step 2 must exist first.

5. GitHub variables from `terraform output -json` (redact the billing account if it appears): `GCP_PROJECT_ID=house-scanning`, `GCP_WIF_PROVIDER=<github_wif_provider_name>`, `GCP_SERVICE_ACCOUNT_EMAIL=hss-gha@house-scanning.iam.gserviceaccount.com`, `TF_BACKEND_BUCKET=smp-substrate-tfstate-prod`, `FIXTURE_SOURCE=gs://hss-fixtures`. Modal Secret names, not values, are `hss-dev-gcp`, `hss-dev-vlm`, `hss-dev-api`.

6. Dispatch `infra.yml` with `apply=false`. After that, changes go PR, dispatch plan, merge, dispatch `apply=true`. The workflow has no prod input.

## PR S (SMP repo)

The patch is `infra/smp/pr-s-substrate-onboarding.diff`, against `huntertcarver/Software-Manufacturing-Plant` at `38b696f`. Branch `house-scanning/substrate-onboarding`. Expected plan after apply: one in-place update of the provider `attribute_condition`, and one create of `google_storage_bucket_iam_member.product_state_object_admin["house-scanning"]`.

```bash
git clone https://github.com/huntertcarver/Software-Manufacturing-Plant.git
cd Software-Manufacturing-Plant
git checkout 38b696f
git checkout -b house-scanning/substrate-onboarding
git apply /PATH/TO/house-scanning-server/infra/smp/pr-s-substrate-onboarding.diff
terraform fmt -check -recursive shared-assets/delivery/terraform-governance-pack/modules/github_actions_workload_identity
terraform fmt -check -recursive shared-assets/delivery/shared-baseline-substrate
terraform -chdir=shared-assets/delivery/shared-baseline-substrate init -backend=false
terraform -chdir=shared-assets/delivery/shared-baseline-substrate validate
```

Apply with SMP's `bootstrap-shared-substrate.yaml` dispatch (environment `substrate`) or locally:

```bash
cd shared-assets/delivery/shared-baseline-substrate
terraform init -backend-config=bucket=smp-substrate-tfstate-substrate -backend-config=prefix=shared-platform/substrate
terraform plan -var-file=env/shared-platform.tfvars
terraform apply -var-file=env/shared-platform.tfvars
```

If a later server-repo `terraform init` fails with `storage.buckets.get` denied, add `roles/storage.legacyBucketReader` (bucket-level, no condition) next to the objectAdmin member in `product_state_access.tf`.

## Fallback (own pool and state bucket)

Use this only if PR S cannot be applied. Set in the tfvars you apply (do not commit secrets): `github_wif_mode="own"`, `github_wif_pool_name=null`, `github_wif_provider_name=null`, `create_state_bucket=true`. Repo variables become `TF_BACKEND_BUCKET=hss-tfstate` and `GCP_WIF_PROVIDER=projects/<HSS_PROJECT_NUMBER>/locations/global/workloadIdentityPools/github-actions/providers/house-scanning-server`.

```bash
cd infra/terraform
terraform init -backend-config=bucket=hss-tfstate -backend-config=prefix=house-scanning/dev
terraform apply -var-file=env/dev.tfvars
```

## Migration to shared

After PR S is applied, from a directory that already has the `house-scanning/dev` state:

```bash
terraform init -migrate-state -backend-config=bucket=smp-substrate-tfstate-prod -backend-config=prefix=house-scanning/dev
```

Set `github_wif_mode="shared"`, the two `github_wif_*` names, and `create_state_bucket=false`. Then:

```bash
terraform state rm 'module.state_bucket[0]'
terraform apply -var-file=env/dev.tfvars
```

Update `GCP_WIF_PROVIDER` and `TF_BACKEND_BUCKET`. Dispatch `infra.yml` with `apply=false`. After that plan shows no surprises, `gcloud storage rm -r gs://hss-tfstate`. Keep the old bucket until the shared state is verified; `state rm` does not delete it.

## CI

`infra.yml` is `workflow_dispatch` only (`apply` default false) against prefix `house-scanning/dev`. It never runs `apply` on push and never passes `-auto-approve`.
