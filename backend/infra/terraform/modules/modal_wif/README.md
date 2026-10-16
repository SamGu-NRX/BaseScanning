# modal_wif

Workload Identity Federation from Modal OIDC (`https://oidc.modal.com`, audience `oidc.modal.com`) to the runtime service account `hss-runtime-<env>`.

`google.subject` is `modal/{workspace_id}/{environment_name}/{app_name}`. Modal's `sub` is about 140 characters and GCP rejects mapped subjects over 127 bytes, so it is not used.

`project_number` is an input so `terraform test` does not need credentials. One pool per environment.

The runtime service account can sign blobs as itself (`roles/iam.serviceAccountTokenCreator`). Bucket IAM is not in this module: the root stack passes `object_admin_members` into `capture_bucket`.
