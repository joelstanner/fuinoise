# Fuinoise agent instructions

## Communication

- Keep responses brief and direct. Summarize the change, verification, and any remaining issue.
- When showing code changes, use focused diffs or specific line edits. Do not paste entire files or large blocks of unchanged code.

## Project

- This is a Django project. Project settings and URLs are in `fuinoise/`; the application, models, admin, and migrations are in `fuinoise_live/`.
- Use `venv/bin/python` for project commands. The environment uses Python 3.13; dependencies are pinned in `requirements.txt`.
- Keep existing local edits and the ignored `db.sqlite3` intact unless the user asks otherwise.

## Verification

- Before committing, run `venv/bin/black --check .`, `venv/bin/ruff check .`, and `venv/bin/mypy fuinoise fuinoise_live`.
- Run `venv/bin/python manage.py check` and `venv/bin/python manage.py test` after Django code changes.
- After model changes, run `venv/bin/python manage.py makemigrations --check --dry-run`; add a migration when the model state changes.
- Use the test database for verification. Do not alter the local SQLite database just to run checks.
