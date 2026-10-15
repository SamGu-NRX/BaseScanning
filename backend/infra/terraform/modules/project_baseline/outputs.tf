output "project_id" { value = local.effective_project_id }
output "project_number" { value = data.google_project.current.number }
output "enabled_apis" { value = sort(tolist(var.required_apis)) }
