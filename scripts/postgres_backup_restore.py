"""Local PostgreSQL custom-format backup and isolated restore assurance.

The command is deliberately bound to the repository's fixed Docker Compose ``db``
service. Database credentials and ``DATABASE_URL`` are neither read nor forwarded.
All subprocess output is captured or discarded and converted to fixed error codes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Never

_SOURCE_DATABASE: Final = "grocery"
_DATABASE_USER: Final = "grocery"
_COMPOSE_SERVICE: Final = "db"
_FORMAT_VERSION: Final = "grocery-postgres-custom-v1"
_POSTGRES_MAJOR: Final = 18
_DUMP_FILENAME: Final = "database.dump"
_MANIFEST_FILENAME: Final = "manifest.json"
_DUMP_MAGIC: Final = b"PGDMP"
_MAX_MANIFEST_BYTES: Final = 4 * 1024 * 1024
_MAX_INVENTORY_BYTES: Final = 4 * 1024 * 1024
_MAX_DUMP_BYTES: Final = 8 * 1024 * 1024 * 1024
_MAX_TABLES: Final = 1_024
_MAX_MIGRATIONS: Final = 16_384
_MAX_ROW_COUNT: Final = (2**63) - 1
_MAX_ACTIVATIONS: Final = 10_000
_MAX_PUBLICATION_ENTRIES: Final = 100_000
_TOOL_TIMEOUT_SECONDS: Final = 30
_INVENTORY_TIMEOUT_SECONDS: Final = 120
_DUMP_TIMEOUT_SECONDS: Final = 600
_RESTORE_TIMEOUT_SECONDS: Final = 600
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TABLE_NAME = re.compile(r"public\.[a-z0-9_]{1,63}\Z")
_MIGRATION_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_TARGET_DATABASE = re.compile(r"grocery_restore_[a-z0-9][a-z0-9_]{0,45}\Z")
_VERSION_TOKEN = re.compile(r"([0-9]+)(?:\.[0-9]+)*\Z")

_INVENTORY_SQL: Final = r"""
CREATE TEMP TABLE assurance_counts (
    table_name text PRIMARY KEY,
    row_count bigint NOT NULL
) ON COMMIT DROP;
DO $assurance$
DECLARE
    table_record record;
BEGIN
    FOR table_record IN
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY schemaname, tablename
    LOOP
        EXECUTE format(
            'INSERT INTO assurance_counts(table_name, row_count) '
            'SELECT %L, count(*) FROM %I.%I',
            table_record.schemaname || '.' || table_record.tablename,
            table_record.schemaname,
            table_record.tablename
        );
    END LOOP;
