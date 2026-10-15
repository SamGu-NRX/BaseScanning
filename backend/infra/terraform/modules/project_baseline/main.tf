resource "google_project" "this" {
  count           = var.create_project ? 1 : 0
  project_id      = var.project_id
  name            = var.project_name
  billing_account = var.billing_account
  folder_id       = var.folder_id
  org_id          = var.folder_id == null ? var.org_id : null
  labels          = local.effective_labels
}

resource "google_project_service" "required" {
  for_each           = var.required_apis
  project            = local.effective_project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "current" {
  project_id = local.effective_project_id
  depends_on = [google_project.this]
}

resource "google_billing_budget" "project" {
  count           = var.manage_budget && var.billing_account != null && var.monthly_budget_amount != null ? 1 : 0
  billing_account = var.billing_account
  display_name    = var.project_id

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency_code
      units         = tostring(local.budget_units)
      nanos         = local.budget_nanos
    }
  }

  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
}
