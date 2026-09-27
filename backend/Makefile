# Skeleton targets. The fixtures PR adds the scripts behind fixtures, gt and validate,
# and then `ci` gains `validate` (brief §8: make ci on main after that PR).
.PHONY: setup lint test schemas hygiene fixtures gt validate validate-full ci

ONLY ?= small

setup:
	uv sync --frozen

lint:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen lint-imports

test:
	uv run --frozen pytest -q

schemas:
	uv run --frozen python -m server.contracts.export_schemas --check

hygiene:
	uv run --frozen python scripts/check_repo_hygiene.py

fixtures:
	uv run --frozen python scripts/fetch_fixtures.py --only $(ONLY) $(if $(ITEM),--item $(ITEM),)

gt:
	bash scripts/rebuild_gt.sh

validate:
	uv run --frozen python scripts/validate_fixtures.py

validate-full:
	uv run --frozen python scripts/validate_fixtures.py --full

ci: lint test schemas hygiene
