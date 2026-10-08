.DEFAULT_GOAL := check
.PHONY: help sync test lint format check clean

help:
	@echo "Available targets:"
	@echo "  make sync    - install/sync dependencies (uv sync)"
	@echo "  make test    - run the test suite"
	@echo "  make lint    - ruff check --fix"
	@echo "  make format  - ruff format"
	@echo "  make check   - lint + format + test + clean (run before every commit)"
	@echo "  make clean   - remove __pycache__/.pytest_cache/.ruff_cache"

sync:
	uv sync

test:
	uv run pytest -v

lint:
	uv run ruff check --fix .

format:
	uv run ruff format .

check: lint format test clean

clean:
	rm -rf glue_doctor/__pycache__ glue_doctor/checks/__pycache__ tests/__pycache__ .pytest_cache .ruff_cache
