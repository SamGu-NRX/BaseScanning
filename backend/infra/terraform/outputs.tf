output "project_number" {
  value = data.google_project.this.number
}

output "github_wif_provider_name" {
  value = local.github_wif_provider_name
}

output "github_sa_email" {
  value = one(module.github_sa[*].email)
}

output "fixtures_bucket" {
  value = one(google_storage_bucket.fixtures[*].name)
}

output "modal_wif_audience" {
  value = module.modal_wif.wif_audience
}

output "runtime_sa_email" {
  value = module.modal_wif.runtime_sa_email
}

output "capture_bucket" {
  value = one(module.capture_bucket[*].bucket_name)
}
