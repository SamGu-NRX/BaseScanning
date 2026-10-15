SOURCE: huntertcarver/Software-Manufacturing-Plant@38b696f shared-assets/delivery/terraform-governance-pack/modules/gcs_backend_baseline
LOCAL CHANGES: dropped the unused customer-managed encryption-key input and its dynamic encryption block. This stack uses Google-managed bucket encryption only. With that input left unset, the bucket resource is unchanged.

# GCS Backend Baseline Module

Creates the shared GCS backend bucket used by Terraform root stacks, including versioning. Encryption uses Google-managed keys.

## Key inputs

- `project_id`
- `bucket_name`
- `location`
- `admin_members`

## Key outputs

- `bucket_name`
- `backend_block`
