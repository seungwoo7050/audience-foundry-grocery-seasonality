from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.secret_check import SecretCheckError, main, run_secret_check

_TEST_GIT_ENV = {
    "GIT_AUTHOR_EMAIL": "release-check@example.invalid",
    "GIT_AUTHOR_NAME": "Release Check",
    "GIT_COMMITTER_EMAIL": "release-check@example.invalid",
    "GIT_COMMITTER_NAME": "Release Check",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": os.defpath,
}


def git(repository: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable is not None
    subprocess.run(  # noqa: S603 - executable is resolved for a synthetic repository.
        (executable, *arguments),
        cwd=repository,
        env=_TEST_GIT_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )


def synthetic_secret() -> str:
    return "synthetic-release-credential-" + "q" * 23


def write_secret(repository: Path, value: str, *, mode: int = 0o600) -> Path:
    secret_path = repository / ".env.local"
    secret_path.write_text(f"KAMIS_API_KEY={value}\n", encoding="utf-8")
    secret_path.chmod(mode)
    return secret_path


def repository(tmp_path: Path, secret: str) -> Path:
    root = tmp_path / "synthetic-repository"
    root.mkdir()
    git(root, "init", "--quiet")
    (root / ".gitignore").write_text(".env.local\n", encoding="utf-8")
    (root / "tracked.txt").write_text("safe tracked content\n", encoding="utf-8")
    git(root, "add", ".gitignore", "tracked.txt")
    git(root, "commit", "--quiet", "-m", "initial")
    write_secret(root, secret)
    return root


def test_success_emits_only_fixed_receipt_fields(tmp_path: Path) -> None:
    secret = synthetic_secret()
    root = repository(tmp_path, secret)

    receipt = run_secret_check(root)

    assert receipt.render().splitlines() == [
        "present=yes",
        "ignored=yes",
        "permissions=ok",
        "current_match=no",
        "history_match=no",
    ]
    assert secret not in receipt.render()
    assert str(len(secret)) not in receipt.render()


def test_uncommitted_tracked_leak_fails_without_filename_or_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = synthetic_secret()
    root = repository(tmp_path, secret)
    leak_name = "tracked.txt"
    (root / leak_name).write_text(f"prefix:{secret}:suffix", encoding="utf-8")

    monkeypatch.chdir(root)
    exit_code = main([])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert output == "status=failed\ncode=current_match\n"
    assert secret not in output
    assert str(len(secret)) not in output
    assert leak_name not in output


def test_historical_only_leak_is_detected_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = synthetic_secret()
    root = repository(tmp_path, secret)
    leak_name = "historical-only.txt"
    leaked = root / leak_name
    leaked.write_text(secret, encoding="utf-8")
    git(root, "add", leak_name)
    git(root, "commit", "--quiet", "-m", "historical leak")
    leaked.write_text("replaced with safe bytes\n", encoding="utf-8")
    git(root, "add", leak_name)
    git(root, "commit", "--quiet", "-m", "remove leak from current tree")

    monkeypatch.chdir(root)
    exit_code = main([])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert output == "status=failed\ncode=history_match\n"
    assert secret not in output
    assert str(len(secret)) not in output
    assert leak_name not in output


def test_secret_is_never_sent_to_git_args_environment_or_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = synthetic_secret()
    root = repository(tmp_path, secret)
    real_run = subprocess.run

    def audited_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = args[0]
        environment = kwargs.get("env", {})
        stdin_value = kwargs.get("input")
        assert secret not in repr(command)
        assert all(secret not in str(value) for value in environment.values())
        assert stdin_value is None or secret.encode() not in stdin_value
        return real_run(*args, **kwargs)

    monkeypatch.setattr("scripts.secret_check.subprocess.run", audited_run)

    run_secret_check(root)


def test_fixed_git_boundary_collision_fails_before_spawning_child(tmp_path: Path) -> None:
    root = repository(tmp_path, "C")

    with pytest.raises(SecretCheckError) as caught:
        run_secret_check(root)

    assert caught.value.code == "git_boundary_collision"


@pytest.mark.parametrize(
    ("arrange", "code"),
    [
        (
            lambda root: (root / ".gitignore").write_text("", encoding="utf-8"),
            "secret_file_not_ignored",
        ),
        (lambda root: (root / ".env.local").chmod(0o640), "secret_file_permissions"),
    ],
)
def test_prerequisite_failure_uses_fixed_code(
    tmp_path: Path,
    arrange: Callable[[Path], object],
    code: str,
) -> None:
    secret = synthetic_secret()
    root = repository(tmp_path, secret)
    arrange(root)

    with pytest.raises(SecretCheckError) as caught:
        run_secret_check(root)

    assert caught.value.code == code
    assert str(caught.value) == code
    assert secret not in repr(caught.value)


def test_secret_file_symlink_is_rejected_without_following_target(tmp_path: Path) -> None:
    secret = synthetic_secret()
    root = repository(tmp_path, secret)
    secret_path = root / ".env.local"
    target = root / "outside-secret"
    target.write_text(f"KAMIS_API_KEY={secret}\n", encoding="utf-8")
    target.chmod(0o600)
    secret_path.unlink()
    secret_path.symlink_to(target)

    with pytest.raises(SecretCheckError) as caught:
        run_secret_check(root)

    assert caught.value.code == "secret_file_symlink"
    assert secret not in repr(caught.value)