END
$assurance$;
SELECT json_build_object(
    'rows', COALESCE(
        (
            SELECT json_object_agg(table_name, row_count ORDER BY table_name)
            FROM assurance_counts
        ),
        '{}'::json
    ),
    'migrations', COALESCE(
        (
            SELECT json_agg(json_build_array(app, name) ORDER BY app, name)
            FROM public.django_migrations
        ),
        '[]'::json
    ),
    'publication', json_build_object(
        'channel', (
            SELECT json_build_object(
                'channel', channel,
                'version', version,
                'current_revision_id', current_revision_id
            )
            FROM public.grocery_publicationchannel
            WHERE channel = 'RECENT_RETAIL'
        ),
        'active_revision', (
            SELECT json_build_object(
                'id', revision.id,
                'typed_fact_set_sha256', revision.typed_fact_set_sha256,
                'generation_id', revision.generation_id,
                'review_decision_id', revision.review_decision_id,
                'review_parse_run_id', decision.parse_run_id,
                'review_decision', decision.decision,
                'entry_count', revision.entry_count
            )
            FROM public.grocery_publicationchannel AS channel
            JOIN public.grocery_publicationrevision AS revision
              ON revision.id = channel.current_revision_id
             AND revision.channel = 'RECENT_RETAIL'
             AND revision.sealed_at IS NOT NULL
            JOIN public.grocery_reviewdecision AS decision
              ON decision.id = revision.review_decision_id
            JOIN public.grocery_parserun AS generation
              ON generation.id = revision.generation_id
            WHERE channel.channel = 'RECENT_RETAIL'
              AND decision.decision = 'APPROVE'
              AND decision.parse_run_id = generation.id
              AND decision.approved_mode = 'RECENT_COMPARISON'
              AND generation.status = 'VALIDATED'
              AND generation.accepted_row_count = revision.entry_count
              AND revision.entry_count = (
                  SELECT count(*)
                  FROM public.grocery_publicationentry AS entry
                  WHERE entry.revision_id = revision.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.grocery_reviewdecision AS replacement
                  WHERE replacement.supersedes_id = decision.id
              )
        ),
        'activations', COALESCE(
            (
                SELECT json_agg(
                    json_build_object(
                        'id', activation.id,
                        'operation', activation.operation,
                        'sequence', activation.sequence,
                        'previous_revision_id', activation.previous_revision_id,
                        'target_revision_id', activation.target_revision_id,
                        'reason_code', activation.reason_code,
                        'acceptance_evidence_sha256',
                            activation.acceptance_evidence_sha256
                    )
                    ORDER BY activation.sequence
                )
                FROM public.grocery_publicationactivation AS activation
                WHERE activation.channel_id = 'RECENT_RETAIL'
            ),
            '[]'::json
        )
    )
)::text;
"""

_CODES: Final = frozenset(
    {
        "backup_changed_during_dump",
        "backup_directory_invalid",
        "backup_directory_permissions",
        "backup_directory_unavailable",
        "backup_id_invalid",
        "backup_manifest_invalid",
        "backup_not_custom_format",
        "backup_too_large",
        "checksum_mismatch",
        "compose_file_missing",
        "create_target_failed",
        "destination_inside_repository",
        "docker_command_failed",
        "docker_unavailable",
        "dump_failed",
        "dump_file_invalid",
        "internal_error",
        "inventory_invalid",
        "inventory_mismatch",
        "migration_mismatch",
        "postgres_tool_missing",
        "postgres_version_mismatch",
        "publication_contract_mismatch",
        "repository_invalid",
        "restore_failed",
        "row_count_mismatch",
        "source_database_unavailable",
        "target_database_exists",
        "target_database_invalid",
        "target_database_is_source",
        "target_preflight_failed",
        "usage_error",
    }
)


class BackupRestoreError(RuntimeError):
    """A failure represented only by a fixed, non-sensitive code."""

    def __init__(self, code: str) -> None:
        selected = code if code in _CODES else "internal_error"
        self.code = selected
        super().__init__(selected)


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject invalid CLI shapes without reflecting supplied argument text."""

    def error(self, message: str) -> Never:
        del message
        raise BackupRestoreError("usage_error")


@dataclass(frozen=True, slots=True)
class Inventory:
    rows: dict[str, int]
    migrations: tuple[tuple[str, str], ...]
    publication: dict[str, object]

    def canonical_data(self) -> dict[str, object]:
        return {
            "migrations": [list(migration) for migration in self.migrations],
            "publication": self.publication,
            "rows": dict(sorted(self.rows.items())),
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_data())).hexdigest()

    @property
    def publication_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.publication)).hexdigest()


@dataclass(frozen=True, slots=True)
class BackupReceipt:
    backup_id: uuid.UUID
    dump_sha256: str
    manifest_sha256: str
    table_count: int
    migration_count: int

    def render(self) -> str:
        return "\n".join(
            (
                "status=backup_complete",
                f"backup_id={self.backup_id}",
                f"dump_sha256={self.dump_sha256}",
                f"manifest_sha256={self.manifest_sha256}",
                f"tables={self.table_count}",
                f"migrations={self.migration_count}",
                "cleanup=retain_or_remove_explicit_backup_directory",
            )
        )


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    backup_id: uuid.UUID
    table_count: int
    migration_count: int

    def render(self) -> str:
        return "\n".join(
            (
                "status=restore_verified",
                f"backup_id={self.backup_id}",
                "target_database_verified=yes",
                "row_counts_consistent=yes",
                "migrations_consistent=yes",
                "publication_contract_consistent=yes",
                f"tables={self.table_count}",
                f"migrations={self.migration_count}",
                "cleanup=drop_explicit_restore_target_after_review",
            )
        )


