resource "google_storage_bucket" "fixtures" {
  count = var.manage_project_resources ? 1 : 0

  name                        = var.fixtures_bucket_name
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = merge(local.base_labels, { data_class = "eval" })

  versioning {
    enabled = false
  }

  # SD-77. Same 30-day deletes as the capture bucket. Eval objects sit outside these prefixes.
  dynamic "lifecycle_rule" {
    for_each = toset(["captures/", "work/", "uploads/"])
    content {
      action {
        type = "Delete"
      }
      condition {
        age            = 30
        matches_prefix = [lifecycle_rule.value]
      }
    }
  }

  lifecycle_rule {
    action {
      type = "AbortIncompleteMultipartUpload"
    }
    condition {
      age = 2
    }
  }
}
