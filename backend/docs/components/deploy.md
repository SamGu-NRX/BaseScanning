# Deploy and infrastructure

WS-12. One deployed stack (SD-77). Modal app `house-scanning` in Modal environment `dev`. GCP project `house-scanning` uses state `gs://smp-substrate-tfstate-prod` prefix `house-scanning/dev`. Existing names stay (`hss-captures-dev`, `hss-fixtures`, `hss-runtime-dev`, `hss-dev-*`). This page has names only. No tokens, keys, or billing-account ids.

## Layout decision

Hunter, 2026-09-26: reuse the shared substrate instead of a standalone state bucket. Project-specific resources (capture buckets, runtime service accounts, the Modal OIDC pool, CI service account `hss-gha`, the project budget, and at migration Cloud Run, Pub/Sub and Cloud SQL) live in `house-scanning`. Artifact Registry, shared logging, shared monitoring and GSM stay in `smp-shared-prod` at migration.

`infra/smp/pr-s-substrate-onboarding.diff` is the SMP change (branch `house-scanning/substrate-onboarding`). It is not applied. Exact commands are in `infra/terraform/README.md`. Do not run `terraform apply` from CI except a manual `infra.yml` dispatch with `apply=true`. Do not create service-account keys. This workstream does not apply Terraform; the bootstrap agent applies `dev` again after billing is linked.

## What is applied (dev only)

Applied 2026-09-27 against `gs://smp-substrate-tfstate-prod` prefix `house-scanning/dev`. Project `house-scanning` number `365941653634`. Billing is linked. No prod apply. No service-account keys.

`env/dev.tfvars` matches that apply: `manage_project_resources=true`, `github_wif_mode=own`, `create_state_bucket=false`, `monthly_budget_amount=50`, `modal_workspace_id=ac-EB54gLJQudckj2p3SOd5QI`. Applying `manage_project_resources=false` against this prefix would destroy `module.github_wif`, `module.github_sa`, `module.project_baseline`, `module.modal_wif`, `module.capture_bucket`, and `google_storage_bucket.fixtures`.

In state: GitHub pool `github-actions` and provider `house-scanning-server`, `hss-gha` with `roles/storage.admin`, `roles/serviceusage.serviceUsageAdmin`, `roles/secretmanager.admin`, and `roles/iam.securityReviewer` only. The GitHub provider condition is `refs/heads/main` of this repository, also matched on repository id and owner id. There is no `environment:dev` subject. Modal pool `modal-dev`, `hss-runtime-dev`, `module.capture_bucket` (`hss-captures-dev`, no count), `hss-fixtures`, Secret Manager, and the $50 budget at 50% and 90%. `hss-gha` does not have `roles/billing.costsManager`. Lifecycle on both buckets deletes `captures/`, `work/`, and `uploads/` at 30 days and aborts incomplete multipart uploads at 2 days.

There is no prod stack. `env/prod.tfvars` is gone. `deploy.yml` and `infra.yml` only touch Modal `dev` and state prefix `house-scanning/dev`. `deploy.yml` runs from `main` and only checks out `main` or a commit already on `main`. Lifecycle deletes `captures/`, `work/`, and `uploads/` after 30 days. `hss-gha` has no identity-admin roles and no `roles/billing.costsManager`. The Modal provider condition is workspace id and environment name.

GitHub repo variables set: `GCP_PROJECT_ID=house-scanning`, `GCP_WIF_PROVIDER=projects/365941653634/locations/global/workloadIdentityPools/github-actions/providers/house-scanning-server`, `GCP_SERVICE_ACCOUNT_EMAIL=hss-gha@house-scanning.iam.gserviceaccount.com`. `TF_BACKEND_BUCKET` and `FIXTURE_SOURCE` are not set. Modal secret `hss-dev-gcp` has `GCP_PROJECT_ID`, `GCS_BUCKET=hss-captures-dev`, `GCP_RUNTIME_SA_EMAIL`, and `GCP_WIF_AUDIENCE` for pool `modal-dev`.