@dataclass(frozen=True, slots=True)
class LoadedBackup:
    backup_id: uuid.UUID
    dump_path: Path
    inventory: Inventory


def create_backup(*, repository: Path, destination_root: Path) -> BackupReceipt:
    root = _repository_root(repository)
    destination = _operator_directory(destination_root, repository=root)
    _preflight(root)
    backup_id = uuid.uuid4()
    backup_directory = destination / f"postgres-backup-{backup_id}"

    with _restrictive_umask():
        try:
            backup_directory.mkdir(mode=0o700)
        except OSError:
            raise BackupRestoreError("backup_directory_unavailable") from None
        _require_private_directory(backup_directory)
        dump_path = backup_directory / _DUMP_FILENAME
        before = _read_inventory(root, _SOURCE_DATABASE)
        dump_fd = _create_private_file(dump_path)
        try:
            _dump_database(root, dump_fd)
            os.fsync(dump_fd)
        finally:
            os.close(dump_fd)
        _require_private_regular_file(dump_path, maximum_bytes=_MAX_DUMP_BYTES)
        if _read_prefix(dump_path, len(_DUMP_MAGIC)) != _DUMP_MAGIC:
            raise BackupRestoreError("backup_not_custom_format")
        after = _read_inventory(root, _SOURCE_DATABASE)
        if before != after:
            raise BackupRestoreError("backup_changed_during_dump")

        dump_sha256, dump_bytes = _file_sha256(dump_path)
        manifest = {
            "backup_id": str(backup_id),
            "created_at": datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "dump": {
                "bytes": dump_bytes,
                "filename": _DUMP_FILENAME,
                "sha256": dump_sha256,
            },
            "format_version": _FORMAT_VERSION,
            "inventory": {
                **before.canonical_data(),
                "publication_sha256": before.publication_sha256,
                "sha256": before.sha256,
            },
            "postgres_major": _POSTGRES_MAJOR,
            "source_database": _SOURCE_DATABASE,
        }
        manifest_bytes = _canonical_json(manifest) + b"\n"
        manifest_path = backup_directory / _MANIFEST_FILENAME
        _write_private_file(manifest_path, manifest_bytes)
        _fsync_directory(backup_directory)

    return BackupReceipt(
        backup_id=backup_id,
        dump_sha256=dump_sha256,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        table_count=len(before.rows),
        migration_count=len(before.migrations),
    )


def restore_backup(
    *,
    repository: Path,
    backup_directory: Path,
    target_database: str,
) -> RestoreReceipt:
    root = _repository_root(repository)
    target = _validated_target_database(target_database)
    selected_backup = _load_backup(backup_directory, repository=root)
    _preflight(root)
    _validate_custom_dump(root, selected_backup.dump_path)
    if _database_exists(root, target):
        raise BackupRestoreError("target_database_exists")
    _create_target_database(root, target)
    _restore_database(root, selected_backup.dump_path, target)
    restored = _read_inventory(root, target)
    if restored.rows != selected_backup.inventory.rows:
        raise BackupRestoreError("row_count_mismatch")
    if restored.migrations != selected_backup.inventory.migrations:
        raise BackupRestoreError("migration_mismatch")
    if (
        restored.publication != selected_backup.inventory.publication
        or restored.publication_sha256 != selected_backup.inventory.publication_sha256
    ):
        raise BackupRestoreError("publication_contract_mismatch")
    if restored.sha256 != selected_backup.inventory.sha256:
        raise BackupRestoreError("inventory_mismatch")
    return RestoreReceipt(
        backup_id=selected_backup.backup_id,
        table_count=len(restored.rows),
        migration_count=len(restored.migrations),
    )


