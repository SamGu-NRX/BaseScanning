# Contract targets. Later WS-00 PRs add lint, test, hygiene, fixtures and validate.
.PHONY: schemas

schemas:
	uv run --frozen python -m server.contracts.export_schemas --check