## Rebuild

Image pin: CUDA `12.4.1-devel-ubuntu22.04` linux/amd64 digest `sha256:5645fec64549cc35930eee9d85aafd2b0006c0c3f22632be5a1d85e2604e9749`. COLMAP git tag `4.2.0`, which is the release `pycolmap==4.2.0` is built against. uv image `ghcr.io/astral-sh/uv:0.11.23`. Python 3.12. The Dockerfile does not copy application code.

```bash
docker build --platform linux/amd64 -t hss-gpu:local .
docker run --rm hss-gpu:local sh -c 'colmap -h | head -1; python -c "import pycolmap, torch; print(pycolmap.__version__, torch.__version__)"'
```

Expect `4.2` on both COLMAP and pycolmap, and torch `2.5.1+cu124`. The official COLMAP 4.2 image uses Ubuntu 24.04 and CUDA 12.9. This image keeps Ubuntu 22.04 / CUDA 12.4 so the torch cu124 wheel matches. Jammy has no Ceres 2.2 package, so the Dockerfile builds Ceres `2.2.0` from source and installs `libopenimageio-dev`. A local `docker build` was not run: the Docker daemon is not running. Build the image once with `SMP_ENVIRONMENT=dev uv run python -m server.prebuild_gpu_image` (side app `house-scanning-gpu-image`, no web endpoint) and pass the printed id as `HSS_GPU_IMAGE_ID` on deploy. `modal deploy -m server.modal_app` does not compile this Dockerfile. Without that id, `smoke_cpu` and `image_pin` publish on `CPU_IMAGE` so the capture API still goes out.

```bash
SMP_ENVIRONMENT=dev uv run modal deploy -m server.modal_app -e dev --tag "$(git rev-parse --short=12 HEAD)"
uv run modal run scripts/prefetch_weights.py -e dev
uv run python -m server.bench --env dev --gpu L4 --fixture eth3d-facade --variant arkit-nonlidar --max-frames 40,60,70 --spend-stop-usd 40 --out runs/bench.jsonl
uv run python scripts/cost_report.py --bench runs/bench.jsonl
```

`modal` is a dev dependency. Confirm a token with `uv run modal token info` and do not print the values. `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` stay unset in the shell; the Modal CLI profile holds the token. Deploy only the `dev` environment. Spend cap for this workstream is $25 of the workspace $200. Stop at $20.

GPU extra pins torch and torchvision at `2.5.1` / `0.20.1` from `https://download.pytorch.org/whl/cu124`, plus huggingface-hub, safetensors, einops, plyfile and tqdm. numpy, opencv, pillow, scipy and pycolmap stay on the repository lock (`numpy==2.5.3`, `opencv-python-headless==5.0.0.93`, `pillow==12.3.0`, `scipy==1.18.1`, `pycolmap==4.2.0`). Downgrading those to the September experiment pins would rewrite the shared lock. Model tarballs (Pi3 `9fa3ddb3`, MoGe `925b8ed8`, Depth-Anything-3 `3d835ec1`) are installed after `uv sync` from GitHub archive URLs that end in `.tar.gz`. uv rejects a codeload URL whose path does not end in that suffix.

`CPU_IMAGE` syncs the lock with the `api` extra (GCS, auth, uvicorn). The capture API web function uses only that image. The COLMAP image is prebuilt with `python -m server.prebuild_gpu_image` (side app `house-scanning-gpu-image`, no web endpoint) and deployed as `HSS_GPU_IMAGE_ID`, so publishing `capture-api` does not compile COLMAP. GPU functions stay on `GPU_IMAGE`.