def main(arguments: list[str] | None = None) -> int:
    parser = _parser()
    selected = sys.argv[1:] if arguments is None else arguments
    try:
        parsed = parser.parse_args(selected)
    except BackupRestoreError:
        _print_failure("usage_error", restore_requested="restore" in selected)
        return 2

    restore_requested = parsed.operation == "restore"
    try:
        receipt: BackupReceipt | RestoreReceipt
        if parsed.operation == "backup":
            receipt = create_backup(
                repository=Path.cwd(),
                destination_root=Path(parsed.output_dir),
            )
        else:
            receipt = restore_backup(
                repository=Path.cwd(),
                backup_directory=Path(parsed.backup_dir),
                target_database=parsed.target_database,
            )
    except BackupRestoreError as error:
        _print_failure(error.code, restore_requested=restore_requested)
        return 1
    except Exception:  # noqa: BLE001 - CLI must not reflect dependency or secret text.
        _print_failure("internal_error", restore_requested=restore_requested)
        return 1
    print(receipt.render())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--output-dir", required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--backup-dir", required=True)
    restore.add_argument("--target-database", required=True)
    return parser


def _print_failure(code: str, *, restore_requested: bool) -> None:
    selected = code if code in _CODES else "internal_error"
    print("status=failed")
    print(f"code={selected}")
    if restore_requested:
        print("cleanup=inspect_and_drop_explicit_restore_target_if_created")
    else:
        print("cleanup=remove_incomplete_backup_directory_if_created")


def _repository_root(repository: Path) -> Path:
    try:
        root = repository.resolve(strict=True)
        compose = root / "compose.yaml"
        if not root.is_dir():
            raise OSError
        metadata = compose.lstat()
    except OSError:
        raise BackupRestoreError("repository_invalid") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BackupRestoreError("compose_file_missing")
    return root


def _operator_directory(path: Path, *, repository: Path) -> Path:
    if not path.is_absolute():
        raise BackupRestoreError("backup_directory_invalid")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BackupRestoreError("backup_directory_invalid")
    if metadata.st_uid != os.geteuid():
        raise BackupRestoreError("backup_directory_permissions")
    if _is_within(resolved, repository):
        raise BackupRestoreError("destination_inside_repository")
    return resolved


def _validated_target_database(value: object) -> str:
    if value == _SOURCE_DATABASE:
        raise BackupRestoreError("target_database_is_source")
    if not isinstance(value, str) or _TARGET_DATABASE.fullmatch(value) is None:
        raise BackupRestoreError("target_database_invalid")
    return value


def _preflight(repository: Path) -> None:
    if shutil.which("docker") is None:
        raise BackupRestoreError("docker_unavailable")
    for tool in ("pg_dump", "pg_restore", "createdb", "psql"):
        output = _capture_compose(
            repository,
            (tool, "--version"),
            timeout=_TOOL_TIMEOUT_SECONDS,
            failure_code="postgres_tool_missing",
        )
        try:
            version = output.decode("ascii", errors="strict").strip()
        except UnicodeError:
            raise BackupRestoreError("postgres_version_mismatch") from None
        prefix = f"{tool} (PostgreSQL) "
        if not version.startswith(prefix) or len(version) > 256:
            raise BackupRestoreError("postgres_version_mismatch")
        version_token = version.removeprefix(prefix).split(maxsplit=1)[0]
        match = _VERSION_TOKEN.fullmatch(version_token)
        if match is None or int(match.group(1)) != _POSTGRES_MAJOR:
            raise BackupRestoreError("postgres_version_mismatch")
    source_probe = _capture_compose(
        repository,
        (
            "psql",
            "--no-password",
            f"--username={_DATABASE_USER}",
            f"--dbname={_SOURCE_DATABASE}",
            "--tuples-only",
            "--no-align",
            "--command=SELECT 1;",
        ),
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="source_database_unavailable",
    )
    if source_probe.strip() != b"1":
        raise BackupRestoreError("source_database_unavailable")


def _dump_database(repository: Path, output_fd: int) -> None:
    _run_compose(
        repository,
        (
            "pg_dump",
            "--no-password",
            f"--username={_DATABASE_USER}",
            f"--dbname={_SOURCE_DATABASE}",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ),
        stdin=subprocess.DEVNULL,
        stdout=output_fd,
        timeout=_DUMP_TIMEOUT_SECONDS,
        failure_code="dump_failed",
    )


