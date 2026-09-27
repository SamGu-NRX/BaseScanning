# capture_bucket

One GCS bucket for capture uploads and finalized objects. No CDN, versioning off, soft-delete retention 0 so `DELETE` is a real erasure.

`uploads/` is deleted after 7 days. `captures/` moves to Nearline after 30 days and is not deleted by lifecycle. Incomplete multipart uploads abort after 1 day.

CORS is omitted unless `cors_origins` is non-empty. The reviewer page loads bytes through signed URLs.

```hcl
module "capture_bucket" {
  source                = "./modules/capture_bucket"
  project_id            = var.project_id
  environment           = var.environment
  object_admin_members  = ["serviceAccount:${module.modal_wif.runtime_sa_email}"]
}
```
