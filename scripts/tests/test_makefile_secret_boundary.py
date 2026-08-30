"""Regression coverage for the Make-level source credential boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_make_recipes_do_not_inherit_ambient_source_secret() -> None:
    repository = Path(__file__).resolve().parents[2]
    marker = "synthetic-source-secret-must-not-cross-make-boundary"
    environment = os.environ.copy()
    environment["KAMIS_API_KEY"] = marker

    completed = subprocess.run(  # noqa: S603 - fixed local make target.
        ("/usr/bin/make", "source-secret-env-check"),
        cwd=repository,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "source_secret_environment=absent"
    assert marker not in completed.stdout
    assert marker not in completed.stderr
