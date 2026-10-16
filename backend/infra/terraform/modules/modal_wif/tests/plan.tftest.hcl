mock_provider "google" {}

variables {
  project_id         = "house-scanning"
  project_number     = "123456789"
  environment        = "dev"
  modal_workspace_id = "ac-workspace"
}

run "wif" {
  command = plan

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.oidc[0].issuer_uri == "https://oidc.modal.com"
    error_message = "issuer must be https://oidc.modal.com"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.oidc[0].allowed_audiences == tolist(["oidc.modal.com"])
    error_message = "audience must be oidc.modal.com"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.attribute_mapping["google.subject"] == "\"modal/\" + assertion.workspace_id + \"/\" + assertion.environment_name + \"/\" + assertion.app_name"
    error_message = "google.subject must be the modal concatenation, never assertion.sub"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.attribute_mapping["attribute.workspace_id"] == "assertion.workspace_id"
    error_message = "workspace_id must be mapped"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.attribute_mapping["attribute.environment_name"] == "assertion.environment_name"
    error_message = "environment_name must be mapped"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.attribute_mapping["attribute.app_name"] == "assertion.app_name"
    error_message = "app_name must be mapped"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.modal.attribute_condition == "attribute.workspace_id == \"ac-workspace\" && attribute.environment_name == \"dev\""
    error_message = "condition must bind workspace and environment"
  }

  assert {
    condition     = google_service_account_iam_member.workload_identity_user.role == "roles/iam.workloadIdentityUser"
    error_message = "runtime SA must grant workloadIdentityUser"
  }

  assert {
    condition     = google_service_account_iam_member.token_creator.role == "roles/iam.serviceAccountTokenCreator"
    error_message = "runtime SA must grant serviceAccountTokenCreator to itself"
  }

  assert {
    condition     = startswith(google_service_account_iam_member.token_creator.member, "serviceAccount:")
    error_message = "token creator member must be the runtime service account"
  }
}
