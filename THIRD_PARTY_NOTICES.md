# 제3자 고지

이 프로젝트는 아래 구성 요소를 직접 또는 전이 의존성으로 사용한다. Python package의
artifact URL과 SHA-256은 `uv.lock`에 고정한다. container는 tag와 multi-platform index
digest를 함께 고정한다. 이 고지는 각 upstream license 원문을 대체하지 않는다.

| 구성 요소 | 고정 버전 | 사용 목적 | upstream | license |
|---|---:|---|---|---|
| Python | `3.14.7` | application runtime | `python.org` | PSF-2.0 |
| Django | `5.2.17` | SSR, Forms, Auth, ORM, migration | `djangoproject.com` | BSD-3-Clause |
| Gunicorn | `23.0.0` | 고정 production WSGI process | `gunicorn.org` | MIT |
| WhiteNoise | `6.12.0` | 해시·압축된 WSGI static asset 제공 | `whitenoise.readthedocs.io` | MIT |
| Psycopg | `3.3.4` | PostgreSQL driver | `psycopg.org` | LGPL-3.0-only |
| psycopg-binary | `3.3.4` | local candidate의 self-contained libpq runtime | `psycopg.org` | LGPL-3.0-only 및 wheel 내 고지 |
| asgiref | `3.12.1` | Django 전이 runtime | `github.com/django/asgiref` | BSD-3-Clause |
| packaging | `26.3` | Gunicorn 전이 runtime | `github.com/pypa/packaging` | Apache-2.0 OR BSD-2-Clause |
| sqlparse | `0.6.0` | Django 전이 runtime | `github.com/andialbrecht/sqlparse` | BSD-3-Clause |
| tzdata | `2026.3` | Windows 조건부 timezone data | `github.com/python/tzdata` | Apache-2.0 |
| PostgreSQL official image | `18.6` | local DB·migration·restore rehearsal | `docker.io/library/postgres` | PostgreSQL License 및 image 내 고지 |
| uv | `0.12.6` | Python·dependency·lock 실행 도구 | `github.com/astral-sh/uv` | Apache-2.0 OR MIT |
| Hahmlet Bold | upstream commit `f9c5dac25d88015e9f0953253cec1a71854b7d24` | wordmark·큰 제목의 self-hosted webfont | `github.com/hyper-type/hahmlet` | SIL Open Font License 1.1 |

개발·검증 환경에는 다음 직접 도구를 사용한다. 이들은 production dependency group에
포함되지 않는다.

| 구성 요소 | 고정 버전 | 사용 목적 | license |
|---|---:|---|---|
| django-stubs | `6.1.0` | Django type checking | MIT |
| mypy | `2.3.1` | static type check | MIT |
| pip-audit | `2.10.1` | Python vulnerability scan | Apache-2.0 |
| pip-licenses | `5.5.5` | 설치 dependency license inventory | MIT |
| pytest | `9.1.1` | test runner | MIT |
| pytest-cov | `7.1.0` | coverage integration | MIT |
| pytest-django | `4.14.0` | Django test integration | BSD-3-Clause |
| Ruff | `0.16.5` | formatter·linter | MIT |
| @playwright/cli | `0.1.18` | 실제 Chromium browser E2E·screenshot 검증 | Apache-2.0 |
| Chromium | `152.0.7977.8` | Playwright가 관리하는 browser 검증 runtime | BSD-style 및 bundled component 고지 |
| @axe-core/cli | `4.13.0` | 렌더링된 page의 WCAG A·AA 자동 검사 | MPL-2.0 및 bundled third-party 고지 |

PostgreSQL image index digest는
`sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280`다.
`psycopg-binary`는 local candidate의 재현성을 위해 사용하며 bundle의 libpq·OpenSSL 등
고지는 설치 wheel의 `licenses/`와 함께 배포한다. production platform이 system libpq를
관리할 수 있으면 별도 호환성·license 검토 후 `psycopg[c]`로 전환하는 것은 새 기술 결정이다.

프로젝트는 이 package source를 vendoring하거나 수정하지 않는다. vulnerability scan은
license·provenance 검토를 대체하지 않으며, release gate에서 lock 전체를 다시 검사한다.
Playwright·Chromium·axe-core는 `uv.lock`이나 production artifact에 포함하지 않는 별도
assurance 도구다. browser evidence를 재생성하는 환경은 각 배포물의 upstream license와
bundled notice를 함께 보존한다.

Hahmlet Bold는 위 upstream commit의 `fonts/webfonts/Hahmlet-Bold.woff2`를 수정하거나 glyph
subset하지 않고 `grocery/static/grocery/fonts/hahmlet-bold.woff2`에 포함한다. 배포 WOFF2
SHA-256은 `9a5ab61f43a689167d0dea3046003bc3a897f32ab3af7c437add32075c15c948`이며,
upstream의 완전한 license 원문은 `LICENSES/Hahmlet-OFL-1.1.txt`에 보존한다. 원격 font
service나 CDN은 사용하지 않는다.
