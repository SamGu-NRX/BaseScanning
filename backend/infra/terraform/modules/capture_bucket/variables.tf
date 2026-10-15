variable "project_id" {
  type = string
}

variable "environment" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "location" {
  type    = string
  default = "us-central1"
}

variable "bucket_name" {
  type    = string
  default = null
}

variable "owner" {
  type    = string
  default = "huntertcarver"
}

variable "upload_ttl_days" {
  type    = number
  default = 7
}

variable "nearline_after_days" {
  type    = number
  default = 30
}

variable "abort_multipart_after_days" {
  type    = number
  default = 1
}

variable "cors_origins" {
  type    = list(string)
  default = []
}

variable "object_admin_members" {
  type    = list(string)
  default = []
}

variable "object_viewer_members" {
  type    = list(string)
  default = []
}

variable "force_destroy" {
  type    = bool
  default = false
}

variable "extra_labels" {
  type    = map(string)
  default = {}
}
