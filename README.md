# fuinoise
Fuinoise website

A place to display the weekly lineup and keep an archive of the past.

## MVP scope and architecture

See [the agreed MVP definition](docs/mvp-definition.md) for release requirements
and architecture, and [TODO](TODO.md) for remaining work. The organizer
Timeline uses React and a Django REST Framework API; public and streamer pages
use Django templates. The MVP requires a successful real pilot event.

The [implementation plan](docs/mvp-implementation-plan.md) defines milestone order,
the first scheduling milestone, and verification requirements.

The scheduling backend is implemented: planned durations, private drafts,
explicit publication, and protection against stale edits. Twitch sign-in,
Discord linking, and organizer eligibility reviews are implemented. See
[account setup and eligibility](docs/accounts-and-eligibility.md) for required
credentials, organizer roles, and live verification. Streamer signup, preference
requests, confirmed-assignment dashboards, and direct cancellation are implemented;
see [requests and assignments](docs/requests-and-assignments.md). The React
Timeline and its API are implemented; see [workspace setup and usage](docs/organizer-workspace.md).

## Local setup

Use Python 3.13. Create a virtual environment and install the dependencies:

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
venv/bin/python manage.py migrate
venv/bin/python manage.py runserver
```

Migration 0013 preserves existing schedules and leaves their planned durations
unknown. Review and enter those durations before saving or publishing a working
draft. New slots use their event's default length, initially 60 minutes. Public
pages continue to show existing schedules while awaiting review.

The frontend build requires Node 20.19 or 22.12 or newer in those supported
series. Build it before collecting static files for deployment.

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
Coverage for application code is shown in the terminal. Local pytest runs and CI
fail if total coverage falls below 80%; migrations and test code are excluded.

```sh
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest
```
