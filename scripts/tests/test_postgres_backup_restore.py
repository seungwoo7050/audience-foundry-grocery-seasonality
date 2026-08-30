"""Unit tests for bounded local PostgreSQL backup and restore assurance."""

from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from unittest.mock import call, patch

import pytest

from scripts.postgres_backup_restore import (
    _INVENTORY_SQL,
    BackupReceipt,
    BackupRestoreError,
    Inventory,
    _database_exists,
    _docker_environment,
    _parse_inventory,
    _preflight,
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


def _synthetic_dump(_repository: Path, descriptor: int) -> None:
    os.write(descriptor, b"PGDMPsynthetic-custom-format")


def _make_backup(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> tuple[Path, BackupReceipt]:
    with (
        patch("scripts.postgres_backup_restore._preflight"),
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
    assert manifest["format_version"] == "grocery-postgres-custom-v1"
    assert manifest["postgres_major"] == 18
    assert manifest["source_database"] == "grocery"
    assert manifest["inventory"]["sha256"] == inventory.sha256
    assert manifest["inventory"]["publication"] == inventory.publication
    assert manifest["inventory"]["publication_sha256"] == inventory.publication_sha256
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
        patch("scripts.postgres_backup_restore._preflight"),
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
        call(repository.resolve(), "grocery"),
        call(repository.resolve(), "grocery"),
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


def test_restore_validates_then_creates_separate_target_and_compares_inventory(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, backup_receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight") as preflight,
        patch("scripts.postgres_backup_restore._validate_custom_dump") as validate_dump,
        patch("scripts.postgres_backup_restore._database_exists", return_value=False) as exists,
        patch("scripts.postgres_backup_restore._create_target_database") as create_target,
        patch("scripts.postgres_backup_restore._restore_database") as restore,
        patch("scripts.postgres_backup_restore._read_inventory", return_value=inventory),
    ):
        receipt = restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_rehearsal1",
        )

    preflight.assert_called_once_with(repository.resolve())
    validate_dump.assert_called_once_with(repository.resolve(), backup_directory / "database.dump")
    exists.assert_called_once_with(repository.resolve(), "grocery_restore_rehearsal1")
    create_target.assert_called_once_with(repository.resolve(), "grocery_restore_rehearsal1")
    restore.assert_called_once_with(
        repository.resolve(),
        backup_directory / "database.dump",
        "grocery_restore_rehearsal1",
    )
    assert receipt.backup_id == backup_receipt.backup_id
    assert "row_counts_consistent=yes" in receipt.render()
    assert "migrations_consistent=yes" in receipt.render()
    assert "publication_contract_consistent=yes" in receipt.render()
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
    backup_directory, _receipt = _make_backup(repository, destination, inventory)
    with pytest.raises(BackupRestoreError) as source:
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery",
        )
    assert source.value.code == "target_database_is_source"

    with pytest.raises(BackupRestoreError) as invalid:
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="production",
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
    backup_directory, _receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database"),
        patch("scripts.postgres_backup_restore._restore_database"),
        patch("scripts.postgres_backup_restore._read_inventory", return_value=restored),
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_drift",
        )

    assert caught.value.code == expected_code


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
    backup_directory, _receipt = _make_backup(repository, destination, inventory)
    with (
        patch("scripts.postgres_backup_restore._preflight"),
        patch("scripts.postgres_backup_restore._validate_custom_dump"),
        patch("scripts.postgres_backup_restore._database_exists", return_value=False),
        patch("scripts.postgres_backup_restore._create_target_database"),
        patch("scripts.postgres_backup_restore._restore_database"),
        patch("scripts.postgres_backup_restore._read_inventory", return_value=restored),
        pytest.raises(BackupRestoreError) as caught,
    ):
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_publication_drift",
        )

    assert caught.value.code == "publication_contract_mismatch"


