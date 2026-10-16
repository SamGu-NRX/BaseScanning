output "bucket_name" {
  value = google_storage_bucket.captures.name
}

output "bucket_url" {
  value = "gs://${google_storage_bucket.captures.name}"
}

output "bucket_self_link" {
  value = google_storage_bucket.captures.self_link
}