def _validate_custom_dump(repository: Path, dump_path: Path) -> None:
    descriptor = os.open(
        dump_path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _run_compose(
            repository,
            ("pg_restore", "--list"),
            stdin=descriptor,
            stdout=subprocess.DEVNULL,
            timeout=_INVENTORY_TIMEOUT_SECONDS,
            failure_code="backup_not_custom_format",
        )
    finally:
        os.close(descriptor)


def _database_exists(repository: Path, target: str) -> bool:
    output = _capture_compose(
        repository,
        (
            "psql",
            "--no-password",
            f"--username={_DATABASE_USER}",
            "--dbname=postgres",
            "--tuples-only",
            "--no-align",
            # ``psql --command`` does not perform psql-variable interpolation.
            # The target has already passed the strict lowercase/underscore
            # allowlist in ``_validated_target_database`` and is therefore safe
            # to place in this fixed string literal.
            f"--command=SELECT count(*) FROM pg_database WHERE datname = '{target}';",  # noqa: S608
        ),
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="target_preflight_failed",
    )
    result = output.strip()
    if result not in {b"0", b"1"}:
        raise BackupRestoreError("target_preflight_failed")
    return result == b"1"


def _create_target_database(repository: Path, target: str) -> None:
    _run_compose(
        repository,
        (
            "createdb",
            "--no-password",
            f"--username={_DATABASE_USER}",
            f"--owner={_DATABASE_USER}",
            "--template=template0",
            "--encoding=UTF8",
            target,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="create_target_failed",
    )


def _restore_database(repository: Path, dump_path: Path, target: str) -> None:
    descriptor = os.open(
        dump_path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _run_compose(
            repository,
            (
                "pg_restore",
                "--no-password",
                f"--username={_DATABASE_USER}",
                f"--dbname={target}",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
            ),
            stdin=descriptor,
            stdout=subprocess.DEVNULL,
            timeout=_RESTORE_TIMEOUT_SECONDS,
            failure_code="restore_failed",
        )
    finally:
        os.close(descriptor)


def _read_inventory(repository: Path, database: str) -> Inventory:
    output = _capture_compose(
        repository,
        (
            "psql",
            "--no-password",
            f"--username={_DATABASE_USER}",
            f"--dbname={database}",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            f"--command={_INVENTORY_SQL}",
        ),
        timeout=_INVENTORY_TIMEOUT_SECONDS,
        failure_code="inventory_invalid",
    )
    if len(output) > _MAX_INVENTORY_BYTES:
        raise BackupRestoreError("inventory_invalid")
    try:
        decoded = json.loads(output.decode("utf-8", errors="strict"))
    except UnicodeError, json.JSONDecodeError:
        raise BackupRestoreError("inventory_invalid") from None
    return _parse_inventory(decoded)


def _parse_inventory(value: object) -> Inventory:
    if not isinstance(value, dict) or set(value) != {"rows", "migrations", "publication"}:
        raise BackupRestoreError("inventory_invalid")
    raw_rows = value["rows"]
    raw_migrations = value["migrations"]
    raw_publication = value["publication"]
    if (
        not isinstance(raw_rows, dict)
        or len(raw_rows) < 1
        or len(raw_rows) > _MAX_TABLES
        or not isinstance(raw_migrations, list)
        or len(raw_migrations) < 1
        or len(raw_migrations) > _MAX_MIGRATIONS
    ):
        raise BackupRestoreError("inventory_invalid")
    rows: dict[str, int] = {}
    for key, count in raw_rows.items():
        if (
            not isinstance(key, str)
            or _TABLE_NAME.fullmatch(key) is None
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or count > _MAX_ROW_COUNT
        ):
            raise BackupRestoreError("inventory_invalid")
        rows[key] = count
    migrations: list[tuple[str, str]] = []
    for raw in raw_migrations:
        if (
            not isinstance(raw, list)
            or len(raw) != 2
            or not all(isinstance(token, str) for token in raw)
        ):
            raise BackupRestoreError("inventory_invalid")
        app, name = raw
        if _MIGRATION_TOKEN.fullmatch(app) is None or _MIGRATION_TOKEN.fullmatch(name) is None:
            raise BackupRestoreError("inventory_invalid")
        migrations.append((app, name))
    ordered_migrations = tuple(sorted(migrations))
    if len(set(ordered_migrations)) != len(ordered_migrations):
        raise BackupRestoreError("inventory_invalid")
    publication = _parse_publication_contract(raw_publication)
    return Inventory(
        rows=dict(sorted(rows.items())),
        migrations=ordered_migrations,
        publication=publication,
    )


def _parse_publication_contract(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "channel",
        "active_revision",
        "activations",
    }:
        raise BackupRestoreError("inventory_invalid")
    raw_channel = value["channel"]
    raw_revision = value["active_revision"]
    raw_activations = value["activations"]
    if not isinstance(raw_channel, dict) or set(raw_channel) != {
        "channel",
        "version",
        "current_revision_id",
    }:
        raise BackupRestoreError("inventory_invalid")
    if raw_channel["channel"] != "RECENT_RETAIL":
        raise BackupRestoreError("inventory_invalid")
    version = raw_channel["version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or version > _MAX_ACTIVATIONS
    ):
        raise BackupRestoreError("inventory_invalid")
    current_revision_id = _canonical_uuid_text(raw_channel["current_revision_id"])

    if not isinstance(raw_revision, dict) or set(raw_revision) != {
        "id",
        "typed_fact_set_sha256",
        "generation_id",
        "review_decision_id",
        "review_parse_run_id",
        "review_decision",
        "entry_count",
    }:
        raise BackupRestoreError("inventory_invalid")
    revision_id = _canonical_uuid_text(raw_revision["id"])
    generation_id = _canonical_uuid_text(raw_revision["generation_id"])
    review_decision_id = _canonical_uuid_text(raw_revision["review_decision_id"])
    review_parse_run_id = _canonical_uuid_text(raw_revision["review_parse_run_id"])
    typed_fact_set_sha256 = _canonical_sha256_text(raw_revision["typed_fact_set_sha256"])
    entry_count = raw_revision["entry_count"]
    if (
        revision_id != current_revision_id
        or generation_id != review_parse_run_id
        or raw_revision["review_decision"] != "APPROVE"
        or not isinstance(entry_count, int)
        or isinstance(entry_count, bool)
        or entry_count < 1
        or entry_count > _MAX_PUBLICATION_ENTRIES
    ):
        raise BackupRestoreError("inventory_invalid")

    if (
        not isinstance(raw_activations, list)
        or len(raw_activations) != version
        or len(raw_activations) > _MAX_ACTIVATIONS
    ):
        raise BackupRestoreError("inventory_invalid")
    activations: list[dict[str, object]] = []
    derived_current: str | None = None
    prior_targets: set[str] = set()
    seen_ids: set[str] = set()
    for expected_sequence, raw_activation in enumerate(raw_activations, start=1):
        parsed = _parse_activation(raw_activation, expected_sequence=expected_sequence)
        activation_id = parsed["id"]
        operation = parsed["operation"]
        target = parsed["target_revision_id"]
        if (
            not isinstance(activation_id, str)
            or not isinstance(operation, str)
            or (target is not None and not isinstance(target, str))
        ):
            raise BackupRestoreError("inventory_invalid")
        if activation_id in seen_ids or parsed["previous_revision_id"] != derived_current:
            raise BackupRestoreError("inventory_invalid")
        seen_ids.add(activation_id)
        if operation == "WITHDRAW":
            if derived_current is None or target is not None:
                raise BackupRestoreError("inventory_invalid")
        else:
            if target is None or target == derived_current:
                raise BackupRestoreError("inventory_invalid")
            if operation == "ROLLBACK" and target not in prior_targets:
                raise BackupRestoreError("inventory_invalid")
            prior_targets.add(target)
        derived_current = target if isinstance(target, str) else None
        activations.append(parsed)
    if derived_current != current_revision_id:
        raise BackupRestoreError("inventory_invalid")

    return {
        "active_revision": {
            "entry_count": entry_count,
            "generation_id": generation_id,
            "id": revision_id,
            "review_decision": "APPROVE",
            "review_decision_id": review_decision_id,
            "review_parse_run_id": review_parse_run_id,
            "typed_fact_set_sha256": typed_fact_set_sha256,
        },
        "activations": activations,
        "channel": {
            "channel": "RECENT_RETAIL",
            "current_revision_id": current_revision_id,
            "version": version,
        },
    }


def _parse_activation(value: object, *, expected_sequence: int) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "id",
        "operation",
        "sequence",
        "previous_revision_id",
        "target_revision_id",
        "reason_code",
        "acceptance_evidence_sha256",
    }:
        raise BackupRestoreError("inventory_invalid")
    operation = value["operation"]
    if not isinstance(operation, str) or operation not in {
        "ACTIVATE",
        "ROLLBACK",
        "WITHDRAW",
    }:
        raise BackupRestoreError("inventory_invalid")
    sequence = value["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence != expected_sequence:
        raise BackupRestoreError("inventory_invalid")
    reason_code = value["reason_code"]
    if not isinstance(reason_code, str) or _REASON_CODE.fullmatch(reason_code) is None:
        raise BackupRestoreError("inventory_invalid")
    return {
        "acceptance_evidence_sha256": _canonical_sha256_text(value["acceptance_evidence_sha256"]),
        "id": _canonical_uuid_text(value["id"]),
        "operation": operation,
        "previous_revision_id": _nullable_uuid_text(value["previous_revision_id"]),
        "reason_code": reason_code,
        "sequence": expected_sequence,
        "target_revision_id": _nullable_uuid_text(value["target_revision_id"]),
    }


def _canonical_uuid_text(value: object) -> str:
    if not isinstance(value, str):
        raise BackupRestoreError("inventory_invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise BackupRestoreError("inventory_invalid") from None
    if str(parsed) != value:
        raise BackupRestoreError("inventory_invalid")
    return value


def _nullable_uuid_text(value: object) -> str | None:
    return None if value is None else _canonical_uuid_text(value)


def _canonical_sha256_text(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BackupRestoreError("inventory_invalid")
    return value


def _load_backup(path: Path, *, repository: Path) -> LoadedBackup:
    backup_directory = _operator_directory(path, repository=repository)
    _require_private_directory(backup_directory)
    manifest_path = backup_directory / _MANIFEST_FILENAME
    dump_path = backup_directory / _DUMP_FILENAME
    _require_private_regular_file(manifest_path, maximum_bytes=_MAX_MANIFEST_BYTES)
    _require_private_regular_file(dump_path, maximum_bytes=_MAX_DUMP_BYTES)
    manifest_bytes = _read_bounded(manifest_path, _MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
    except UnicodeError, json.JSONDecodeError:
        raise BackupRestoreError("backup_manifest_invalid") from None
    if not isinstance(manifest, dict) or set(manifest) != {
        "backup_id",
        "created_at",
        "dump",
        "format_version",
        "inventory",
        "postgres_major",
        "source_database",
    }:
        raise BackupRestoreError("backup_manifest_invalid")
    if (
        manifest["format_version"] != _FORMAT_VERSION
        or manifest["postgres_major"] != _POSTGRES_MAJOR
        or manifest["source_database"] != _SOURCE_DATABASE
        or not isinstance(manifest["created_at"], str)
    ):
        raise BackupRestoreError("backup_manifest_invalid")
    try:
        backup_id = uuid.UUID(manifest["backup_id"])
    except ValueError, AttributeError, TypeError:
        raise BackupRestoreError("backup_id_invalid") from None
    if str(backup_id) != manifest["backup_id"]:
        raise BackupRestoreError("backup_id_invalid")

    dump = manifest["dump"]
    inventory_value = manifest["inventory"]
    if not isinstance(dump, dict) or set(dump) != {"bytes", "filename", "sha256"}:
        raise BackupRestoreError("backup_manifest_invalid")
    if (
        dump["filename"] != _DUMP_FILENAME
        or not isinstance(dump["bytes"], int)
        or isinstance(dump["bytes"], bool)
        or dump["bytes"] < len(_DUMP_MAGIC)
        or dump["bytes"] > _MAX_DUMP_BYTES
        or not isinstance(dump["sha256"], str)
        or _SHA256.fullmatch(dump["sha256"]) is None
    ):
        raise BackupRestoreError("backup_manifest_invalid")
    dump_sha256, dump_bytes = _file_sha256(dump_path)
    if dump_sha256 != dump["sha256"] or dump_bytes != dump["bytes"]:
        raise BackupRestoreError("checksum_mismatch")
    if _read_prefix(dump_path, len(_DUMP_MAGIC)) != _DUMP_MAGIC:
        raise BackupRestoreError("backup_not_custom_format")

    if not isinstance(inventory_value, dict) or set(inventory_value) != {
        "migrations",
        "publication",
        "publication_sha256",
        "rows",
        "sha256",
    }:
        raise BackupRestoreError("backup_manifest_invalid")
    inventory = _parse_inventory(
        {
            "migrations": inventory_value["migrations"],
            "publication": inventory_value["publication"],
            "rows": inventory_value["rows"],
        }
    )
    publication_sha = inventory_value["publication_sha256"]
    if (
        not isinstance(publication_sha, str)
        or _SHA256.fullmatch(publication_sha) is None
        or inventory.publication_sha256 != publication_sha
    ):
        raise BackupRestoreError("publication_contract_mismatch")
    inventory_sha = inventory_value["sha256"]
    if (
        not isinstance(inventory_sha, str)
        or _SHA256.fullmatch(inventory_sha) is None
        or inventory.sha256 != inventory_sha
    ):
        raise BackupRestoreError("checksum_mismatch")
    return LoadedBackup(backup_id=backup_id, dump_path=dump_path, inventory=inventory)


def _capture_compose(
    repository: Path,
    tool_arguments: tuple[str, ...],
    *,
    timeout: int,
    failure_code: str,
) -> bytes:
    completed = _run_compose(
        repository,
        tool_arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        timeout=timeout,
        failure_code=failure_code,
    )
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) > _MAX_INVENTORY_BYTES:
        raise BackupRestoreError(failure_code)
    return output


def _run_compose(
    repository: Path,
    tool_arguments: tuple[str, ...],
    *,
    stdin: int,
    stdout: int,
    timeout: int,
    failure_code: str,
) -> subprocess.CompletedProcess[bytes]:
    docker = shutil.which("docker")
    if docker is None:
        raise BackupRestoreError("docker_unavailable")
    command = (
        docker,
        "compose",
        "exec",
        "-T",
        _COMPOSE_SERVICE,
        *tool_arguments,
    )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed docker + validated tool arguments.
            command,
            cwd=repository,
            env=_docker_environment(),
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except OSError, subprocess.SubprocessError:
        raise BackupRestoreError(failure_code) from None
    if completed.returncode != 0:
        raise BackupRestoreError(failure_code)
    return completed


def _docker_environment() -> dict[str, str]:
    allowed = ("DOCKER_CONTEXT", "DOCKER_HOST", "HOME", "PATH")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update({"LANG": "C", "LC_ALL": "C"})
    return environment


@contextmanager
def _restrictive_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _create_private_file(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path, flags, 0o600)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor = _create_private_file(path)
    try:
        written = 0
        while written < len(content):
            chunk_size = os.write(descriptor, content[written:])
            if chunk_size < 1:
                raise OSError
            written += chunk_size
        os.fsync(descriptor)
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None
    finally:
        os.close(descriptor)


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise BackupRestoreError("backup_directory_invalid") from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BackupRestoreError("backup_directory_permissions")


def _require_private_regular_file(path: Path, *, maximum_bytes: int) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BackupRestoreError("backup_directory_permissions")
    if metadata.st_size < 1 or metadata.st_size > maximum_bytes:
        raise BackupRestoreError("backup_too_large")


def _read_bounded(path: Path, maximum_bytes: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        data = os.read(descriptor, maximum_bytes + 1)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    finally:
        os.close(descriptor)
    if len(data) > maximum_bytes:
        raise BackupRestoreError("backup_too_large")
    return data


def _read_prefix(path: Path, length: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return os.read(descriptor, length)
    finally:
        os.close(descriptor)


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_DUMP_BYTES:
                raise BackupRestoreError("backup_too_large")
            digest.update(chunk)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None
    finally:
        os.close(descriptor)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
