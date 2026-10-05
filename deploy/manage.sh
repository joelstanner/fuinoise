#!/bin/sh
set -eu
# Run as the fuinoise service account. The environment file is root-managed.
release=$1
shift
set -a
. /etc/fuinoise/fuinoise.env
set +a
export FUINOISE_STATIC_ROOT="$release/staticfiles"
cd "$release"
exec venv/bin/python manage.py "$@"
