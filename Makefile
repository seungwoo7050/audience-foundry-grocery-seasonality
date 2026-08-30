UV_RUN := env UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools UV_PYTHON_INSTALL_DIR=.cache/python uvx --from uv==0.12.6 uv
PYTHON := .venv/bin/python

# production-check requires explicit DJANGO_DEBUG=0, ADMIN_ENABLED=0,
# DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS,
# DATABASE_URL, and the exact 40-character lowercase release DEPLOY_VERSION.
# Its secret-check reads the ignored owner-only .env.local in-process; do not export
# KAMIS_API_KEY into Make, a command argument, or a child-process environment.
.PHONY: check db-up dependency-audit format-check license-inventory lint local-release-db-check migrate migration-check production-check production-env-check runtime-sync secret-check serve sync test type

sync:
	$(UV_RUN) sync --frozen

runtime-sync:
	$(UV_RUN) sync --frozen --no-dev

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
	.venv/bin/mypy config grocery scripts manage.py

test:
	.venv/bin/pytest

check: format-check lint type migration-check test
	$(PYTHON) manage.py check

secret-check:
	$(PYTHON) -m scripts.secret_check

dependency-audit:
	.venv/bin/pip-audit --local --progress-spinner off

license-inventory:
	.venv/bin/pip-licenses --format=plain --with-urls

production-env-check:
	@case "$${DJANGO_DEBUG:-}" in 0|false|no|off) ;; *) echo "production_check=failed code=debug_must_be_disabled"; exit 2;; esac
	@test "$${ADMIN_ENABLED:-}" = "0" || { echo "production_check=failed code=admin_must_be_disabled"; exit 2; }
	@test -n "$${DATABASE_URL:-}" || { echo "production_check=failed code=database_url_required"; exit 2; }
	@test "$${#DEPLOY_VERSION}" -eq 40 || { echo "production_check=failed code=release_sha_required"; exit 2; }
	@case "$${DEPLOY_VERSION}" in *[!0-9a-f]*) echo "production_check=failed code=release_sha_required"; exit 2;; esac

local-release-db-check:
	$(PYTHON) -m scripts.local_release_database_check

production-check: production-env-check local-release-db-check check secret-check dependency-audit license-inventory
	$(PYTHON) manage.py check --deploy --fail-level WARNING

serve:
	.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2 --threads 4
