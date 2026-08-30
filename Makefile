UV_RUN := env UV_CACHE_DIR=.cache/uv UV_TOOL_DIR=.cache/uv-tools UV_PYTHON_INSTALL_DIR=.cache/python uvx --from uv==0.12.6 uv
PYTHON := .venv/bin/python

# The source credential belongs only to the explicit ingestion process. Even if a
# developer exported it in the parent shell, no Make recipe or assurance tool may
# inherit it. secret-check reads the ignored owner-only file in its own process.
unexport KAMIS_API_KEY

# production-check requires explicit DJANGO_DEBUG=0, ADMIN_ENABLED=0,
# DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS,
# DATABASE_URL, and the exact 40-character lowercase release DEPLOY_VERSION.
# Its secret-check reads the ignored owner-only .env.local in-process; do not export
# KAMIS_API_KEY into Make, a command argument, or a child-process environment.
.PHONY: check db-up dependency-audit format-check license-inventory lint local-release-db-check migrate migration-check production-check production-env-check runtime-sync secret-check serve source-secret-env-check sync test type

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
	# Keep the test runtime deterministic even when production-check is invoked with
	# HTTPS redirect, HSTS, and Admin disabled. Production settings are validated by
	# production-env-check and the final check --deploy gate below.
	env DJANGO_DEBUG=1 ADMIN_ENABLED=1 QA_STATE_PREVIEWS_ENABLED=0 \
		CONTROL_PLANE_OPERATIONS_ENABLED=0 DJANGO_SECURE_SSL_REDIRECT=0 \
		DJANGO_SECURE_HSTS_SECONDS=0 DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=0 \
		DJANGO_SECURE_HSTS_PRELOAD=0 DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver \
		DEPLOY_VERSION=0000000 .venv/bin/pytest

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

source-secret-env-check:
	@test -z "$${KAMIS_API_KEY+x}" || { echo "source_secret_environment=failed code=ambient_source_secret_inherited"; exit 2; }
	@echo "source_secret_environment=absent"

local-release-db-check:
	$(PYTHON) -m scripts.local_release_database_check

production-check: source-secret-env-check production-env-check local-release-db-check check secret-check dependency-audit license-inventory
	$(PYTHON) manage.py check --deploy --fail-level WARNING

serve:
	.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2 --threads 4
