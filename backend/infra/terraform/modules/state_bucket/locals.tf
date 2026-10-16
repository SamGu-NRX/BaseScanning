locals { effective_labels = merge({ managed_by = "terraform", purpose = "terraform-state" }, var.labels) }
