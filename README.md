# fuinoise
Fuinoise website

A place to display the weekly lineup and keep an archive of the past.

## Local setup

Use Python 3.12 or newer. Create a virtual environment and install the dependencies:

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py runserver
```

## Lint and type checks

Install the development tools with `venv/bin/python -m pip install -r requirements-dev.txt`,
then run all three checks with `make quality`. The same checks run on GitHub
pushes and pull requests. To run them individually:

```sh
venv/bin/black --check .
venv/bin/ruff check .
venv/bin/mypy fuinoise fuinoise_live
```

## Tests

Install the development dependencies, then run the Django tests with pytest.
Coverage for application code is shown in the terminal:

```sh
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest
```
