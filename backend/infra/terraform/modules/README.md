# Terraform modules for WS-12

WS-12 pastes this block into the root stack. State lives in SMP's substrate bucket. This WS validates the modules with `terraform test` and does not apply them.

SOURCE: shape of huntertcarver/Software-Manufacturing-Plant@38b696f shared-assets/data/object-storage-cdn-baseline/modules/object_storage_cdn (no CDN, no versioning).

```hcl
terraform {
  backend "gcs" {}
}

module "modal_wif" {
  source             = "./modules/modal_wif"
  project_id         = var.project_id
  project_number     = var.project_number
  environment        = var.environment
  modal_workspace_id = var.modal_workspace_id
}

module "capture_bucket" {
  source               = "./modules/capture_bucket"
  project_id           = var.project_id
  environment          = var.environment
  object_admin_members = ["serviceAccount:${module.modal_wif.runtime_sa_email}"]
}

output "capture_bucket" {
  value = module.capture_bucket.bucket_url
}

output "runtime_sa_email" {
  value = module.modal_wif.runtime_sa_email
}

output "modal_wif_audience" {
  value = module.modal_wif.wif_audience
}
```

Init (WS-12, not this PR):

```bash
terraform init \
  -backend-config=bucket=smp-substrate-tfstate-prod \
  -backend-config=prefix=house-scanning/<env>
```

Those three outputs are what the Modal secret `hss-<env>-gcp` needs, together with `GCP_PROJECT_ID`.

## Postgres DDL sketch (WS-12 migration)

```sql
captures(capture_id pk, packet_id unique, parent_capture_id, status, tier, flow, device_model, order_ref, owner_principal, created_at,
         finalize_by, packet_sha256, run_id, finalized_at, deleted_at, extras jsonb)
files(capture_id fk, path, role, bytes, sha256, md5, content_type, state, key, generation, committed_at, meta jsonb, pk(capture_id, path))
events(capture_id, seq bigint, type, at, run_id, stage, data jsonb, pk(capture_id, seq))
runs(run_id pk, capture_id, packet_sha256, parent_run_id, status, branch, flow, tier, profile_default, created_at, deadline_at, started_at,
     finished_at, artifacts jsonb, outcome_kind, failure_code, degraded jsonb, spend_usd jsonb)
stages(run_id, stage, status, attempt, failures, respawns, gpu, worker_call_id, heartbeat, substep, artifact_uri, error, error_code,
       metrics jsonb, pk(run_id, stage))
blobs(key pk, value jsonb);
spend(scope pk, usd numeric);
idempotency(scope, key, fingerprint, ref, created_at, pk(scope, key));
amendments(capture_id, amendment_id, answers jsonb, status, packet_sha256, run_id, created_at, pk(capture_id, amendment_id));
confirmations(capture_id, field_id, confirmation_id, image_id, kind, value, client_at, received_at, by, pk(capture_id, field_id));
devices(principal, token, platform, bundle_id, updated_at, pk(principal, token));
```

`STATE_BACKEND=postgres` raises until WS-12 ships the backend. Dict keys this API writes are `capture:`, `capturex:`, `file:`, `files:`, `idem:`, `amend:`, `confirm:`, `confirmix:`, `device:`, `tomb:`, `open:`. Run, stage, and event rows go through `StateStore` methods.