def test_restore_rejects_checksum_tamper_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, _receipt = _make_backup(repository, destination, inventory)
    dump_path = backup_directory / "database.dump"
    dump_path.write_bytes(dump_path.read_bytes() + b"tampered")
    os.chmod(dump_path, 0o600)

    with patch("scripts.postgres_backup_restore._preflight") as preflight:
        with pytest.raises(BackupRestoreError) as caught:
            restore_backup(
                repository=repository,
                backup_directory=backup_directory,
                target_database="grocery_restore_tamper",
            )

    assert caught.value.code == "checksum_mismatch"
    preflight.assert_not_called()


def test_restore_rejects_publication_contract_hash_tamper_before_preflight(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, _receipt = _make_backup(repository, destination, inventory)
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
        )

    assert caught.value.code == "publication_contract_mismatch"
    preflight.assert_not_called()


def test_restore_rejects_broad_backup_permissions(
    repository: Path,
    destination: Path,
    inventory: Inventory,
) -> None:
    backup_directory, _receipt = _make_backup(repository, destination, inventory)
    os.chmod(backup_directory / "manifest.json", 0o644)

    with pytest.raises(BackupRestoreError) as caught:
        restore_backup(
            repository=repository,
            backup_directory=backup_directory,
            target_database="grocery_restore_permissions",
        )

    assert caught.value.code == "backup_directory_permissions"


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


def test_docker_environment_drops_database_and_password_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "private-database-url")
    monkeypatch.setenv("PGPASSWORD", "private-password")
    monkeypatch.setenv("POSTGRES_PASSWORD", "private-postgres-password")
    monkeypatch.setenv("DOCKER_HOST", "unix:///safe/docker.sock")

    environment = _docker_environment()

    assert environment["DOCKER_HOST"] == "unix:///safe/docker.sock"
    assert "DATABASE_URL" not in environment
    assert "PGPASSWORD" not in environment
    assert "POSTGRES_PASSWORD" not in environment
    assert "private" not in str(environment)


def test_preflight_requires_all_postgres_18_tools_and_source_connectivity(
    repository: Path,
) -> None:
    versions = (
        b"pg_dump (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"pg_restore (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"createdb (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"psql (PostgreSQL) 18.6 (Debian 18.6-1)\n",
        b"1\n",
    )
    with (
        patch("scripts.postgres_backup_restore.shutil.which", return_value="/safe/docker"),
        patch(
            "scripts.postgres_backup_restore._capture_compose",
            side_effect=versions,
        ) as capture,
    ):
        _preflight(repository)

    assert [entry.args[1][0] for entry in capture.call_args_list] == [
        "pg_dump",
        "pg_restore",
        "createdb",
        "psql",
        "psql",
    ]


def test_preflight_rejects_missing_or_wrong_major_tool(repository: Path) -> None:
    with (
        patch("scripts.postgres_backup_restore.shutil.which", return_value=None),
        pytest.raises(BackupRestoreError) as missing,
    ):
        _preflight(repository)
    assert missing.value.code == "docker_unavailable"

    with (
        patch("scripts.postgres_backup_restore.shutil.which", return_value="/safe/docker"),
        patch(
            "scripts.postgres_backup_restore._capture_compose",
            return_value=b"pg_dump (PostgreSQL) 17.9\n",
        ),
        pytest.raises(BackupRestoreError) as wrong_version,
    ):
        _preflight(repository)
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
            ]
        )
    output = capsys.readouterr()
    assert result == 1
    assert "code=internal_error" in output.out
    assert "cleanup=inspect_and_drop_explicit_restore_target_if_created" in output.out
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
        "scripts.postgres_backup_restore._capture_compose",
        return_value=b"0\n",
    ) as capture:
        assert _database_exists(repository, target) is False

    arguments = capture.call_args.args[1]
    assert not any(argument.startswith("--set=") for argument in arguments)
    expected_command = (
        "--command=SELECT count(*) FROM pg_database WHERE datname = "
        "'grocery_restore_rehearsal_20260830';"
    )
    assert arguments[-1] == expected_command
