"""Fail closed unless a release assurance run targets the fixed local database."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def is_fixed_local_release_database(value: object) -> bool:
    """Recognize only the repository's loopback Compose PostgreSQL contract."""

    if type(value) is not str:
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"postgres", "postgresql"}
        and parsed.hostname == "127.0.0.1"
        and port == 55_434
        and parsed.path == "/grocery"
        and parsed.username == "grocery"
        # This is the public Compose-only development credential in compose.yaml.
        and parsed.password == "local-grocery-only"  # noqa: S105
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def main() -> int:
    if not is_fixed_local_release_database(os.environ.get("DATABASE_URL")):
        print("local_release_database=failed code=fixed_loopback_database_required")
        return 2
    print("local_release_database=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
