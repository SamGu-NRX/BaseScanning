locals {
  effective_labels     = merge({ environment = var.environment, managed_by = "terraform", scope = "shared-baseline" }, var.labels)
  effective_project_id = var.create_project ? google_project.this[0].project_id : var.project_id
  budget_units         = var.monthly_budget_amount == null ? null : floor(var.monthly_budget_amount)
  budget_nanos         = var.monthly_budget_amount == null ? null : floor((var.monthly_budget_amount - floor(var.monthly_budget_amount)) * 1000000000)
}
