variable "project_id" {
  type = string
}
variable "bucket_name" {
  type = string
}
variable "location" {
  type    = string
  default = "US"
}
variable "labels" {
  type    = map(string)
  default = {}
}
variable "versioning_enabled" {
  type    = bool
  default = true
}
variable "admin_members" {
  type    = set(string)
  default = []
}
