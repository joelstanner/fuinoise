.PHONY: quality

quality:
	venv/bin/black --check .
	venv/bin/ruff check .
	venv/bin/mypy fuinoise fuinoise_live
