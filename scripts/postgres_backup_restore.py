"""Local PostgreSQL custom-format backup and isolated restore assurance.

The command talks directly to one fully inspected local Docker container over the fixed
Unix socket. It does not use a mutable Docker context or the Compose plugin. Ambient
database, Docker, and KAMIS credentials are neither read nor forwarded. All subprocess
output is captured or discarded and converted to fixed error codes.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
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
_COMPOSE_PROJECT: Final = "audience-foundry-grocery-seasonality"
_COMPOSE_SERVICE: Final = "db"
_LOCAL_DOCKER_HOST: Final = "unix:///var/run/docker.sock"
_LOCAL_DATABASE_PORT: Final = 55_434
_LOCAL_DATABASE_PASSWORD: Final = "local-grocery-only"  # noqa: S105 - tracked local fixture
_FORMAT_VERSION: Final = "grocery-postgres-custom-v2"
_POSTGRES_MAJOR: Final = 18
_DUMP_FILENAME: Final = "database.dump"
_MANIFEST_FILENAME: Final = "manifest.json"
_DUMP_MAGIC: Final = b"PGDMP"
_MAX_MANIFEST_BYTES: Final = 4 * 1024 * 1024
_MAX_INVENTORY_BYTES: Final = 4 * 1024 * 1024
_MAX_DOCKER_INSPECT_BYTES: Final = 256 * 1024
_MAX_DUMP_BYTES: Final = 8 * 1024 * 1024 * 1024
_MAX_TABLES: Final = 1_024
_MAX_MIGRATIONS: Final = 16_384
_MAX_ROW_COUNT: Final = (2**63) - 1
_MAX_ACTIVATIONS: Final = 10_000
_MAX_PUBLICATION_ENTRIES: Final = 100_000
_TOOL_TIMEOUT_SECONDS: Final = 30
_CREATE_INNER_TIMEOUT_SECONDS: Final = 20
_INVENTORY_TIMEOUT_SECONDS: Final = 120
_DUMP_TIMEOUT_SECONDS: Final = 600
_RESTORE_TIMEOUT_SECONDS: Final = 600
_RESTORE_INNER_TIMEOUT_SECONDS: Final = 570
_INSPECTION_TIMEOUT_SECONDS: Final = 120
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TABLE_NAME = re.compile(r"public\.[a-z0-9_]{1,63}\Z")
_MIGRATION_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_TARGET_DATABASE = re.compile(r"grocery_restore_[a-z0-9][a-z0-9_]{0,45}\Z")
_VERSION_TOKEN = re.compile(r"([0-9]+)(?:\.[0-9]+)*\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_APPLICATION_NAME = re.compile(r"grocery_(?:backup|restore)_[0-9a-f]{32}\Z")

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
        "database_container_invalid",
        "docker_unavailable",
        "dump_failed",
        "dump_file_invalid",
        "internal_error",
        "inventory_invalid",
        "inventory_mismatch",
        "migration_mismatch",
        "manifest_receipt_mismatch",
        "postgres_tool_missing",
        "postgres_version_mismatch",
        "publication_contract_mismatch",
        "publication_inspection_failed",
        "repository_invalid",
        "restore_failed",
        "row_count_mismatch",
        "source_database_unavailable",
        "target_database_exists",
        "target_database_invalid",
        "target_database_is_source",
        "target_cleanup_failed",
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
class CanonicalPublication:
    version: int
    current_revision_id: str
    typed_fact_set_sha256: str
    entry_count: int
    last_activation_id: str
    last_activation_operation: str
    last_activation_sequence: int

    def canonical_data(self) -> dict[str, object]:
        return {
            "channel": "RECENT_RETAIL",
            "current_revision_id": self.current_revision_id,
            "entry_count": self.entry_count,
            "last_activation_id": self.last_activation_id,
            "last_activation_operation": self.last_activation_operation,
            "last_activation_sequence": self.last_activation_sequence,
            "publication_state": "AVAILABLE",
            "typed_fact_set_sha256": self.typed_fact_set_sha256,
            "version": self.version,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.canonical_data())).hexdigest()


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _DatabaseContainer:
    docker_binary: str
    container_id: str


@dataclass(frozen=True, slots=True)
class _OpenedDirectory:
    path: Path
    descriptor: int


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
                "publication_metadata_consistent=yes",
                "publication_canonical_consistent=yes",
                f"tables={self.table_count}",
                f"migrations={self.migration_count}",
                "cleanup=drop_explicit_restore_target_after_review",
            )
        )


@dataclass(frozen=True, slots=True)
class LoadedBackup:
    backup_id: uuid.UUID
    dump_descriptor: int
    dump_identity: _FileIdentity
    inventory: Inventory
    canonical_publication: CanonicalPublication


def create_backup(*, repository: Path, destination_root: Path) -> BackupReceipt:
    root = _repository_root(repository)
    with _open_operator_directory(destination_root, repository=root) as destination:
        application_name = _new_application_name("backup")
        container = _preflight(root, application_name)
        before_canonical = _inspect_publication(
            root,
            _SOURCE_DATABASE,
            container,
            application_name,
        )
        before = _read_inventory(root, _SOURCE_DATABASE, container, application_name)
        _require_canonical_inventory_match(before_canonical, before)
        backup_id = uuid.uuid4()
        backup_name = f"postgres-backup-{backup_id}"

        with _restrictive_umask():
            backup_descriptor = _create_private_directory_at(
                destination.descriptor,
                backup_name,
            )
            try:
                dump_fd = _create_private_file_at(backup_descriptor, _DUMP_FILENAME)
                try:
                    _dump_database(root, dump_fd, container, application_name)
                    os.fsync(dump_fd)
                    dump_identity = _require_private_regular_descriptor(
                        dump_fd,
                        maximum_bytes=_MAX_DUMP_BYTES,
                    )
                    if _read_prefix_descriptor(dump_fd, len(_DUMP_MAGIC)) != _DUMP_MAGIC:
                        raise BackupRestoreError("backup_not_custom_format")
                    dump_sha256, dump_bytes = _file_sha256_descriptor(dump_fd)
                    _require_descriptor_identity(dump_fd, dump_identity)
                finally:
                    os.close(dump_fd)

                after = _read_inventory(root, _SOURCE_DATABASE, container, application_name)
                after_canonical = _inspect_publication(
                    root,
                    _SOURCE_DATABASE,
                    container,
                    application_name,
                )
                _require_canonical_inventory_match(after_canonical, after)
                if before != after or before_canonical != after_canonical:
                    raise BackupRestoreError("backup_changed_during_dump")

                manifest = {
                    "backup_id": str(backup_id),
                    "created_at": (
                        datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                    ),
                    "dump": {
                        "bytes": dump_bytes,
                        "filename": _DUMP_FILENAME,
                        "sha256": dump_sha256,
                    },
                    "format_version": _FORMAT_VERSION,
                    "inventory": {
                        **before.canonical_data(),
                        "canonical_publication": before_canonical.canonical_data(),
                        "canonical_publication_sha256": before_canonical.sha256,
                        "publication_sha256": before.publication_sha256,
                        "sha256": before.sha256,
                    },
                    "postgres_major": _POSTGRES_MAJOR,
                    "source_database": _SOURCE_DATABASE,
                }
                manifest_bytes = _canonical_json(manifest) + b"\n"
                _write_private_file_at(
                    backup_descriptor,
                    _MANIFEST_FILENAME,
                    manifest_bytes,
                )
                _fsync_directory_descriptor(backup_descriptor)
            finally:
                os.close(backup_descriptor)
            _fsync_directory_descriptor(destination.descriptor)

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
    expected_manifest_sha256: str,
) -> RestoreReceipt:
    root = _repository_root(repository)
    target = _validated_target_database(target_database)
    expected_manifest = _validated_manifest_sha256(expected_manifest_sha256)
    with _load_backup(
        backup_directory,
        repository=root,
        expected_manifest_sha256=expected_manifest,
    ) as selected_backup:
        application_name = _new_application_name("restore")
        container = _preflight(root, application_name)
        _validate_custom_dump(
            root,
            selected_backup.dump_descriptor,
            container,
            application_name,
        )
        _require_descriptor_identity(
            selected_backup.dump_descriptor,
            selected_backup.dump_identity,
        )
        if _database_exists(root, target, container, application_name):
            raise BackupRestoreError("target_database_exists")
        cleanup_required = False
        try:
            _create_target_database(root, target, container, application_name)
            # A failed or timed-out ``createdb`` is ambiguous: another local process
            # may have won the same validated name after the absence check. Never
            # delete by name until this invocation has observed a fully successful
            # command and the post-command container identity check.
            cleanup_required = True
            _restore_database(
                root,
                selected_backup.dump_descriptor,
                target,
                container,
                application_name,
            )
            _require_descriptor_identity(
                selected_backup.dump_descriptor,
                selected_backup.dump_identity,
            )
            restored = _read_inventory(root, target, container, application_name)
            restored_canonical = _inspect_publication(
                root,
                target,
                container,
                application_name,
            )
            _require_canonical_inventory_match(restored_canonical, restored)
            if restored.rows != selected_backup.inventory.rows:
                raise BackupRestoreError("row_count_mismatch")
            if restored.migrations != selected_backup.inventory.migrations:
                raise BackupRestoreError("migration_mismatch")
            if (
                restored.publication != selected_backup.inventory.publication
                or restored.publication_sha256 != selected_backup.inventory.publication_sha256
            ):
                raise BackupRestoreError("publication_contract_mismatch")
            if restored_canonical != selected_backup.canonical_publication:
                raise BackupRestoreError("publication_contract_mismatch")
            if restored.sha256 != selected_backup.inventory.sha256:
                raise BackupRestoreError("inventory_mismatch")
            cleanup_required = False
            return RestoreReceipt(
                backup_id=selected_backup.backup_id,
                table_count=len(restored.rows),
                migration_count=len(restored.migrations),
            )
        except BackupRestoreError:
            if cleanup_required:
                try:
                    _cleanup_target_database(root, target, container, application_name)
                except BackupRestoreError:
                    raise BackupRestoreError("target_cleanup_failed") from None
            raise


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
                expected_manifest_sha256=parsed.expected_manifest_sha256,
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
    restore.add_argument("--expected-manifest-sha256", required=True)
    return parser


def _print_failure(code: str, *, restore_requested: bool) -> None:
    selected = code if code in _CODES else "internal_error"
    print("status=failed")
    print(f"code={selected}")
    if restore_requested:
        if selected in {
            "create_target_failed",
            "database_container_invalid",
            "docker_command_failed",
            "internal_error",
            "target_cleanup_failed",
        }:
            print("cleanup=manual_target_cleanup_required")
        else:
            print("cleanup=automatic_created_target_cleanup_verified_or_not_created")
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


def _validated_target_database(value: object) -> str:
    if value == _SOURCE_DATABASE:
        raise BackupRestoreError("target_database_is_source")
    if not isinstance(value, str) or _TARGET_DATABASE.fullmatch(value) is None:
        raise BackupRestoreError("target_database_invalid")
    return value


def _validated_manifest_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BackupRestoreError("manifest_receipt_mismatch")
    return value


def _new_application_name(operation: str) -> str:
    if operation not in {"backup", "restore"}:
        raise BackupRestoreError("internal_error")
    return f"grocery_{operation}_{uuid.uuid4().hex}"


def _preflight(repository: Path, application_name: str) -> _DatabaseContainer:
    _require_application_name(application_name)
    docker_binary = _resolve_docker_binary()
    container = _DatabaseContainer(
        docker_binary=docker_binary,
        container_id=_discover_database_container(repository, docker_binary),
    )
    for tool in ("pg_dump", "pg_restore", "createdb", "dropdb", "psql"):
        output = _capture_database_command(
            repository,
            container,
            application_name,
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
    timeout_version = _capture_database_command(
        repository,
        container,
        application_name,
        ("timeout", "--version"),
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="postgres_tool_missing",
    )
    if not timeout_version.startswith(b"timeout (GNU coreutils) ") or len(timeout_version) > 512:
        raise BackupRestoreError("postgres_tool_missing")
    source_probe = _capture_database_command(
        repository,
        container,
        application_name,
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
    return container


def _dump_database(
    repository: Path,
    output_fd: int,
    container: _DatabaseContainer,
    application_name: str,
) -> None:
    _run_database_command(
        repository,
        container,
        application_name,
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


def _validate_custom_dump(
    repository: Path,
    dump_descriptor: int,
    container: _DatabaseContainer,
    application_name: str,
) -> None:
    _rewind_descriptor(dump_descriptor)
    _run_database_command(
        repository,
        container,
        application_name,
        ("pg_restore", "--list"),
        stdin=dump_descriptor,
        stdout=subprocess.DEVNULL,
        timeout=_INVENTORY_TIMEOUT_SECONDS,
        failure_code="backup_not_custom_format",
    )


def _database_exists(
    repository: Path,
    target: str,
    container: _DatabaseContainer,
    application_name: str,
) -> bool:
    output = _capture_database_command(
        repository,
        container,
        application_name,
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


def _create_target_database(
    repository: Path,
    target: str,
    container: _DatabaseContainer,
    application_name: str,
) -> None:
    _run_database_command(
        repository,
        container,
        application_name,
        (
            "timeout",
            "--signal=TERM",
            "--kill-after=5s",
            f"{_CREATE_INNER_TIMEOUT_SECONDS}s",
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


def _restore_database(
    repository: Path,
    dump_descriptor: int,
    target: str,
    container: _DatabaseContainer,
    application_name: str,
) -> None:
    _rewind_descriptor(dump_descriptor)
    _run_database_command(
        repository,
        container,
        application_name,
        (
            "timeout",
            "--signal=TERM",
            "--kill-after=5s",
            f"{_RESTORE_INNER_TIMEOUT_SECONDS}s",
            "pg_restore",
            "--no-password",
            f"--username={_DATABASE_USER}",
            f"--dbname={target}",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-privileges",
        ),
        stdin=dump_descriptor,
        stdout=subprocess.DEVNULL,
        timeout=_RESTORE_TIMEOUT_SECONDS,
        failure_code="restore_failed",
    )


def _cleanup_target_database(
    repository: Path,
    target: str,
    container: _DatabaseContainer,
    application_name: str,
) -> None:
    terminate = _capture_database_command(
        repository,
        container,
        application_name,
        (
            "psql",
            "--no-password",
            f"--username={_DATABASE_USER}",
            "--dbname=postgres",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            (
                f"--command=WITH target_sessions AS MATERIALIZED ("  # noqa: S608
                "SELECT pid FROM pg_stat_activity "
                f"WHERE datname = '{target}' AND pid <> pg_backend_pid()"
                ") SELECT count(*) FROM target_sessions "
                "WHERE pg_terminate_backend(pid);"
            ),
        ),
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="target_cleanup_failed",
    )
    if not terminate.strip().isdigit():
        raise BackupRestoreError("target_cleanup_failed")
    sessions = _capture_database_command(
        repository,
        container,
        application_name,
        (
            "psql",
            "--no-password",
            f"--username={_DATABASE_USER}",
            "--dbname=postgres",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            (
                f"--command=SELECT count(*) FROM pg_stat_activity WHERE datname = '{target}';"  # noqa: S608
            ),
        ),
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="target_cleanup_failed",
    )
    if sessions.strip() != b"0":
        raise BackupRestoreError("target_cleanup_failed")
    _run_database_command(
        repository,
        container,
        application_name,
        (
            "dropdb",
            "--no-password",
            f"--username={_DATABASE_USER}",
            "--if-exists",
            "--force",
            target,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="target_cleanup_failed",
    )
    if _database_exists(repository, target, container, application_name):
        raise BackupRestoreError("target_cleanup_failed")


def _read_inventory(
    repository: Path,
    database: str,
    container: _DatabaseContainer,
    application_name: str,
) -> Inventory:
    output = _capture_database_command(
        repository,
        container,
        application_name,
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


def _parse_canonical_publication(value: object) -> CanonicalPublication:
    expected_keys = {
        "channel",
        "current_revision_id",
        "entry_count",
        "last_activation_id",
        "last_activation_operation",
        "last_activation_sequence",
        "publication_state",
        "typed_fact_set_sha256",
        "version",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise BackupRestoreError("publication_inspection_failed")
    version = value["version"]
    entry_count = value["entry_count"]
    activation_sequence = value["last_activation_sequence"]
    activation_operation = value["last_activation_operation"]
    if (
        value["channel"] != "RECENT_RETAIL"
        or value["publication_state"] != "AVAILABLE"
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or version > _MAX_ACTIVATIONS
        or not isinstance(entry_count, int)
        or isinstance(entry_count, bool)
        or entry_count < 1
        or entry_count > _MAX_PUBLICATION_ENTRIES
        or not isinstance(activation_sequence, int)
        or isinstance(activation_sequence, bool)
        or activation_sequence != version
        or not isinstance(activation_operation, str)
        or activation_operation not in {"ACTIVATE", "ROLLBACK"}
    ):
        raise BackupRestoreError("publication_inspection_failed")
    try:
        current_revision_id = _canonical_uuid_text(value["current_revision_id"])
        activation_id = _canonical_uuid_text(value["last_activation_id"])
        fact_set_sha256 = _canonical_sha256_text(value["typed_fact_set_sha256"])
    except BackupRestoreError:
        raise BackupRestoreError("publication_inspection_failed") from None
    return CanonicalPublication(
        version=version,
        current_revision_id=current_revision_id,
        typed_fact_set_sha256=fact_set_sha256,
        entry_count=entry_count,
        last_activation_id=activation_id,
        last_activation_operation=activation_operation,
        last_activation_sequence=activation_sequence,
    )


def _require_canonical_inventory_match(
    canonical: CanonicalPublication,
    inventory: Inventory,
) -> None:
    channel = inventory.publication["channel"]
    revision = inventory.publication["active_revision"]
    activations = inventory.publication["activations"]
    if (
        not isinstance(channel, dict)
        or not isinstance(revision, dict)
        or not isinstance(activations, list)
        or not activations
    ):
        raise BackupRestoreError("publication_contract_mismatch")
    latest = activations[-1]
    if not isinstance(latest, dict):
        raise BackupRestoreError("publication_contract_mismatch")
    if (
        canonical.version != channel.get("version")
        or canonical.current_revision_id != channel.get("current_revision_id")
        or canonical.current_revision_id != revision.get("id")
        or canonical.typed_fact_set_sha256 != revision.get("typed_fact_set_sha256")
        or canonical.entry_count != revision.get("entry_count")
        or canonical.last_activation_id != latest.get("id")
        or canonical.last_activation_operation != latest.get("operation")
        or canonical.last_activation_sequence != latest.get("sequence")
    ):
        raise BackupRestoreError("publication_contract_mismatch")


def _inspect_publication(
    repository: Path,
    database: str,
    container: _DatabaseContainer,
    application_name: str,
) -> CanonicalPublication:
    if database != _SOURCE_DATABASE and _TARGET_DATABASE.fullmatch(database) is None:
        raise BackupRestoreError("publication_inspection_failed")
    _require_application_name(application_name)
    python = repository / ".venv" / "bin" / "python"
    manage = repository / "manage.py"
    try:
        python_metadata = python.stat()
        manage_metadata = manage.lstat()
    except OSError:
        raise BackupRestoreError("publication_inspection_failed") from None
    if (
        not stat.S_ISREG(python_metadata.st_mode)
        or stat.S_ISLNK(manage_metadata.st_mode)
        or not stat.S_ISREG(manage_metadata.st_mode)
    ):
        raise BackupRestoreError("publication_inspection_failed")
    _require_same_database_container(repository, container)
    completed: subprocess.CompletedProcess[bytes] | None = None
    command_failed = False
    try:
        completed = subprocess.run(  # noqa: S603 - exact local interpreter and command.
            (str(python), str(manage), "inspect_recent_publication"),
            cwd=repository,
            env=_inspection_environment(database, application_name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_INSPECTION_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.SubprocessError:
        command_failed = True
    _require_same_database_container(repository, container)
    if command_failed or completed is None:
        raise BackupRestoreError("publication_inspection_failed")
    output = completed.stdout
    if (
        completed.returncode != 0
        or not isinstance(output, bytes)
        or len(output) < 1
        or len(output) > _MAX_MANIFEST_BYTES
    ):
        raise BackupRestoreError("publication_inspection_failed")
    try:
        decoded = json.loads(output.decode("ascii", errors="strict"))
    except UnicodeError, json.JSONDecodeError:
        raise BackupRestoreError("publication_inspection_failed") from None
    return _parse_canonical_publication(decoded)


def _inspection_environment(database: str, application_name: str) -> dict[str, str]:
    _require_application_name(application_name)
    return {
        "ADMIN_ENABLED": "0",
        "CONTROL_PLANE_OPERATIONS_ENABLED": "0",
        "DATABASE_CONN_MAX_AGE": "0",
        "DATABASE_URL": (
            f"postgresql://{_DATABASE_USER}:{_LOCAL_DATABASE_PASSWORD}"
            f"@127.0.0.1:{_LOCAL_DATABASE_PORT}/{database}"
        ),
        "DJANGO_DEBUG": "1",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "PGAPPNAME": application_name,
        "PYTHONDONTWRITEBYTECODE": "1",
        "QA_STATE_PREVIEWS_ENABLED": "0",
    }


@contextmanager
def _load_backup(
    path: Path,
    *,
    repository: Path,
    expected_manifest_sha256: str,
) -> Iterator[LoadedBackup]:
    expected_manifest = _validated_manifest_sha256(expected_manifest_sha256)
    with _open_operator_directory(
        path,
        repository=repository,
        require_private=True,
    ) as backup_directory:
        manifest_descriptor = _open_private_file_at(
            backup_directory.descriptor,
            _MANIFEST_FILENAME,
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
        try:
            manifest_identity = _require_private_regular_descriptor(
                manifest_descriptor,
                maximum_bytes=_MAX_MANIFEST_BYTES,
            )
            manifest_bytes = _read_bounded_descriptor(
                manifest_descriptor,
                _MAX_MANIFEST_BYTES,
            )
            _require_descriptor_identity(manifest_descriptor, manifest_identity)
            actual_manifest = hashlib.sha256(manifest_bytes).hexdigest()
            if not hmac.compare_digest(actual_manifest, expected_manifest):
                raise BackupRestoreError("manifest_receipt_mismatch")
        finally:
            os.close(manifest_descriptor)
        dump_descriptor = _open_private_file_at(
            backup_directory.descriptor,
            _DUMP_FILENAME,
            maximum_bytes=_MAX_DUMP_BYTES,
        )
        try:
            dump_identity = _require_private_regular_descriptor(
                dump_descriptor,
                maximum_bytes=_MAX_DUMP_BYTES,
            )
            selected = _parse_loaded_backup(
                manifest_bytes=manifest_bytes,
                dump_descriptor=dump_descriptor,
                dump_identity=dump_identity,
            )
            yield selected
        finally:
            os.close(dump_descriptor)


def _parse_loaded_backup(
    *,
    manifest_bytes: bytes,
    dump_descriptor: int,
    dump_identity: _FileIdentity,
) -> LoadedBackup:
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
    dump_sha256, dump_bytes = _file_sha256_descriptor(dump_descriptor)
    _require_descriptor_identity(dump_descriptor, dump_identity)
    if dump_sha256 != dump["sha256"] or dump_bytes != dump["bytes"]:
        raise BackupRestoreError("checksum_mismatch")
    if _read_prefix_descriptor(dump_descriptor, len(_DUMP_MAGIC)) != _DUMP_MAGIC:
        raise BackupRestoreError("backup_not_custom_format")

    if not isinstance(inventory_value, dict) or set(inventory_value) != {
        "canonical_publication",
        "canonical_publication_sha256",
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
    canonical = _parse_canonical_publication(inventory_value["canonical_publication"])
    canonical_sha = inventory_value["canonical_publication_sha256"]
    if (
        not isinstance(canonical_sha, str)
        or _SHA256.fullmatch(canonical_sha) is None
        or canonical.sha256 != canonical_sha
    ):
        raise BackupRestoreError("publication_contract_mismatch")
    _require_canonical_inventory_match(canonical, inventory)
    return LoadedBackup(
        backup_id=backup_id,
        dump_descriptor=dump_descriptor,
        dump_identity=dump_identity,
        inventory=inventory,
        canonical_publication=canonical,
    )


def _capture_database_command(
    repository: Path,
    container: _DatabaseContainer,
    application_name: str,
    tool_arguments: tuple[str, ...],
    *,
    timeout: int,
    failure_code: str,
) -> bytes:
    completed = _run_database_command(
        repository,
        container,
        application_name,
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


def _resolve_docker_binary() -> str:
    selected = shutil.which("docker")
    if selected is None:
        raise BackupRestoreError("docker_unavailable")
    try:
        resolved = Path(selected).resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        raise BackupRestoreError("docker_unavailable") from None
    if (
        not resolved.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise BackupRestoreError("docker_unavailable")
    return str(resolved)


def _require_local_docker_socket() -> None:
    socket_path = Path(_LOCAL_DOCKER_HOST.removeprefix("unix://"))
    try:
        metadata = socket_path.stat()
    except OSError:
        raise BackupRestoreError("database_container_invalid") from None
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid not in {0, os.geteuid()}:
        raise BackupRestoreError("database_container_invalid")


def _database_container_ids(
    repository: Path,
    docker_binary: str,
) -> tuple[str, ...]:
    completed = _run_docker_cli(
        repository,
        docker_binary,
        (
            "container",
            "ls",
            "--no-trunc",
            f"--filter=label=com.docker.compose.project={_COMPOSE_PROJECT}",
            f"--filter=label=com.docker.compose.service={_COMPOSE_SERVICE}",
            "--format={{.ID}}",
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="database_container_invalid",
    )
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) > 512:
        raise BackupRestoreError("database_container_invalid")
    try:
        identifiers = tuple(
            line for line in output.decode("ascii", errors="strict").splitlines() if line
        )
    except UnicodeError:
        raise BackupRestoreError("database_container_invalid") from None
    if len(identifiers) != 1 or _CONTAINER_ID.fullmatch(identifiers[0]) is None:
        raise BackupRestoreError("database_container_invalid")
    return identifiers


def _discover_database_container(repository: Path, docker_binary: str) -> str:
    _require_local_docker_socket()
    before = _database_container_ids(repository, docker_binary)
    container_id = before[0]
    completed = _run_docker_cli(
        repository,
        docker_binary,
        ("container", "inspect", container_id),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        timeout=_TOOL_TIMEOUT_SECONDS,
        failure_code="database_container_invalid",
    )
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) < 1 or len(output) > _MAX_DOCKER_INSPECT_BYTES:
        raise BackupRestoreError("database_container_invalid")
    try:
        decoded = json.loads(output.decode("utf-8", errors="strict"))
    except UnicodeError, json.JSONDecodeError:
        raise BackupRestoreError("database_container_invalid") from None
    _validate_database_container_inspection(repository, container_id, decoded)
    after = _database_container_ids(repository, docker_binary)
    if after != before:
        raise BackupRestoreError("database_container_invalid")
    return container_id


def _validate_database_container_inspection(
    repository: Path,
    container_id: str,
    decoded: object,
) -> None:
    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
        raise BackupRestoreError("database_container_invalid")
    inspection = decoded[0]
    config = inspection.get("Config")
    state = inspection.get("State")
    network = inspection.get("NetworkSettings")
    if not isinstance(config, dict) or not isinstance(state, dict) or not isinstance(network, dict):
        raise BackupRestoreError("database_container_invalid")
    labels = config.get("Labels")
    health = state.get("Health")
    ports = network.get("Ports")
    expected_labels = {
        "com.docker.compose.project": _COMPOSE_PROJECT,
        "com.docker.compose.project.config_files": str(repository / "compose.yaml"),
        "com.docker.compose.project.working_dir": str(repository),
        "com.docker.compose.service": _COMPOSE_SERVICE,
    }
    expected_port = [{"HostIp": "127.0.0.1", "HostPort": str(_LOCAL_DATABASE_PORT)}]
    if (
        inspection.get("Id") != container_id
        or not isinstance(labels, dict)
        or any(labels.get(key) != value for key, value in expected_labels.items())
        or state.get("Status") != "running"
        or state.get("Running") is not True
        or not isinstance(health, dict)
        or health.get("Status") != "healthy"
        or not isinstance(ports, dict)
        or {key: value for key, value in ports.items() if value is not None}
        != {"5432/tcp": expected_port}
    ):
        raise BackupRestoreError("database_container_invalid")


def _require_same_database_container(
    repository: Path,
    container: _DatabaseContainer,
) -> None:
    discovered = _discover_database_container(repository, container.docker_binary)
    if not hmac.compare_digest(discovered, container.container_id):
        raise BackupRestoreError("database_container_invalid")


def _run_database_command(
    repository: Path,
    container: _DatabaseContainer,
    application_name: str,
    tool_arguments: tuple[str, ...],
    *,
    stdin: int,
    stdout: int,
    timeout: int,
    failure_code: str,
) -> subprocess.CompletedProcess[bytes]:
    _require_application_name(application_name)
    _require_same_database_container(repository, container)
    completed: subprocess.CompletedProcess[bytes] | None = None
    command_error: BackupRestoreError | None = None
    try:
        completed = _run_docker_cli(
            repository,
            container.docker_binary,
            (
                "exec",
                "-i",
                f"--env=PGAPPNAME={application_name}",
                container.container_id,
                *tool_arguments,
            ),
            stdin=stdin,
            stdout=stdout,
            timeout=timeout,
            failure_code=failure_code,
        )
    except BackupRestoreError as error:
        command_error = error
    _require_same_database_container(repository, container)
    if command_error is not None:
        raise command_error
    if completed is None:
        raise BackupRestoreError(failure_code)
    return completed


def _run_docker_cli(
    repository: Path,
    docker_binary: str,
    arguments: tuple[str, ...],
    *,
    stdin: int,
    stdout: int,
    timeout: int,
    failure_code: str,
) -> subprocess.CompletedProcess[bytes]:
    if not Path(docker_binary).is_absolute():
        raise BackupRestoreError("docker_unavailable")
    command = (docker_binary, f"--host={_LOCAL_DOCKER_HOST}", *arguments)
    try:
        completed = subprocess.run(  # noqa: S603 - resolved docker and fixed local socket.
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


def _require_application_name(value: str) -> None:
    if _APPLICATION_NAME.fullmatch(value) is None:
        raise BackupRestoreError("internal_error")


def _docker_environment() -> dict[str, str]:
    return {
        "DOCKER_CONFIG": "/var/empty",
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
    }


@contextmanager
def _restrictive_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@contextmanager
def _open_operator_directory(
    path: Path,
    *,
    repository: Path,
    require_private: bool = False,
) -> Iterator[_OpenedDirectory]:
    if not path.is_absolute():
        raise BackupRestoreError("backup_directory_invalid")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
        parent_metadata = resolved.parent.lstat()
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
        or (before.st_dev, before.st_ino) != (resolved_metadata.st_dev, resolved_metadata.st_ino)
    ):
        raise BackupRestoreError("backup_directory_invalid")
    _require_trusted_directory_metadata(resolved_metadata, require_private=require_private)
    _require_trusted_parent_metadata(parent_metadata)
    if _is_within(resolved, repository):
        raise BackupRestoreError("destination_inside_repository")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags)
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (resolved_metadata.st_dev, resolved_metadata.st_ino):
            raise BackupRestoreError("backup_directory_invalid")
        _require_trusted_directory_metadata(opened, require_private=require_private)
        yield _OpenedDirectory(path=resolved, descriptor=descriptor)
    finally:
        os.close(descriptor)


def _require_trusted_directory_metadata(
    metadata: os.stat_result,
    *,
    require_private: bool,
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or mode & 0o022
        or (require_private and mode & 0o077)
    ):
        raise BackupRestoreError("backup_directory_permissions")


def _require_trusted_parent_metadata(metadata: os.stat_result) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    owner_is_trusted = metadata.st_uid in {0, os.geteuid()}
    not_broadly_writable = mode & 0o022 == 0
    sticky_directory = stat.S_ISDIR(metadata.st_mode) and bool(mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or not owner_is_trusted
        or not (not_broadly_writable or sticky_directory)
    ):
        raise BackupRestoreError("backup_directory_permissions")


def _create_private_directory_at(parent_descriptor: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None
    try:
        _require_trusted_directory_metadata(os.fstat(descriptor), require_private=True)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _create_private_file_at(directory_descriptor: int, name: str) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        return os.open(name, flags, 0o600, dir_fd=directory_descriptor)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None


def _write_private_file_at(directory_descriptor: int, name: str, content: bytes) -> None:
    descriptor = _create_private_file_at(directory_descriptor, name)
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


def _open_private_file_at(
    directory_descriptor: int,
    name: str,
    *,
    maximum_bytes: int,
) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    try:
        _require_private_regular_descriptor(descriptor, maximum_bytes=maximum_bytes)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _require_private_regular_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
) -> _FileIdentity:
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BackupRestoreError("backup_directory_permissions")
    if metadata.st_size < 1 or metadata.st_size > maximum_bytes:
        raise BackupRestoreError("backup_too_large")
    return _file_identity(metadata)


def _file_identity(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _require_descriptor_identity(descriptor: int, expected: _FileIdentity) -> None:
    try:
        actual = _file_identity(os.fstat(descriptor))
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    if actual != expected:
        raise BackupRestoreError("checksum_mismatch")


def _read_bounded_descriptor(descriptor: int, maximum_bytes: int) -> bytes:
    _rewind_descriptor(descriptor)
    chunks: list[bytes] = []
    total = 0
    try:
        while total <= maximum_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    data = b"".join(chunks)
    if len(data) > maximum_bytes:
        raise BackupRestoreError("backup_too_large")
    return data


def _read_prefix_descriptor(descriptor: int, length: int) -> bytes:
    _rewind_descriptor(descriptor)
    chunks: list[bytes] = []
    remaining = length
    try:
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None
    return b"".join(chunks)


def _file_sha256_descriptor(descriptor: int) -> tuple[str, int]:
    _rewind_descriptor(descriptor)
    digest = hashlib.sha256()
    total = 0
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
    return digest.hexdigest(), total


def _rewind_descriptor(descriptor: int) -> None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        raise BackupRestoreError("dump_file_invalid") from None


def _fsync_directory_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        raise BackupRestoreError("backup_directory_unavailable") from None


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