WS-04's pose path is pycolmap, not the CLI. The PyPI `pycolmap==4.2.0` wheel has `has_cuda` false, so `Device.auto` runs SIFT on CPU. The GPU image uninstalls that wheel and installs `pycolmap-cuda12==4.2.0` (the CUDA 12 build of the same 4.2.0 bindings). `pycolmap.has_cuda` is true. Keep calling `extract_features` and `match_*` with `pycolmap.Device.auto` or `pycolmap.Device.cuda`. `colmap_cuda` is the L4 smoke for that assertion. The CUDA `colmap` binary is still at `HSS_POSES_COLMAP_BIN` (`/usr/local/bin/colmap`) for a caller that shells out.

## GitHub and Modal names

Repo variables: `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT_EMAIL`, `TF_BACKEND_BUCKET`, `FIXTURE_SOURCE`. Optional: `HSS_PIPELINE_MIN_CONTAINERS`, `HSS_PIPELINE_MAX_CONTAINERS`, `HSS_PIPELINE_GPU`.

GitHub environment `dev` (branch `main` only) holds secrets `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `CAPTURE_API_TOKEN` and variable `CAPTURE_API_URL`. There is no `prod` environment.

Modal Secrets in dev: `hss-dev-api` and `hss-dev-gcp`. `hss-dev-vlm` is not created, so deploy does not attach it. Optional later: `hss-<env>-r2`, `hss-<env>-db`, `hss-<env>-apns`. Volume: `house-capture-pipeline`. App: `house-scanning`. Web label: `capture-api`.

Knobs at deploy time: `HSS_PIPELINE_MIN_CONTAINERS` default 0 (set 1 for a demo window), `HSS_PIPELINE_MAX_CONTAINERS` default 3, `HSS_PIPELINE_GPU` default `L4`. `server.modal_app.KNOBS` publishes them as `pipeline_gpu`, `pipeline_min_containers`, and `pipeline_max_containers` (SD-73). WS-03 reads those keys.

Dev deploy env (I-5, SD-73), on every `secrets_for` service and on Modal secret `hss-dev-api`: `HSS_INGEST_HOOKS=auto` and `HSS_ORCH_STAGE_STUBS=1`. `deploy_env("capture-api")` also sets `STORAGE_BACKEND=gcs` so the function uses `hss-captures-dev` even when `hss-dev-api` still says `volume`, and `HSS_AUTH_MODE=none` (SD-86) so the demo API does not require a bearer token. Static tokens and JWT stay in the code and are not configured. Drop the two dev hook values after every stage package is merged.

## Bootstrap, fallback, promotion

Shared bootstrap, the SMP diff, the own-pool fallback and the later migration are in `infra/terraform/README.md`.

`deploy.yml` push deploys Modal `dev` from `main` only. A dispatch may pass a 40-character SHA that is already on `main`. Rollback is `modal app rollback house-scanning -e dev` from a laptop, then a pinned-SHA dispatch. Terraform has no auto-rollback.

`gpu-bench.yml` and `infra.yml` are dispatch-only. `infra.yml` plans and applies prefix `house-scanning/dev` only. `rollover.yml` rolls the `dev` app and does not change GPU class, timeouts or container counts.

No rollback run link yet. `image_pin` is a CPU function on `GPU_IMAGE` (the prebuilt id when `HSS_GPU_IMAGE_ID` is set). It does not request a GPU and it does not compile COLMAP. Health is `GET /v1/healthz` on the `capture-api` label.

## Alerts and cost

Services log through `server.common.telemetry.log_event`. One JSON line uses the `server.common.logging` key allowlist, plus `gpu_s`, `gpu_usd`, `total_usd`, and `time_to_verdict_s`. Other keys are dropped. Values containing `Bearer `, `X-Goog-Signature`, or a JWT are replaced with `[redacted]`.

`scripts/cost_report.py --bench runs/bench.jsonl` prints gpu, gpu_s, gpu_usd, vlm_usd, total_usd, time_to_verdict_s, disagreements. Reference: geometry-only L4 about $0.05–0.15 per house; VLM about $0.30 with a hard cap of $1.50 per run.

Alerts now: GitHub Actions failure mail, the Modal budget mail, and the nightly e2e workflow once WS-11 lands. At migration: time-to-verdict p95 > 480 s over 1 h; any `stage_failed` in 15 min; `stage_respawned` > 2 per hour; `cost_summary.vlm_usd` > 2.00 for one house; daily disagreement rate > 0.20; `/v1/healthz` failing; GCP budget 50% and 90%; Modal environment budget reached.

## SMP exceptions

1. Cloud SQL Postgres instead of Spanner (`SMP-DEC-006`).
2. One hackathon environment. The live names stay `dev` (`SMP-DEC-018` is not a second stack).
3. Sentry deferred. GCP-native observability until an `OBS-DEC-009` exception is written.
4. GPU runtime (Cloud Run GPU service or job with `nvidia-l4`) is outside SMP's runtime rubric (`RTM-DEC-001/002/005`).
5. Modal is the execution platform during the hackathon.
6. Terraform apply is a manual `infra.yml` dispatch (`DLV-DEC-005` default is none). There is no prod approval gate.

## Migration

Not built. Copy from SMP `38b696f`: `runtime/cloud-run-baseline-template/modules/cloud_run_service`, `delivery/terraform-governance-pack/modules/artifact_registry_repo` (reference only; the repo lives in `smp-shared-prod`), `eventing/pubsub-contracts/modules/pubsub_topology`, `observability/logging-retention-baseline/modules/logging_retention_baseline`, `security/secret-handoff-baseline/modules/gsm_secret_namespace` and `service_identity_secret_access`, `.github/actions/build-publish-container`. Do not call `slo_alert_baseline`.

Extend the Cloud Run service copy with `gpu_type`, `gpu_count` (1), `gpu_zonal_redundancy_disabled` (true), `cpu_idle` (true), `startup_cpu_boost` (false), `execution_environment` (`EXECUTION_ENVIRONMENT_GEN2`), `cloud_sql_instances`, and `startup_probe`. Services: `hss-<env>-api` (1 CPU / 1 GiB, min 1), `hss-<env>-early` (2 CPU / 4 GiB), later `hss-<env>-reviewer` behind IAP.

Pipeline job `hss-<env>-pipeline`: GPU image, `nvidia-l4`, cpu 8, memory 24Gi, one GPU, task timeout 1500 s, `max_retries=2`. A GPU service with min 1 replaces `warm()` only if job start latency breaks the 3-minute p50.

Pub/Sub topics `hss-<env>-capture-events`, `hss-<env>-run-requests`, and the two `-dlq` topics. Push subscriptions use `ack_deadline_seconds=600`, `max_delivery_attempts=5`, and `push_config.oidc_token` (add it; SMP's module lacks it).

New module `infra/terraform/modules/cloud_sql_postgres/`: `POSTGRES_16`, `db-custom-1-3840`, `ZONAL` until 1,000 houses a day, `deletion_protection=true`, public IP with no authorized networks, `ssl_mode=ENCRYPTED_ONLY`, `cloudsql.iam_authentication=on`, database `house_scanning`. `STATE_BACKEND=postgres`.

GSM in `smp-shared-prod`: secret ids `secret--house-scanning--<env>--<name>` for `openai-api-key`, `xai-api-key`, `capture-api-tokens`, `webhook-hmac-secret`, `database-url`, `apns-key`. No `initial_value`. Versions are added out of band.

SMP shared-env change (separate PR): Artifact Registry writer `hss-gha`, reader the Cloud Run robot of this project, log bucket writer, monitored project, and the alert policies above. Add `run`, `pubsub`, `sqladmin`, `logging`, `monitoring`, `artifactregistry` to `required_apis` at that point.

Cutover: keep `serviceAccountTokenCreator` on the runtime service account. Set `enable_modal_wif=false` once WS-01 adds that variable. Delete Modal environments two weeks later.

Log retention at migration: 30 days.

WIF fallback debt: an SA key inside `hss-<env>-gcp` is not the plan. If it is ever used, treat it as debt and remove it. This repo does not contain a key.
