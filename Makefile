UV_RUN := env UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools UV_PYTHON_INSTALL_DIR=.cache/python uvx --from uv==0.12.6 uv
PYTHON := .venv/bin/python

.PHONY: check db-up format-check lint migrate migration-check serve sync test type

sync:
	$(UV_RUN) sync --frozen

db-up:
	docker compose up -d db

migrate:
	$(PYTHON) manage.py migrate --noinput

migration-check:
	$(PYTHON) manage.py makemigrations --check --dry-run

format-check:
	.venv/bin/ruff format --check .

lint:
	.venv/bin/ruff check .

type:
	.venv/bin/mypy config grocery manage.py

test:
	.venv/bin/pytest

check: format-check lint type migration-check test
	$(PYTHON) manage.py check

serve:
	.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2 --threads 4
