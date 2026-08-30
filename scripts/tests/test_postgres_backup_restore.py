"""Unit tests for bounded local PostgreSQL backup and restore assurance."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import call, patch

import pytest

from scripts.postgres_backup_restore import (
    _INVENTORY_SQL,
    BackupReceipt,
    BackupRestoreError,
    CanonicalPublication,
    Inventory,
    _cleanup_target_database,
    _database_exists,
    _DatabaseContainer,
    _discover_database_container,
    _docker_environment,
    _inspect_publication,
    _new_application_name,
    _parse_inventory,
    _preflight,
    _restore_database,
    _run_database_command,
    _run_docker_cli,
    _validate_database_container_inspection,
    _validated_target_database,
    create_backup,
    main,
    restore_backup,
)

_REVISION_V1 = "11111111-1111-4111-8111-111111111111"
_REVISION_V2 = "22222222-2222-4222-8222-222222222222"
_GENERATION_ID = "33333333-3333-4333-8333-333333333333"
_REVIEW_ID = "44444444-4444-4444-8444-444444444444"
_ACTIVATION_V1 = "55555555-5555-4555-8555-555555555555"
_ACTIVATION_V2 = "66666666-6666-4666-8666-666666666666"
_FACT_SET_HASH = "a" * 64
_EVIDENCE_HASH = "8" * 64
_CONTAINER_ID = "c" * 64
_DOCKER = "/safe/docker"
_APPLICATION_NAME = "grocery_restore_" + ("d" * 32)
_BACKUP_APPLICATION_NAME = "grocery_backup_" + ("e" * 32)
_CONTAINER = _DatabaseContainer(docker_binary=_DOCKER, container_id=_CONTAINER_ID)


def _container_inspection(repository: Path) -> list[dict[str, object]]:
    return [
        {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "audience-foundry-grocery-seasonality",
                    "com.docker.compose.project.config_files": str(repository / "compose.yaml"),
                    "com.docker.compose.project.working_dir": str(repository),
                    "com.docker.compose.service": "db",
                }
            },
            "Id": _CONTAINER_ID,
            "NetworkSettings": {
                "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55434"}]}
            },
            "State": {"Health": {"Status": "healthy"}, "Running": True, "Status": "running"},
        }
    ]


def _publication_contract() -> dict[str, object]:
    return {
        "active_revision": {
            "entry_count": 10,
            "generation_id": _GENERATION_ID,
            "id": _REVISION_V2,
            "review_decision": "APPROVE",
            "review_decision_id": _REVIEW_ID,
            "review_parse_run_id": _GENERATION_ID,
            "typed_fact_set_sha256": _FACT_SET_HASH,
        },
        "activations": [
            {
                "acceptance_evidence_sha256": _EVIDENCE_HASH,
                "id": _ACTIVATION_V1,
                "operation": "ACTIVATE",
                "previous_revision_id": None,
                "reason_code": "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
                "sequence": 1,
                "target_revision_id": _REVISION_V1,
            },
            {
                "acceptance_evidence_sha256": _EVIDENCE_HASH,
                "id": _ACTIVATION_V2,
                "operation": "ACTIVATE",
                "previous_revision_id": _REVISION_V1,
                "reason_code": "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
                "sequence": 2,
                "target_revision_id": _REVISION_V2,
            },
        ],
        "channel": {
            "channel": "RECENT_RETAIL",
            "current_revision_id": _REVISION_V2,
            "version": 2,
        },
    }


def _canonical_publication() -> CanonicalPublication:
    return CanonicalPublication(
        version=2,
        current_revision_id=_REVISION_V2,
        typed_fact_set_sha256=_FACT_SET_HASH,
        entry_count=10,
        last_activation_id=_ACTIVATION_V2,
        last_activation_operation="ACTIVATE",
        last_activation_sequence=2,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    return root


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    root = tmp_path / "backups"
    root.mkdir()
    return root


@pytest.fixture
def inventory() -> Inventory:
    return Inventory(
        rows={
            "public.auth_user": 1,
            "public.django_migrations": 17,
            "public.grocery_publicationrevision": 2,
        },
        migrations=(
            ("contenttypes", "0001_initial"),
            ("grocery", "0008_publication_activation"),
        ),
        publication=_publication_contract(),
    )


def _synthetic_dump(
    _repository: Path,
    descriptor: int,
    _container: _DatabaseContainer,
    _application_name: str,
) -> None:
    os.write(descriptor, b"PGDMPsynthetic-custom-format")


def _make_backup(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> tuple[Path, BackupReceipt]:
    with (
        patch("scripts.postgres_backup_restore._preflight", return_value=_CONTAINER),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ),
        patch(
            "scripts.postgres_backup_restore._read_inventory",
            side_effect=(inventory, inventory),
        ),
        patch(
            "scripts.postgres_backup_restore._dump_database",
            side_effect=_synthetic_dump,
        ),
    ):
        receipt = create_backup(repository=repository, destination_root=destination)
    backup_directory = next(destination.iterdir())
    return backup_directory, receipt


def _manifest_receipt(backup_directory: Path) -> str:
    return hashlib.sha256((backup_directory / "manifest.json").read_bytes()).hexdigest()


def test_backup_creates_private_custom_dump_and_secret_free_checksum_manifest(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, receipt = _make_backup(repository, destination, inventory)

    dump = backup_directory / "database.dump"
    manifest_path = backup_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stat.S_IMODE(backup_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert dump.read_bytes().startswith(b"PGDMP")
    assert manifest["format_version"] == "grocery-postgres-custom-v2"
    assert manifest["postgres_major"] == 18
    assert manifest["source_database"] == "grocery"
    assert manifest["inventory"]["sha256"] == inventory.sha256
    assert manifest["inventory"]["publication"] == inventory.publication
    assert manifest["inventory"]["publication_sha256"] == inventory.publication_sha256
    assert manifest["inventory"]["canonical_publication"] == (
        _canonical_publication().canonical_data()
    )
    assert manifest["inventory"]["canonical_publication_sha256"] == (
        _canonical_publication().sha256
    )
    assert manifest["dump"]["sha256"] == receipt.dump_sha256
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "DATABASE_URL" not in serialized
    assert "password" not in serialized.lower()
    assert "local-grocery-only" not in serialized
    rendered = receipt.render()
    assert str(destination) not in rendered
    assert "cleanup=retain_or_remove_explicit_backup_directory" in rendered
    for internal_value in (
        _REVISION_V2,
        _GENERATION_ID,
        _REVIEW_ID,
        _ACTIVATION_V1,
        _FACT_SET_HASH,
        _EVIDENCE_HASH,
        "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
    ):
        assert internal_value not in rendered


def test_backup_reads_inventory_before_and_after_dump(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    with (
        patch(
            "scripts.postgres_backup_restore._new_application_name",
            return_value=_BACKUP_APPLICATION_NAME,
        ),
        patch("scripts.postgres_backup_restore._preflight", return_value=_CONTAINER),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ) as inspect_publication,
        patch(
            "scripts.postgres_backup_restore._read_inventory",
            side_effect=(inventory, inventory),
        ) as read_inventory,
        patch(
            "scripts.postgres_backup_restore._dump_database",
            side_effect=_synthetic_dump,
        ),
    ):
        create_backup(repository=repository, destination_root=destination)

    assert read_inventory.call_args_list == [
        call(repository.resolve(), "grocery", _CONTAINER, _BACKUP_APPLICATION_NAME),
        call(repository.resolve(), "grocery", _CONTAINER, _BACKUP_APPLICATION_NAME),
    ]
    assert inspect_publication.call_args_list == [
        call(repository.resolve(), "grocery", _CONTAINER, _BACKUP_APPLICATION_NAME),
        call(repository.resolve(), "grocery", _CONTAINER, _BACKUP_APPLICATION_NAME),
    ]


def test_backup_fails_if_inventory_changes_during_dump(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    changed = Inventory(
        rows={**inventory.rows, "public.auth_user": 2},
        migrations=inventory.migrations,
        publication=inventory.publication,
    )
    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ),
        patch(
            "scripts.postgres_backup_restore._read_inventory",
            side_effect=(inventory, changed),
        ),
        patch(
            "scripts.postgres_backup_restore._dump_database",
            side_effect=_synthetic_dump,
        ),
        pytest.raises(BackupRestoreError) as caught,
    ):
        create_backup(repository=repository, destination_root=destination)

    assert caught.value.code == "backup_changed_during_dump"


def test_backup_rejects_relative_or_repository_destination(
    repository: Path,
) -> None:
    with pytest.raises(BackupRestoreError) as relative:
        create_backup(repository=repository, destination_root=Path("relative"))
    assert relative.value.code == "backup_directory_invalid"

    child = repository / "backups"
    child.mkdir()
    with pytest.raises(BackupRestoreError) as inside:
        create_backup(repository=repository, destination_root=child)
    assert inside.value.code == "destination_inside_repository"


@pytest.mark.parametrize("unsafe_mode", (0o770, 0o707))
def test_backup_rejects_group_or_world_writable_output_root(
    repository: Path,
    destination: Path,
    unsafe_mode: int,
) -> None:
    os.chmod(destination, unsafe_mode)

    with (
        patch("scripts.postgres_backup_restore._preflight") as preflight,
        pytest.raises(BackupRestoreError) as caught,
    ):
        create_backup(repository=repository, destination_root=destination)

    assert caught.value.code == "backup_directory_permissions"
    preflight.assert_not_called()


def test_backup_rejects_untrusted_immediate_parent(
    repository: Path,
    tmp_path: Path,
) -> None:
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o700)
    destination = unsafe_parent / "backups"
    destination.mkdir(mode=0o700)
    os.chmod(unsafe_parent, 0o777)  # noqa: S103 - intentional unsafe-boundary fixture.

    with pytest.raises(BackupRestoreError) as caught:
        create_backup(repository=repository, destination_root=destination)

    assert caught.value.code == "backup_directory_permissions"


def test_backup_accepts_root_or_operator_owned_sticky_immediate_parent(
    repository: Path,
    tmp_path: Path,
    inventory: Inventory,
) -> None:
    sticky_parent = tmp_path / "sticky-parent"
    sticky_parent.mkdir(mode=0o700)
    destination = sticky_parent / "backups"
    destination.mkdir(mode=0o700)
    os.chmod(sticky_parent, 0o1777)  # noqa: S103 - intentional sticky-directory fixture.

    _backup_directory, receipt = _make_backup(repository, destination, inventory)

    assert receipt.table_count == len(inventory.rows)


def test_restore_validates_then_creates_separate_target_and_compares_inventory(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with (
        patch(
            "scripts.postgres_backup_restore._new_application_name",
            return_value=_APPLICATION_NAME,
        ),
        patch(
            "scripts.postgres_backup_restore._preflight",
            return_value=_CONTAINER,
        ) as preflight,
        patch("scripts.postgres_backup_restore._validate_custom_dump") as validate_dump,
        patch("scripts.postgres_backup_restore._database_exists", return_value=False) as exists,
        patch("scripts.postgres_backup_restore._create_target_database") as create_target,
        patch("scripts.postgres_backup_restore._restore_database") as restore,
        patch("scripts.postgres_backup_restore._cleanup_target_database") as cleanup,
        patch("scripts.postgres_backup_restore._read_inventory", return_value=inventory),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ),
    ):
        receipt = restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_rehearsal1",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    preflight.assert_called_once_with(repository.resolve(), _APPLICATION_NAME)
    assert validate_dump.call_count == 1
    validated_descriptor = validate_dump.call_args.args[1]
    assert isinstance(validated_descriptor, int)
    exists.assert_called_once_with(
        repository.resolve(),
        "grocery_restore_rehearsal1",
        _CONTAINER,
        _APPLICATION_NAME,
    )
    create_target.assert_called_once_with(
        repository.resolve(),
        "grocery_restore_rehearsal1",
        _CONTAINER,
        _APPLICATION_NAME,
    )
    restore.assert_called_once_with(
        repository.resolve(),
        validated_descriptor,
        "grocery_restore_rehearsal1",
        _CONTAINER,
        _APPLICATION_NAME,
    )
    assert receipt.backup_id == backup_receipt.backup_id
    cleanup.assert_not_called()
    assert "row_counts_consistent=yes" in receipt.render()
    assert "migrations_consistent=yes" in receipt.render()
    assert "publication_metadata_consistent=yes" in receipt.render()
    assert "publication_canonical_consistent=yes" in receipt.render()
    assert "grocery_restore_rehearsal1" not in receipt.render()
    for internal_value in (
        _REVISION_V2,
        _GENERATION_ID,
        _REVIEW_ID,
        _ACTIVATION_V1,
        _FACT_SET_HASH,
        _EVIDENCE_HASH,
        "LOCAL_PHASE0_PUBLICATION_ACTIVATED",
    ):
        assert internal_value not in receipt.render()


def test_restore_refuses_source_invalid_or_existing_target_before_create(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with pytest.raises(BackupRestoreError) as source:
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )
    assert source.value.code == "target_database_is_source"

    with pytest.raises(BackupRestoreError) as invalid:
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="production",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )
    assert invalid.value.code == "target_database_invalid"

    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=True),
        patch("scripts.postgres_backup_restore._create_target_database") as create_target,
        pytest.raises(BackupRestoreError) as existing,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_existing",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )
    assert existing.value.code == "target_database_exists"
    create_target.assert_not_called()


@pytest.mark.parametrize(
    ("restored", "expected_code"),
    (
        (
            Inventory(
                rows={"public.django_migrations": 99},
                migrations=(("grocery", "0008_publication_activation"),),
                publication=_publication_contract(),
            ),
            "row_count_mismatch",
        ),
        (
            Inventory(
                rows={
                    "public.auth_user": 1,
                    "public.django_migrations": 17,
                    "public.grocery_publicationrevision": 2,
                },
                migrations=(("grocery", "0007_publication_revision"),),
                publication=_publication_contract(),
            ),
            "migration_mismatch",
        ),
    ),
)
def test_restore_fails_closed_on_row_or_migration_drift(
    repository: Path,
    destination: Path,
    inventory: Inventory,
    restored: Inventory,
    expected_code: str,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database"),
        patch("scripts.postgres_backup_restore._restore_database"),
        patch("scripts.postgres_backup_restore._cleanup_target_database") as cleanup,
        patch("scripts.postgres_backup_restore._read_inventory", return_value=restored),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ),
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_drift",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert caught.value.code == expected_code
    cleanup.assert_called_once()


def test_restore_fails_closed_on_publication_contract_drift(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    changed_contract = deepcopy(inventory.publication)
    active_revision = changed_contract["active_revision"]
    assert isinstance(active_revision, dict)
    active_revision["typed_fact_set_sha256"] = "b" * 64
    restored = Inventory(
        rows=inventory.rows,
        migrations=inventory.migrations,
        publication=changed_contract,
    )
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database"),
        patch("scripts.postgres_backup_restore._restore_database"),
        patch("scripts.postgres_backup_restore._cleanup_target_database") as cleanup,
        patch("scripts.postgres_backup_restore._read_inventory", return_value=restored),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ),
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_publication_drift",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert caught.value.code == "publication_contract_mismatch"
    cleanup.assert_called_once()


@pytest.mark.parametrize(
    ("stage", "expected_code", "cleanup_expected"),
    (
        ("create", "create_target_failed", False),
        ("restore", "restore_failed", True),
        ("inventory", "inventory_invalid", True),
        ("inspection", "publication_inspection_failed", True),
    ),
)
def test_restore_only_cleans_target_after_unambiguous_create_success(
    repository: Path,
    destination: Path,
    inventory: Inventory,
    stage: str,
    expected_code: str,
    cleanup_expected: bool,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight", return_value=_CONTAINER),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database") as create_target,
        patch("scripts.postgres_backup_restore._restore_database") as restore,
        patch("scripts.postgres_backup_restore._read_inventory", return_value=inventory) as read,
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ) as inspect,
        patch("scripts.postgres_backup_restore._cleanup_target_database") as cleanup,
        pytest.raises(BackupRestoreError) as caught,
    ):
        failing = {
            "create": create_target,
            "restore": restore,
            "inventory": read,
            "inspection": inspect,
        }[stage]
        failing.side_effect = BackupRestoreError(expected_code)
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_failure_cleanup",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert caught.value.code == expected_code
    if cleanup_expected:
        cleanup.assert_called_once()
    else:
        cleanup.assert_not_called()


def test_restore_reports_fixed_cleanup_failure_code(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight", return_value=_CONTAINER),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database"),
        patch(
            "scripts.postgres_backup_restore._restore_database",
            side_effect=BackupRestoreError("restore_failed"),
        ),
        patch(
            "scripts.postgres_backup_restore._cleanup_target_database",
            side_effect=BackupRestoreError("target_cleanup_failed"),
        ),
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_cleanup_failure",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert caught.value.code == "target_cleanup_failed"


def test_restore_rejects_checksum_tamper_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    dump_path = backup_directory / "database.dump"
    dump_path.write_bytes(dump_path.read_bytes() + b"tampered")
    os.chmod(dump_path, 0o600)

    with patch("scripts.postgres_backup_restore._preflight") as preflight:
        with pytest.raises(BackupRestoreError) as caught:
            restore_backup(
                repository=repository,
                backup_directory=backup_directory,
                target_database="grocery_restore_tamper",
                expected_manifest_sha256=backup_receipt.manifest_sha256,
            )

    assert caught.value.code == "checksum_mismatch"
    preflight.assert_not_called()


def test_restore_requires_canonical_matching_manifest_receipt_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    for expected in ("A" * 64, "0" * 64):
        with (
            patch("scripts.postgres_backup_restore._preflight") as preflight,
            pytest.raises(BackupRestoreError) as caught,
        ):
            restore_backup(
                repository=repository,
                backup_directory=backup_directory,
                target_database="grocery_restore_manifest_receipt",
                expected_manifest_sha256=expected,
            )

        assert caught.value.code == "manifest_receipt_mismatch"
        preflight.assert_not_called()
    assert backup_receipt.manifest_sha256 == _manifest_receipt(backup_directory)


def test_alternate_valid_rehashed_archive_fails_pinned_manifest_receipt(
    repository: Path,
    tmp_path: Path,
    inventory: Inventory,
) -> None:
    first_root = tmp_path / "first-backups"
    second_root = tmp_path / "second-backups"
    first_root.mkdir()
    second_root.mkdir()
    _first_directory, first_receipt = _make_backup(repository, first_root, inventory)
    second_directory, second_receipt = _make_backup(repository, second_root, inventory)
    assert first_receipt.manifest_sha256 != second_receipt.manifest_sha256
    assert second_receipt.manifest_sha256 == _manifest_receipt(second_directory)

    with (
        patch("scripts.postgres_backup_restore._preflight") as preflight,
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=second_directory,
            target_database="grocery_restore_alternate_archive",
            expected_manifest_sha256=first_receipt.manifest_sha256,
        )

    assert caught.value.code == "manifest_receipt_mismatch"
    preflight.assert_not_called()


def test_restore_rejects_publication_contract_hash_tamper_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, _backup_receipt = _make_backup(repository, destination, inventory)
    manifest_path = backup_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inventory"]["publication_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    os.chmod(manifest_path, 0o600)

    with (
        patch("scripts.postgres_backup_restore._preflight") as preflight,
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_contract_hash_tamper",
            expected_manifest_sha256=_manifest_receipt(backup_directory),
        )

    assert caught.value.code == "publication_contract_mismatch"
    preflight.assert_not_called()


def test_restore_rejects_canonical_publication_tamper_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, _backup_receipt = _make_backup(repository, destination, inventory)
    manifest_path = backup_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inventory"]["canonical_publication"]["typed_fact_set_sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    os.chmod(manifest_path, 0o600)

    with (
        patch("scripts.postgres_backup_restore._preflight") as preflight,
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_canonical_tamper",
            expected_manifest_sha256=_manifest_receipt(backup_directory),
        )

    assert caught.value.code == "publication_contract_mismatch"
    preflight.assert_not_called()


def test_restore_rejects_broad_backup_permissions(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    os.chmod(backup_directory / "manifest.json", 0o644)

    with pytest.raises(BackupRestoreError) as caught:
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_permissions",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert caught.value.code == "backup_directory_permissions"


@pytest.mark.parametrize("filename", ("manifest.json", "database.dump"))
def test_restore_rejects_symlinked_backup_member_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
    filename: str,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    member = backup_directory / filename
    original = backup_directory / f"{filename}.original"
    member.rename(original)
    member.symlink_to(original.name)

    with (
        patch("scripts.postgres_backup_restore._preflight") as preflight,
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_symlink",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert caught.value.code == "dump_file_invalid"
    preflight.assert_not_called()


def test_restore_keeps_one_dump_descriptor_when_path_is_renamed_after_load(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    moved_directory = destination / "moved-after-load"
    validated_descriptors: list[int] = []
    restored_descriptors: list[int] = []

    def validate_dump(
        _repository: Path,
        descriptor: int,
        _container: _DatabaseContainer,
        _application_name: str,
    ) -> None:
        validated_descriptors.append(descriptor)
        backup_directory.rename(moved_directory)
        backup_directory.mkdir(mode=0o700)

    def restore_dump(
        _repository: Path,
        descriptor: int,
        _target: str,
        _container: _DatabaseContainer,
        _application_name: str,
    ) -> None:
        restored_descriptors.append(descriptor)
        duplicate = os.dup(descriptor)
        try:
            os.lseek(duplicate, 0, os.SEEK_SET)
            assert os.read(duplicate, 5) == b"PGDMP"
        finally:
            os.close(duplicate)

    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch(
            "scripts.postgres_backup_restore._validate_custom_dump",
            side_effect=validate_dump,
        ),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database"),
        patch(
            "scripts.postgres_backup_restore._restore_database",
            side_effect=restore_dump,
        ),
        patch("scripts.postgres_backup_restore._read_inventory", return_value=inventory),
        patch(
            "scripts.postgres_backup_restore._inspect_publication",
            return_value=_canonical_publication(),
        ),
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_path_swap",
            expected_manifest_sha256=backup_receipt.manifest_sha256,
        )

    assert validated_descriptors == restored_descriptors
    assert len(validated_descriptors) == 1


def test_inventory_parser_is_bounded_and_canonical(inventory: Inventory) -> None:
    parsed = _parse_inventory(inventory.canonical_data())
    assert parsed == inventory
    assert len(parsed.sha256) == 64
    assert len(parsed.publication_sha256) == 64

    with pytest.raises(BackupRestoreError) as caught:
        _parse_inventory({"rows": {"private.table": 1}, "migrations": [["app", "0001"]]})
    assert caught.value.code == "inventory_invalid"


def test_publication_contract_parser_rejects_invalid_shapes_and_chain(
    inventory: Inventory,
) -> None:
    invalid_contracts: list[dict[str, object]] = []

    extra_publisher = deepcopy(inventory.publication)
    activations = extra_publisher["activations"]
    assert isinstance(activations, list)
    assert isinstance(activations[0], dict)
    activations[0]["publisher_id"] = 7
    invalid_contracts.append(extra_publisher)

    boolean_sequence = deepcopy(inventory.publication)
    activations = boolean_sequence["activations"]
    assert isinstance(activations, list)
    assert isinstance(activations[0], dict)
    activations[0]["sequence"] = True
    invalid_contracts.append(boolean_sequence)

    broken_pointer = deepcopy(inventory.publication)
    activations = broken_pointer["activations"]
    assert isinstance(activations, list)
    assert isinstance(activations[1], dict)
    activations[1]["previous_revision_id"] = None
    invalid_contracts.append(broken_pointer)

    noncanonical_uuid = deepcopy(inventory.publication)
    channel = noncanonical_uuid["channel"]
    assert isinstance(channel, dict)
    channel["current_revision_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    invalid_contracts.append(noncanonical_uuid)

    invalid_hash = deepcopy(inventory.publication)
    active_revision = invalid_hash["active_revision"]
    assert isinstance(active_revision, dict)
    active_revision["typed_fact_set_sha256"] = "a" * 63
    invalid_contracts.append(invalid_hash)

    version_mismatch = deepcopy(inventory.publication)
    channel = version_mismatch["channel"]
    assert isinstance(channel, dict)
    channel["version"] = 3
    invalid_contracts.append(version_mismatch)

    entry_count_out_of_bounds = deepcopy(inventory.publication)
    active_revision = entry_count_out_of_bounds["active_revision"]
    assert isinstance(active_revision, dict)
    active_revision["entry_count"] = 100_001
    invalid_contracts.append(entry_count_out_of_bounds)

    for invalid_contract in invalid_contracts:
        invalid_inventory = inventory.canonical_data()
        invalid_inventory["publication"] = invalid_contract
        with pytest.raises(BackupRestoreError) as caught:
            _parse_inventory(invalid_inventory)
        assert caught.value.code == "inventory_invalid"


def test_publication_contract_rejects_rollback_to_never_active_revision(
    inventory: Inventory,
) -> None:
    invalid_inventory = inventory.canonical_data()
    contract = deepcopy(inventory.publication)
    activations = contract["activations"]
    channel = contract["channel"]
    active_revision = contract["active_revision"]
    assert isinstance(activations, list)
    assert isinstance(activations[1], dict)
    assert isinstance(channel, dict)
    assert isinstance(active_revision, dict)
    never_active = "77777777-7777-4777-8777-777777777777"
    activations[1]["operation"] = "ROLLBACK"
    activations[1]["target_revision_id"] = never_active
    channel["current_revision_id"] = never_active
    active_revision["id"] = never_active
    invalid_inventory["publication"] = contract

    with pytest.raises(BackupRestoreError) as caught:
        _parse_inventory(invalid_inventory)

    assert caught.value.code == "inventory_invalid"


def test_publication_inventory_sql_is_ordered_and_omits_publisher_identity() -> None:
    assert "ORDER BY activation.sequence" in _INVENTORY_SQL
    assert "replacement.supersedes_id = decision.id" in _INVENTORY_SQL
    assert "generation.status = 'VALIDATED'" in _INVENTORY_SQL
    assert "generation.accepted_row_count = revision.entry_count" in _INVENTORY_SQL
    assert "entry.revision_id = revision.id" in _INVENTORY_SQL
    assert "publisher" not in _INVENTORY_SQL


def test_canonical_inspector_uses_bounded_sanitized_subprocess_environment(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary_directory = repository / ".venv" / "bin"
    binary_directory.mkdir(parents=True)
    python = binary_directory / "python"
    python.write_bytes(b"synthetic")
    (repository / "manage.py").write_text("# synthetic\n", encoding="utf-8")
    monkeypatch.setenv("KAMIS_API_KEY", "must-not-cross-boundary")
    monkeypatch.setenv("DATABASE_URL", "must-not-cross-boundary")
    completed = subprocess.CompletedProcess(
        args=(),
        returncode=0,
        stdout=json.dumps(_canonical_publication().canonical_data()).encode("ascii"),
    )

    with (
        patch("scripts.postgres_backup_restore._require_same_database_container") as identity,
        patch("scripts.postgres_backup_restore.subprocess.run", return_value=completed) as run,
    ):
        inspected = _inspect_publication(
            repository,
            "grocery_restore_inspection",
            _CONTAINER,
            _APPLICATION_NAME,
        )

    assert inspected == _canonical_publication()
    assert identity.call_count == 2
    command = run.call_args.args[0]
    environment = run.call_args.kwargs["env"]
    assert command == (str(python), str(repository / "manage.py"), "inspect_recent_publication")
    assert run.call_args.kwargs["timeout"] == 120
    assert "KAMIS_API_KEY" not in environment
    assert "must-not-cross-boundary" not in str(environment)
    assert environment["DATABASE_URL"].endswith("/grocery_restore_inspection")
    assert environment["PGAPPNAME"] == _APPLICATION_NAME
    assert "grocery_restore_inspection" not in str(command)


@pytest.mark.parametrize(
    "unsafe_receipt",
    (
        {**_canonical_publication().canonical_data(), "unexpected": "value"},
        {**_canonical_publication().canonical_data(), "publication_state": "ERROR"},
        {**_canonical_publication().canonical_data(), "last_activation_operation": []},
    ),
)
def test_canonical_inspector_rejects_noncanonical_receipt(
    repository: Path,
    unsafe_receipt: dict[str, object],
) -> None:
    binary_directory = repository / ".venv" / "bin"
    binary_directory.mkdir(parents=True)
    (binary_directory / "python").write_bytes(b"synthetic")
    (repository / "manage.py").write_text("# synthetic\n", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=(),
        returncode=0,
        stdout=json.dumps(unsafe_receipt).encode("ascii"),
    )

    with (
        patch("scripts.postgres_backup_restore._require_same_database_container"),
        patch("scripts.postgres_backup_restore.subprocess.run", return_value=completed),
        pytest.raises(BackupRestoreError) as caught,
    ):
        _inspect_publication(repository, "grocery", _CONTAINER, _APPLICATION_NAME)

    assert caught.value.code == "publication_inspection_failed"


def test_docker_environment_drops_database_and_password_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "private-database-url")
    monkeypatch.setenv("PGPASSWORD", "private-password")
    monkeypatch.setenv("POSTGRES_PASSWORD", "private-postgres-password")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid:2376")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-context")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setenv("DOCKER_CERT_PATH", "/private/cert-path")
    monkeypatch.setenv("DOCKER_CONFIG", "/private/docker-config")

    environment = _docker_environment()

    for removed in (
        "DATABASE_URL",
        "PGPASSWORD",
        "POSTGRES_PASSWORD",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        assert removed not in environment
    assert environment == {
        "DOCKER_CONFIG": "/var/empty",
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
    }
    assert "private" not in str(environment)


def test_container_discovery_accepts_exact_local_compose_identity(
    repository: Path,
) -> None:
    inspection = subprocess.CompletedProcess(
        args=(),
        returncode=0,
        stdout=json.dumps(_container_inspection(repository)).encode(),
    )
    with (
        patch("scripts.postgres_backup_restore._require_local_docker_socket"),
        patch(
            "scripts.postgres_backup_restore._database_container_ids",
            side_effect=((_CONTAINER_ID,), (_CONTAINER_ID,)),
        ),
        patch(
            "scripts.postgres_backup_restore._run_docker_cli",
            return_value=inspection,
        ) as run,
    ):
        discovered = _discover_database_container(repository, _DOCKER)

    assert discovered == _CONTAINER_ID
    assert run.call_args.args[2] == (
        "container",
        "inspect",
        _CONTAINER_ID,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("project", "other-project"),
        ("service", "web"),
        ("working_dir", "/wrong/repository"),
        ("config_files", "/wrong/compose.yaml"),
        ("status", "exited"),
        ("health", "starting"),
        ("host_ip", "0.0.0.0"),  # noqa: S104 - unsafe published-port fixture.
        ("host_port", "6543"),
        ("extra_port", True),
    ),
)
def test_container_inspection_rejects_wrong_identity_or_broad_port(
    repository: Path,
    field: str,
    value: object,
) -> None:
    decoded = _container_inspection(repository)
    inspection = decoded[0]
    config = inspection["Config"]
    state = inspection["State"]
    network = inspection["NetworkSettings"]
    assert isinstance(config, dict)
    assert isinstance(state, dict)
    assert isinstance(network, dict)
    labels = config["Labels"]
    ports = network["Ports"]
    assert isinstance(labels, dict)
    assert isinstance(ports, dict)
    if field == "project":
        labels["com.docker.compose.project"] = value
    elif field == "service":
        labels["com.docker.compose.service"] = value
    elif field == "working_dir":
        labels["com.docker.compose.project.working_dir"] = value
    elif field == "config_files":
        labels["com.docker.compose.project.config_files"] = value
    elif field == "status":
        state["Status"] = value
    elif field == "health":
        health = state["Health"]
        assert isinstance(health, dict)
        health["Status"] = value
    elif field == "host_ip":
        mapping = ports["5432/tcp"]
        assert isinstance(mapping, list) and isinstance(mapping[0], dict)
        mapping[0]["HostIp"] = value
    elif field == "host_port":
        mapping = ports["5432/tcp"]
        assert isinstance(mapping, list) and isinstance(mapping[0], dict)
        mapping[0]["HostPort"] = value
    else:
        ports["9999/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "9999"}]

    with pytest.raises(BackupRestoreError) as caught:
        _validate_database_container_inspection(repository, _CONTAINER_ID, decoded)

    assert caught.value.code == "database_container_invalid"


def test_direct_docker_cli_uses_only_fixed_host_and_sanitized_environment(
    repository: Path,
) -> None:
    completed = subprocess.CompletedProcess(args=(), returncode=0, stdout=b"")
    with patch("scripts.postgres_backup_restore.subprocess.run", return_value=completed) as run:
        _run_docker_cli(
            repository,
            _DOCKER,
            ("version",),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            timeout=30,
            failure_code="docker_command_failed",
        )

    assert run.call_args.args[0] == (
        _DOCKER,
        "--host=unix:///var/run/docker.sock",
        "version",
    )
    assert run.call_args.kwargs["env"] == _docker_environment()


def test_database_command_uses_direct_exec_and_fails_on_container_swap(
    repository: Path,
) -> None:
    completed = subprocess.CompletedProcess(args=(), returncode=0, stdout=b"")
    with (
        patch("scripts.postgres_backup_restore._require_same_database_container") as identity,
        patch(
            "scripts.postgres_backup_restore._run_docker_cli",
            return_value=completed,
        ) as run,
    ):
        _run_database_command(
            repository,
            _CONTAINER,
            _APPLICATION_NAME,
            ("psql", "--version"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            timeout=30,
            failure_code="docker_command_failed",
        )

    assert identity.call_count == 2
    assert run.call_args.args[2] == (
        "exec",
        "-i",
        f"--env=PGAPPNAME={_APPLICATION_NAME}",
        _CONTAINER_ID,
        "psql",
        "--version",
    )

    with (
        patch(
            "scripts.postgres_backup_restore._require_same_database_container",
            side_effect=(None, BackupRestoreError("database_container_invalid")),
        ),
        patch("scripts.postgres_backup_restore._run_docker_cli", return_value=completed),
        pytest.raises(BackupRestoreError) as swapped,
    ):
        _run_database_command(
            repository,
            _CONTAINER,
            _APPLICATION_NAME,
            ("psql", "--version"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            timeout=30,
            failure_code="docker_command_failed",
        )

    assert swapped.value.code == "database_container_invalid"


def test_container_discovery_rejects_identity_swap(
    repository: Path,
) -> None:
    replacement_id = "f" * 64
    inspection = subprocess.CompletedProcess(
        args=(),
        returncode=0,
        stdout=json.dumps(_container_inspection(repository)).encode(),
    )
    with (
        patch("scripts.postgres_backup_restore._require_local_docker_socket"),
        patch(
            "scripts.postgres_backup_restore._database_container_ids",
            side_effect=((_CONTAINER_ID,), (replacement_id,)),
        ),
        patch("scripts.postgres_backup_restore._run_docker_cli", return_value=inspection),
        pytest.raises(BackupRestoreError) as caught,
    ):
        _discover_database_container(repository, _DOCKER)

    assert caught.value.code == "database_container_invalid"


def test_restore_command_has_shorter_gnu_timeout_and_unique_application_name(
    repository: Path,
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "database.dump"
    dump_path.write_bytes(b"PGDMPsynthetic")
    descriptor = os.open(dump_path, os.O_RDONLY)
    try:
        with patch("scripts.postgres_backup_restore._run_database_command") as run:
            _restore_database(
                repository,
                descriptor,
                "grocery_restore_timeout",
                _CONTAINER,
                _APPLICATION_NAME,
            )
    finally:
        os.close(descriptor)

    arguments = run.call_args.args[3]
    assert arguments[:5] == (
        "timeout",
        "--signal=TERM",
        "--kill-after=5s",
        "570s",
        "pg_restore",
    )
    assert run.call_args.kwargs["timeout"] == 600
    first = _new_application_name("restore")
    second = _new_application_name("restore")
    assert first != second
    assert first.startswith("grocery_restore_")
    assert len(first) == len("grocery_restore_") + 32


def test_target_cleanup_terminates_sessions_verifies_zero_drops_and_verifies_absence(
    repository: Path,
) -> None:
    with (
        patch(
            "scripts.postgres_backup_restore._capture_database_command",
            side_effect=(b"2\n", b"0\n", b"0\n"),
        ) as capture,
        patch("scripts.postgres_backup_restore._run_database_command") as run,
    ):
        _cleanup_target_database(
            repository,
            "grocery_restore_cleanup",
            _CONTAINER,
            _APPLICATION_NAME,
        )

    assert capture.call_count == 3
    terminate_arguments = capture.call_args_list[0].args[3]
    session_arguments = capture.call_args_list[1].args[3]
    absence_arguments = capture.call_args_list[2].args[3]
    assert "target_sessions AS MATERIALIZED" in terminate_arguments[-1]
    assert "pg_terminate_backend(pid)" in terminate_arguments[-1]
    assert "pg_stat_activity" in session_arguments[-1]
    assert "pg_database" in absence_arguments[-1]
    drop_arguments = run.call_args.args[3]
    assert drop_arguments == (
        "dropdb",
        "--no-password",
        "--username=grocery",
        "--if-exists",
        "--force",
        "grocery_restore_cleanup",
    )


def test_target_cleanup_fails_closed_when_sessions_remain(repository: Path) -> None:
    with (
        patch(
            "scripts.postgres_backup_restore._capture_database_command",
            side_effect=(b"1\n", b"1\n"),
        ),
        patch("scripts.postgres_backup_restore._run_database_command") as drop,
        pytest.raises(BackupRestoreError) as caught,
    ):
        _cleanup_target_database(
            repository,
            "grocery_restore_cleanup",
            _CONTAINER,
            _APPLICATION_NAME,
        )

    assert caught.value.code == "target_cleanup_failed"
    drop.assert_not_called()


def test_preflight_requires_all_postgres_18_tools_and_source_connectivity(
    repository: Path,
) -> None:
    versions = (
        b"pg_dump (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"pg_restore (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"createdb (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"dropdb (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"psql (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"timeout (GNU coreutils) 9.7\n",
        b"1\n",
    )
    with (
        patch("scripts.postgres_backup_restore._resolve_docker_binary", return_value=_DOCKER),
        patch(
            "scripts.postgres_backup_restore._discover_database_container",
            return_value=_CONTAINER_ID,
        ),
        patch(
            "scripts.postgres_backup_restore._capture_database_command",
            side_effect=versions,
        ) as capture,
    ):
        result = _preflight(repository, _APPLICATION_NAME)

    assert result == _CONTAINER
    assert [entry.args[3][0] for entry in capture.call_args_list] == [
        "pg_dump",
        "pg_restore",
        "createdb",
        "dropdb",
        "psql",
        "timeout",
        "psql",
    ]


def test_preflight_rejects_missing_or_wrong_major_tool(repository: Path) -> None:
    with (
        patch(
            "scripts.postgres_backup_restore._resolve_docker_binary",
            side_effect=BackupRestoreError("docker_unavailable"),
        ),
        pytest.raises(BackupRestoreError) as missing,
    ):
        _preflight(repository, _APPLICATION_NAME)
    assert missing.value.code == "docker_unavailable"

    with (
        patch("scripts.postgres_backup_restore._resolve_docker_binary", return_value=_DOCKER),
        patch(
            "scripts.postgres_backup_restore._discover_database_container",
            return_value=_CONTAINER_ID,
        ),
        patch(
            "scripts.postgres_backup_restore._capture_database_command",
            return_value=b"pg_dump (PostgreSQL) 17.9\n",
        ),
        pytest.raises(BackupRestoreError) as wrong_version,
    ):
        _preflight(repository, _APPLICATION_NAME)
    assert wrong_version.value.code == "postgres_version_mismatch"


def test_cli_failure_never_reflects_path_target_or_arbitrary_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path_marker = "/private/operator/path-marker"
    with patch(
        "scripts.postgres_backup_restore.create_backup",
        side_effect=RuntimeError("private-database-password-marker"),
    ):
        result = main(["backup", "--output-dir", path_marker])
    output = capsys.readouterr()
    assert result == 1
    assert output.out == "\n".join(
        (
            "status=failed",
            "code=internal_error",
            "cleanup=remove_incomplete_backup_directory_if_created",
            "",
        )
    )
    assert "private" not in output.out
    assert output.err == ""

    target_marker = "grocery_restore_private_marker"
    with patch(
        "scripts.postgres_backup_restore.restore_backup",
        side_effect=RuntimeError("private-database-password-marker"),
    ):
        result = main(
            [
                "restore",
                "--backup-dir",
                path_marker,
                "--target-database",
                target_marker,
                "--expected-manifest-sha256",
                "0" * 64,
            ]
        )
    output = capsys.readouterr()
    assert result == 1
    assert "code=internal_error" in output.out
    assert "cleanup=manual_target_cleanup_required" in output.out
    assert "private" not in output.out
    assert output.err == ""

    with patch(
        "scripts.postgres_backup_restore.restore_backup",
        side_effect=BackupRestoreError("restore_failed"),
    ):
        result = main(
            [
                "restore",
                "--backup-dir",
                path_marker,
                "--target-database",
                target_marker,
                "--expected-manifest-sha256",
                "0" * 64,
            ]
        )
    output = capsys.readouterr()
    assert result == 1
    assert "code=restore_failed" in output.out
    assert "cleanup=automatic_created_target_cleanup_verified_or_not_created" in output.out
    assert "private" not in output.out
    assert output.err == ""


def test_cli_usage_error_does_not_reflect_unknown_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["backup", "--private-password-marker"])
    output = capsys.readouterr()

    assert result == 2
    assert "code=usage_error" in output.out
    assert "private-password-marker" not in output.out
    assert output.err == ""


def test_restore_cli_requires_expected_manifest_receipt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("scripts.postgres_backup_restore.restore_backup") as restore:
        result = main(
            [
                "restore",
                "--backup-dir",
                "/private/backup",
                "--target-database",
                "grocery_restore_missing_receipt",
            ]
        )

    output = capsys.readouterr()
    assert result == 2
    assert "code=usage_error" in output.out
    assert "private" not in output.out
    restore.assert_not_called()


def test_target_database_contract_is_explicit_and_separate() -> None:
    assert _validated_target_database("grocery_restore_rehearsal_20260830") == (
        "grocery_restore_rehearsal_20260830"
    )
    for invalid in ("grocery", "other", "grocery_restore_", "grocery_restore_UPPER"):
        with pytest.raises(BackupRestoreError):
            _validated_target_database(invalid)


def test_target_preflight_uses_validated_literal_in_command_mode(
    repository: Path,
) -> None:
    target = "grocery_restore_rehearsal_20260830"
    with patch(
        "scripts.postgres_backup_restore._capture_database_command",
        return_value=b"0\n",
    ) as capture:
        assert _database_exists(repository, target, _CONTAINER, _APPLICATION_NAME) is False

    arguments = capture.call_args.args[3]
    assert not any(argument.startswith("--set=") for argument in arguments)
    expected_command = (
        "--command=SELECT count(*) FROM pg_database WHERE datname = "
        "'grocery_restore_rehearsal_20260830';"
    )
    assert arguments[-1] == expected_command
