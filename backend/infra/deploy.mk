.PHONY: terraform-check modal-import

terraform-check:
	terraform -chdir=infra/terraform fmt -check -recursive
	terraform -chdir=infra/terraform init -backend=false
	terraform -chdir=infra/terraform validate

modal-import:
	SMP_ENVIRONMENT=dev uv run python -c "import server.modal_app"
