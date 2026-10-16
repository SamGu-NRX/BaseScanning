SOURCE: huntertcarver/Software-Manufacturing-Plant@38b696f shared-assets/delivery/terraform-governance-pack/modules/gcp_project_env_baseline
LOCAL CHANGES: none

# GCP Project Environment Baseline Module

Creates or manages the shared environment project baseline, enables required APIs, and optionally attaches a project budget.

## Key inputs

- `project_id`
- `project_name`
- `environment`
- `billing_account`
- `required_apis`
- `manage_budget`

## Key outputs

- `project_id`
- `project_number`
- `enabled_apis`
