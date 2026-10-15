provider "google" {
  project = var.project_id
}
module "backend" {
  source      = "../../"
  project_id  = var.project_id
  bucket_name = "smp-tfstate-dev"
}
variable "project_id" {
  type = string
}
