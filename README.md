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
