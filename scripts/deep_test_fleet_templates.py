#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ORG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*-test$")
REPO_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
EXPECTED_ORGANIZATION_COUNT = 25
EXPECTED_REPOSITORY_COUNT = 4
EXPECTED_TOTAL = EXPECTED_ORGANIZATION_COUNT * EXPECTED_REPOSITORY_COUNT
BOOTSTRAP_OPERATION = "deep-test-fleet-20260808"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"


@dataclass(frozen=True)
class SuiteSpec:
    order: int
    name: str
    suite: str
    description: str


@dataclass(frozen=True)
class Fleet:
    schema_version: int
    bootstrap_date: str
    tracking_issue: str
    live_creation_enabled: bool
    visibility: str
    owner_login: str
    owner_id: int
    organizations: tuple[str, ...]
    repositories: tuple[SuiteSpec, ...]

    @property
    def total(self) -> int:
        return len(self.organizations) * len(self.repositories)


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_fleet(path: Path) -> Fleet:
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object_pairs)
    required = {
        "schema_version",
        "bootstrap_date",
        "tracking_issue",
        "live_creation_enabled",
        "visibility",
        "expected_owner",
        "organizations",
        "repositories",
    }
    if set(data) != required:
        raise ValueError(f"manifest keys differ: expected={sorted(required)} observed={sorted(data)}")
    if data["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    if data["live_creation_enabled"] is not False:
        raise ValueError("checked-in manifest must remain live_creation_enabled=false")
    if data["visibility"] != "public":
        raise ValueError("deep test repositories must be explicitly public")
    owner = data["expected_owner"]
    if set(owner) != {"login", "id"} or owner["login"] != "ORESoftware" or owner["id"] != 11139560:
        raise ValueError("expected owner identity drift")
    organizations = tuple(data["organizations"])
    if len(organizations) != EXPECTED_ORGANIZATION_COUNT:
        raise ValueError(
            f"expected {EXPECTED_ORGANIZATION_COUNT} organizations, observed {len(organizations)}"
        )
    if tuple(sorted(organizations)) != organizations:
        raise ValueError("organizations must be sorted")
    if len(set(organizations)) != len(organizations):
        raise ValueError("duplicate organization")
    for organization in organizations:
        if not ORG_PATTERN.fullmatch(organization):
            raise ValueError(f"unsafe test organization: {organization}")

    repositories: list[SuiteSpec] = []
    for row in data["repositories"]:
        if set(row) != {"order", "name", "suite", "description"}:
            raise ValueError(f"unexpected repository keys: {row}")
        spec = SuiteSpec(
            order=int(row["order"]),
            name=str(row["name"]),
            suite=str(row["suite"]),
            description=str(row["description"]),
        )
        if not REPO_PATTERN.fullmatch(spec.name):
            raise ValueError(f"unsafe repository name: {spec.name}")
        if spec.suite not in {"contract", "chaos", "upgrade", "security"}:
            raise ValueError(f"unknown suite: {spec.suite}")
        if len(spec.description) < 40 or len(spec.description) > 180:
            raise ValueError(f"description length invalid for {spec.name}")
        repositories.append(spec)
    repositories.sort(key=lambda item: item.order)
    if [item.order for item in repositories] != list(range(1, EXPECTED_REPOSITORY_COUNT + 1)):
        raise ValueError("repository order must be contiguous from 1")
    if len(repositories) != EXPECTED_REPOSITORY_COUNT:
        raise ValueError(
            f"expected {EXPECTED_REPOSITORY_COUNT} repositories per organization"
        )
    if len({item.name for item in repositories}) != len(repositories):
        raise ValueError("duplicate repository name")
    if len({item.suite for item in repositories}) != len(repositories):
        raise ValueError("duplicate suite")

    fleet = Fleet(
        schema_version=1,
        bootstrap_date=str(data["bootstrap_date"]),
        tracking_issue=str(data["tracking_issue"]),
        live_creation_enabled=False,
        visibility="public",
        owner_login=str(owner["login"]),
        owner_id=int(owner["id"]),
        organizations=organizations,
        repositories=tuple(repositories),
    )
    if fleet.total != EXPECTED_TOTAL:
        raise ValueError(f"expected total {EXPECTED_TOTAL}, observed {fleet.total}")
    return fleet


def _dedent(value: str) -> str:
    return textwrap.dedent(value).lstrip("\n").rstrip() + "\n"


def _contract_files() -> dict[str, str]:
    module = _dedent(
        r'''
        from __future__ import annotations

        import hashlib
        import json
        import random
        from dataclasses import dataclass
        from typing import Iterable


        class IdempotencyConflict(ValueError):
            pass


        @dataclass(frozen=True)
        class Command:
            kind: str
            entity_id: str
            value: str | None
            idempotency_key: str

            def fingerprint(self) -> str:
                body = json.dumps(
                    {"entity_id": self.entity_id, "kind": self.kind, "value": self.value},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                return hashlib.sha256(body).hexdigest()


        @dataclass(frozen=True)
        class Outcome:
            revision: int
            entity_id: str
            value: str | None
            deleted: bool


        class ReferenceStore:
            def __init__(self) -> None:
                self._values: dict[str, str] = {}
                self._tombstones: set[str] = set()
                self._revision = 0
                self._dedupe: dict[str, tuple[str, Outcome]] = {}
                self._history: list[Outcome] = []

            @property
            def revision(self) -> int:
                return self._revision

            @property
            def history(self) -> tuple[Outcome, ...]:
                return tuple(self._history)

            def apply(self, command: Command) -> Outcome:
                fingerprint = command.fingerprint()
                prior = self._dedupe.get(command.idempotency_key)
                if prior is not None:
                    prior_fingerprint, prior_outcome = prior
                    if prior_fingerprint != fingerprint:
                        raise IdempotencyConflict("idempotency key was reused for different intent")
                    return prior_outcome

                if command.kind == "create":
                    if command.value is None or command.entity_id in self._values:
                        raise ValueError("create requires a value and a missing entity")
                    self._values[command.entity_id] = command.value
                    self._tombstones.discard(command.entity_id)
                    deleted = False
                elif command.kind == "update":
                    if command.value is None or command.entity_id not in self._values:
                        raise ValueError("update requires an existing entity and a value")
                    self._values[command.entity_id] = command.value
                    deleted = False
                elif command.kind == "delete":
                    if command.entity_id not in self._values:
                        raise ValueError("delete requires an existing entity")
                    del self._values[command.entity_id]
                    self._tombstones.add(command.entity_id)
                    deleted = True
                else:
                    raise ValueError(f"unknown command kind: {command.kind}")

                self._revision += 1
                outcome = Outcome(
                    revision=self._revision,
                    entity_id=command.entity_id,
                    value=None if deleted else self._values[command.entity_id],
                    deleted=deleted,
                )
                self._dedupe[command.idempotency_key] = (fingerprint, outcome)
                self._history.append(outcome)
                return outcome

            def snapshot(self) -> str:
                return json.dumps(
                    {
                        "revision": self._revision,
                        "tombstones": sorted(self._tombstones),
                        "values": dict(sorted(self._values.items())),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )


        def generate_valid_trace(seed: int, steps: int = 250) -> tuple[Command, ...]:
            randomizer = random.Random(seed)
            live: set[str] = set()
            next_id = 0
            commands: list[Command] = []
            for index in range(steps):
                decision = randomizer.random()
                if not live or decision < 0.36:
                    entity_id = f"entity-{seed}-{next_id}"
                    next_id += 1
                    live.add(entity_id)
                    kind = "create"
                    value = f"value-{randomizer.randrange(1_000_000)}"
                elif decision < 0.79:
                    entity_id = randomizer.choice(sorted(live))
                    kind = "update"
                    value = f"value-{randomizer.randrange(1_000_000)}"
                else:
                    entity_id = randomizer.choice(sorted(live))
                    live.remove(entity_id)
                    kind = "delete"
                    value = None
                commands.append(
                    Command(
                        kind=kind,
                        entity_id=entity_id,
                        value=value,
                        idempotency_key=f"seed-{seed}-step-{index}",
                    )
                )
            return tuple(commands)


        def replay(commands: Iterable[Command], duplicate_every: int = 0) -> ReferenceStore:
            store = ReferenceStore()
            for index, command in enumerate(commands):
                first = store.apply(command)
                if duplicate_every and index % duplicate_every == 0:
                    assert store.apply(command) == first
            return store
        '''
    )
    tests = _dedent(
        r'''
        import unittest

        from deep_tests.contract_model import (
            Command,
            IdempotencyConflict,
            ReferenceStore,
            generate_valid_trace,
            replay,
        )


        class ContractConformanceTests(unittest.TestCase):
            def test_stateful_model_replays_deterministically_across_many_seeds(self) -> None:
                for seed in range(32):
                    commands = generate_valid_trace(seed, steps=240)
                    first = replay(commands, duplicate_every=7)
                    second = replay(commands, duplicate_every=5)
                    self.assertEqual(first.snapshot(), second.snapshot(), f"seed={seed}")
                    revisions = [outcome.revision for outcome in first.history]
                    self.assertEqual(revisions, list(range(1, len(revisions) + 1)))

            def test_idempotency_key_reuse_with_different_intent_fails_closed(self) -> None:
                store = ReferenceStore()
                store.apply(Command("create", "a", "one", "same-key"))
                with self.assertRaises(IdempotencyConflict):
                    store.apply(Command("update", "a", "two", "same-key"))

            def test_snapshot_is_canonical_independent_of_insertion_order(self) -> None:
                left = ReferenceStore()
                right = ReferenceStore()
                for entity_id in ("b", "a", "c"):
                    left.apply(Command("create", entity_id, entity_id.upper(), f"l-{entity_id}"))
                for entity_id in ("c", "b", "a"):
                    right.apply(Command("create", entity_id, entity_id.upper(), f"r-{entity_id}"))
                self.assertEqual(left.snapshot(), right.snapshot())

            def test_delete_creates_tombstone_and_duplicate_is_side_effect_free(self) -> None:
                store = ReferenceStore()
                store.apply(Command("create", "a", "one", "create-a"))
                delete = Command("delete", "a", None, "delete-a")
                first = store.apply(delete)
                duplicate = store.apply(delete)
                self.assertEqual(first, duplicate)
                self.assertEqual(store.revision, 2)
                self.assertIn('"tombstones":["a"]', store.snapshot())


        if __name__ == "__main__":
            unittest.main()
        '''
    )
    return {
        "src/deep_tests/contract_model.py": module,
        "tests/test_contract_conformance.py": tests,
    }


def _chaos_files() -> dict[str, str]:
    module = _dedent(
        r'''
        from __future__ import annotations

        import random
        from dataclasses import dataclass


        class SimulatedCrash(RuntimeError):
            pass


        class SimulatedTimeout(TimeoutError):
            pass


        @dataclass(frozen=True)
        class Operation:
            key: str
            entity_id: str
            value: int
            sequence: int


        class DurableService:
            def __init__(self) -> None:
                self.journal: dict[str, Operation] = {}
                self.committed: set[str] = set()
                self.materialized: dict[str, int] = {}
                self.side_effect_counts: dict[str, int] = {}
                self.crashed = False

            def _commit(self, operation: Operation) -> None:
                if operation.key in self.committed:
                    return
                self.materialized[operation.entity_id] = operation.value
                self.side_effect_counts[operation.key] = self.side_effect_counts.get(operation.key, 0) + 1
                self.committed.add(operation.key)

            def receive(self, operation: Operation, fault: str = "none") -> str:
                if self.crashed:
                    raise SimulatedCrash("service is crashed")
                prior = self.journal.get(operation.key)
                if prior is not None and prior != operation:
                    raise ValueError("idempotency key conflict")
                self.journal.setdefault(operation.key, operation)
                if fault == "crash_after_journal":
                    self.crashed = True
                    raise SimulatedCrash("crash after durable journal append")
                self._commit(operation)
                if fault == "timeout_after_commit":
                    raise SimulatedTimeout("response was lost after commit")
                return "committed"

            def recover(self) -> None:
                self.crashed = False
                for key in sorted(self.journal):
                    self._commit(self.journal[key])


        class Replica:
            def __init__(self) -> None:
                self.applied: set[str] = set()
                self.state: dict[str, int] = {}
                self.entity_sequences: dict[str, int] = {}

            def apply(self, operation: Operation) -> None:
                if operation.key in self.applied:
                    return
                self.applied.add(operation.key)
                current = self.entity_sequences.get(operation.entity_id, -1)
                if operation.sequence >= current:
                    self.entity_sequences[operation.entity_id] = operation.sequence
                    self.state[operation.entity_id] = operation.value


        @dataclass(frozen=True)
        class SimulationResult:
            retries: int
            operations: int
            primary_state: dict[str, int]
            replica_states: tuple[dict[str, int], ...]
            side_effect_counts: dict[str, int]


        def simulate(seed: int, operations: int = 140) -> SimulationResult:
            randomizer = random.Random(seed)
            primary = DurableService()
            replicas = [Replica(), Replica(), Replica()]
            delayed: list[tuple[int, Operation]] = []
            expected: dict[str, int] = {}
            retries = 0

            for index in range(operations):
                operation = Operation(
                    key=f"seed-{seed}-op-{index}",
                    entity_id=f"entity-{randomizer.randrange(17)}",
                    value=randomizer.randrange(1_000_000),
                    sequence=index,
                )
                expected[operation.entity_id] = operation.value
                fault = randomizer.choices(
                    ["none", "drop_before_send", "duplicate", "crash_after_journal", "timeout_after_commit"],
                    weights=[55, 10, 12, 10, 13],
                    k=1,
                )[0]
                delivered = False
                for attempt in range(6):
                    try:
                        if fault == "drop_before_send" and attempt == 0:
                            raise SimulatedTimeout("request was dropped before send")
                        active_fault = fault if attempt == 0 else "none"
                        primary.receive(operation, active_fault)
                        if fault == "duplicate" and attempt == 0:
                            primary.receive(operation)
                        delivered = True
                        break
                    except SimulatedCrash:
                        retries += 1
                        primary.recover()
                    except SimulatedTimeout:
                        retries += 1
                        primary.recover()
                if not delivered and operation.key not in primary.committed:
                    raise AssertionError(f"operation did not recover: {operation.key}")

                for replica_index, replica in enumerate(replicas):
                    if randomizer.random() < 0.28:
                        delayed.append((replica_index, operation))
                    else:
                        replica.apply(operation)
                        if randomizer.random() < 0.22:
                            replica.apply(operation)

            primary.recover()
            randomizer.shuffle(delayed)
            for replica_index, operation in delayed:
                replicas[replica_index].apply(operation)
                if randomizer.random() < 0.5:
                    replicas[replica_index].apply(operation)
            # Reconciliation after partitions heal is authoritative and idempotent.
            for operation in primary.journal.values():
                for replica in replicas:
                    replica.apply(operation)

            if primary.materialized != expected:
                raise AssertionError("primary state diverged from acknowledged intent")
            if any(replica.state != expected for replica in replicas):
                raise AssertionError("replicas did not converge after healing")
            if any(count != 1 for count in primary.side_effect_counts.values()):
                raise AssertionError("a retried operation produced duplicate side effects")
            return SimulationResult(
                retries=retries,
                operations=operations,
                primary_state=dict(primary.materialized),
                replica_states=tuple(dict(replica.state) for replica in replicas),
                side_effect_counts=dict(primary.side_effect_counts),
            )
        '''
    )
    tests = _dedent(
        r'''
        import unittest

        from deep_tests.chaos_model import DurableService, Operation, SimulatedTimeout, simulate


        class ChaosRecoveryTests(unittest.TestCase):
            def test_fault_matrix_converges_without_duplicate_side_effects(self) -> None:
                for seed in range(40):
                    result = simulate(seed, operations=120)
                    self.assertTrue(result.retries >= 0)
                    self.assertEqual(len(result.side_effect_counts), result.operations)
                    self.assertTrue(all(count == 1 for count in result.side_effect_counts.values()))
                    for replica_state in result.replica_states:
                        self.assertEqual(replica_state, result.primary_state, f"seed={seed}")

            def test_timeout_after_commit_is_safe_to_retry(self) -> None:
                service = DurableService()
                operation = Operation("key", "entity", 7, 1)
                with self.assertRaises(SimulatedTimeout):
                    service.receive(operation, "timeout_after_commit")
                service.receive(operation)
                self.assertEqual(service.side_effect_counts["key"], 1)
                self.assertEqual(service.materialized["entity"], 7)

            def test_conflicting_retry_fails_closed(self) -> None:
                service = DurableService()
                service.receive(Operation("key", "entity", 7, 1))
                with self.assertRaises(ValueError):
                    service.receive(Operation("key", "entity", 8, 1))


        if __name__ == "__main__":
            unittest.main()
        '''
    )
    return {
        "src/deep_tests/chaos_model.py": module,
        "tests/test_chaos_recovery.py": tests,
    }


def _upgrade_files() -> dict[str, str]:
    module = _dedent(
        r'''
        from __future__ import annotations

        import copy
        from typing import Any, Iterable


        class IncompatibleChange(ValueError):
            pass


        def semantic(record: dict[str, Any]) -> dict[str, Any]:
            version = record["version"]
            if version == 1:
                return {"id": record["id"], "name": record["name"], "labels": []}
            if version == 2:
                return {
                    "id": record["id"],
                    "name": record["display_name"],
                    "labels": sorted(record.get("labels", [])),
                }
            if version == 3:
                return {
                    "id": record["id"],
                    "name": record["display_name"],
                    "labels": sorted(record.get("metadata", {}).get("labels", [])),
                }
            raise IncompatibleChange(f"unsupported version: {version}")


        def migrate_one(record: dict[str, Any], direction: int) -> dict[str, Any]:
            source = copy.deepcopy(record)
            version = source["version"]
            if direction == 1 and version == 1:
                return {
                    "version": 2,
                    "id": source["id"],
                    "display_name": source["name"],
                    "labels": [],
                }
            if direction == 1 and version == 2:
                return {
                    "version": 3,
                    "id": source["id"],
                    "display_name": source["display_name"],
                    "metadata": {"labels": sorted(source.get("labels", []))},
                    "status": "active",
                }
            if direction == -1 and version == 3:
                return {
                    "version": 2,
                    "id": source["id"],
                    "display_name": source["display_name"],
                    "labels": sorted(source.get("metadata", {}).get("labels", [])),
                }
            if direction == -1 and version == 2:
                labels = sorted(source.get("labels", []))
                if labels:
                    raise IncompatibleChange("v1 cannot represent labels without explicit loss approval")
                return {"version": 1, "id": source["id"], "name": source["display_name"]}
            raise IncompatibleChange(f"cannot migrate version={version} direction={direction}")


        def migrate(record: dict[str, Any], target: int) -> dict[str, Any]:
            if target not in {1, 2, 3}:
                raise IncompatibleChange("target version is unsupported")
            current = copy.deepcopy(record)
            while current["version"] < target:
                current = migrate_one(current, 1)
            while current["version"] > target:
                current = migrate_one(current, -1)
            return current


        def negotiate(local: Iterable[int], remote: Iterable[int]) -> int:
            common = sorted(set(local) & set(remote))
            if not common:
                raise IncompatibleChange("no common protocol version")
            return common[-1]


        def assert_non_destructive_required_change(old_required: set[str], new_required: set[str]) -> None:
            removed = old_required - new_required
            if removed:
                raise IncompatibleChange(f"required fields removed: {sorted(removed)}")


        def read_with_version(record: dict[str, Any], reader_version: int) -> dict[str, Any]:
            # Newer writers may add fields; readers consume only their representable semantic view.
            source_semantic = semantic(record)
            if reader_version == 1:
                return {"id": source_semantic["id"], "name": source_semantic["name"]}
            if reader_version in {2, 3}:
                return source_semantic
            raise IncompatibleChange("reader version is unsupported")


        def replay_snapshot(records: Iterable[dict[str, Any]], target_version: int) -> tuple[dict[str, Any], ...]:
            migrated = [migrate(record, target_version) for record in records]
            return tuple(sorted(migrated, key=lambda item: item["id"]))
        '''
    )
    tests = _dedent(
        r'''
        import random
        import unittest

        from deep_tests.upgrade_model import (
            IncompatibleChange,
            assert_non_destructive_required_change,
            migrate,
            negotiate,
            read_with_version,
            replay_snapshot,
            semantic,
        )


        class UpgradeCompatibilityTests(unittest.TestCase):
            def test_lossless_v1_v3_v1_round_trip_across_many_records(self) -> None:
                for seed in range(50):
                    randomizer = random.Random(seed)
                    source = {
                        "version": 1,
                        "id": f"id-{seed}",
                        "name": f"name-{randomizer.randrange(1_000_000)}",
                    }
                    upgraded = migrate(source, 3)
                    downgraded = migrate(upgraded, 1)
                    self.assertEqual(source, downgraded)
                    self.assertEqual(semantic(source), semantic(upgraded))

            def test_lossy_downgrade_requires_explicit_handling(self) -> None:
                v2 = {"version": 2, "id": "1", "display_name": "one", "labels": ["protected"]}
                with self.assertRaises(IncompatibleChange):
                    migrate(v2, 1)

            def test_new_writer_remains_readable_by_old_reader(self) -> None:
                v3 = {
                    "version": 3,
                    "id": "1",
                    "display_name": "one",
                    "metadata": {"labels": ["a", "b"]},
                    "status": "active",
                    "future_field": {"ignored": True},
                }
                self.assertEqual(read_with_version(v3, 1), {"id": "1", "name": "one"})

            def test_version_negotiation_chooses_highest_common_and_fails_closed(self) -> None:
                self.assertEqual(negotiate([1, 2, 3], [2, 3, 4]), 3)
                with self.assertRaises(IncompatibleChange):
                    negotiate([1], [2, 3])

            def test_required_field_removal_is_rejected(self) -> None:
                assert_non_destructive_required_change({"id", "name"}, {"id", "name", "status"})
                with self.assertRaises(IncompatibleChange):
                    assert_non_destructive_required_change({"id", "name"}, {"id"})

            def test_snapshot_replay_is_deterministic(self) -> None:
                records = [
                    {"version": 1, "id": "b", "name": "two"},
                    {"version": 1, "id": "a", "name": "one"},
                ]
                first = replay_snapshot(records, 3)
                second = replay_snapshot(reversed(records), 3)
                self.assertEqual(first, second)


        if __name__ == "__main__":
            unittest.main()
        '''
    )
    return {
        "src/deep_tests/upgrade_model.py": module,
        "tests/test_upgrade_compatibility.py": tests,
    }


def _security_files() -> dict[str, str]:
    module = _dedent(
        r'''
        from __future__ import annotations

        import hashlib
        import hmac
        import ipaddress
        import re
        import time
        import urllib.parse
        from dataclasses import dataclass, field
        from pathlib import PurePosixPath


        class BoundaryViolation(ValueError):
            pass


        def normalize_relative_path(value: str) -> str:
            decoded = urllib.parse.unquote(urllib.parse.unquote(value))
            if not decoded or "\x00" in decoded or "\\" in decoded or decoded.startswith("/"):
                raise BoundaryViolation("path must be a non-empty relative POSIX path")
            parts = PurePosixPath(decoded).parts
            if any(part in {"", ".", ".."} for part in parts):
                raise BoundaryViolation("path traversal segment is forbidden")
            normalized = "/".join(parts)
            if normalized != decoded:
                raise BoundaryViolation("path normalization changed the request")
            return normalized


        def validate_outbound_url(value: str, allowed_hosts: set[str]) -> str:
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise BoundaryViolation("outbound URL must use HTTPS without user info")
            host = parsed.hostname.rstrip(".").lower()
            if host not in {item.rstrip(".").lower() for item in allowed_hosts}:
                raise BoundaryViolation("outbound host is not allowlisted")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            ):
                raise BoundaryViolation("non-public IP destinations are forbidden")
            if parsed.fragment:
                raise BoundaryViolation("fragments are not sent upstream")
            return urllib.parse.urlunsplit(parsed)


        def redact(value: str) -> str:
            token_pattern = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}")
            bearer_pattern = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+")
            redacted = token_pattern.sub("[REDACTED]", value)
            return bearer_pattern.sub(r"\1[REDACTED]", redacted)


        @dataclass(frozen=True)
        class Principal:
            tenant_id: str
            roles: frozenset[str]


        def authorize_read(principal: Principal, resource_tenant_id: str) -> None:
            if principal.tenant_id != resource_tenant_id:
                raise BoundaryViolation("cross-tenant read is forbidden")
            if not ({"reader", "admin"} & principal.roles):
                raise BoundaryViolation("read role is required")


        def sign(secret: bytes, timestamp: int, nonce: str, body: bytes) -> str:
            message = f"{timestamp}.{nonce}.".encode() + body
            return hmac.new(secret, message, hashlib.sha256).hexdigest()


        @dataclass
        class ReplayWindow:
            max_skew_seconds: int = 300
            seen: dict[str, int] = field(default_factory=dict)

            def verify(
                self,
                secret: bytes,
                timestamp: int,
                nonce: str,
                body: bytes,
                signature: str,
                now: int | None = None,
            ) -> None:
                current = int(time.time()) if now is None else now
                if abs(current - timestamp) > self.max_skew_seconds:
                    raise BoundaryViolation("signature timestamp is outside the replay window")
                if nonce in self.seen:
                    raise BoundaryViolation("nonce replay detected")
                expected = sign(secret, timestamp, nonce, body)
                if not hmac.compare_digest(expected, signature):
                    raise BoundaryViolation("signature mismatch")
                self.seen[nonce] = timestamp
                cutoff = current - self.max_skew_seconds
                self.seen = {key: seen_at for key, seen_at in self.seen.items() if seen_at >= cutoff}
        '''
    )
    tests = _dedent(
        r'''
        import random
        import re
        import unittest
        from pathlib import Path

        from deep_tests.security_model import (
            BoundaryViolation,
            Principal,
            ReplayWindow,
            authorize_read,
            normalize_relative_path,
            redact,
            sign,
            validate_outbound_url,
        )


        class SecurityBoundaryTests(unittest.TestCase):
            def test_path_traversal_corpus_is_rejected(self) -> None:
                traversal = [
                    "../secret",
                    "safe/../secret",
                    "%2e%2e/secret",
                    "%252e%252e/secret",
                    "/absolute",
                    "safe\\windows",
                    "safe/%2e%2e/secret",
                    "safe//double",
                ]
                for value in traversal:
                    with self.subTest(value=value), self.assertRaises(BoundaryViolation):
                        normalize_relative_path(value)
                self.assertEqual(normalize_relative_path("safe/nested/file.txt"), "safe/nested/file.txt")

            def test_ssrf_boundary_requires_exact_https_allowlist(self) -> None:
                allowed = {"api.example.test"}
                self.assertEqual(
                    validate_outbound_url("https://api.example.test/v1", allowed),
                    "https://api.example.test/v1",
                )
                for value in (
                    "http://api.example.test/v1",
                    "https://api.example.test.attacker.invalid/v1",
                    "https://user@api.example.test/v1",
                    "https://127.0.0.1/v1",
                ):
                    with self.subTest(value=value), self.assertRaises(BoundaryViolation):
                        validate_outbound_url(value, allowed)

            def test_tenant_isolation_and_roles_fail_closed(self) -> None:
                authorize_read(Principal("tenant-a", frozenset({"reader"})), "tenant-a")
                with self.assertRaises(BoundaryViolation):
                    authorize_read(Principal("tenant-a", frozenset({"reader"})), "tenant-b")
                with self.assertRaises(BoundaryViolation):
                    authorize_read(Principal("tenant-a", frozenset({"writer"})), "tenant-a")

            def test_signature_tamper_replay_and_skew_are_rejected(self) -> None:
                secret = b"unit-test-secret"
                body = b"payload"
                now = 1_700_000_000
                signature = sign(secret, now, "nonce-1", body)
                window = ReplayWindow(max_skew_seconds=300)
                window.verify(secret, now, "nonce-1", body, signature, now=now)
                with self.assertRaises(BoundaryViolation):
                    window.verify(secret, now, "nonce-1", body, signature, now=now)
                with self.assertRaises(BoundaryViolation):
                    ReplayWindow().verify(secret, now, "nonce-2", b"tampered", signature, now=now)
                with self.assertRaises(BoundaryViolation):
                    ReplayWindow(max_skew_seconds=10).verify(
                        secret, now - 11, "nonce-3", body, sign(secret, now - 11, "nonce-3", body), now=now
                    )

            def test_redaction_removes_token_and_bearer_shapes(self) -> None:
                github_shape = "gh" + "p_" + "A" * 32
                linear_shape = "lin_" + "api_" + "B" * 32
                value = f"Authorization: Bearer opaque {github_shape} {linear_shape}"
                result = redact(value)
                self.assertNotIn(github_shape, result)
                self.assertNotIn(linear_shape, result)
                self.assertNotIn("opaque", result)

            def test_workflow_actions_are_immutable_and_permissions_are_read_only(self) -> None:
                workflow = Path(".github/workflows/deep-tests.yml").read_text()
                self.assertIn("permissions:\n  contents: read", workflow)
                self.assertNotIn("pull_request_target", workflow)
                uses = [line.split("uses:", 1)[1].strip() for line in workflow.splitlines() if "uses:" in line]
                self.assertGreaterEqual(len(uses), 2)
                for action in uses:
                    self.assertRegex(action, re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$"))


        if __name__ == "__main__":
            unittest.main()
        '''
    )
    return {
        "src/deep_tests/security_model.py": module,
        "tests/test_security_boundaries.py": tests,
    }


def _common_files(organization: str, spec: SuiteSpec, fleet: Fleet) -> dict[str, str]:
    primary = organization[: -len("-test")]
    metadata = {
        "schema_version": 1,
        "organization": organization,
        "primary_organization": primary,
        "repository": spec.name,
        "suite": spec.suite,
        "description": spec.description,
        "tracking_issue": fleet.tracking_issue,
        "bootstrap_operation": BOOTSTRAP_OPERATION,
        "bootstrap_date": fleet.bootstrap_date,
        "visibility": fleet.visibility,
        "required_paths": [
            ".github/workflows/deep-tests.yml",
            ".zpkg.toml",
            "AGENTS.md",
            "docs/test-strategy.md",
            "scripts/verify_repository.py",
            "src/deep_tests/__init__.py",
            "tests",
        ],
    }
    readme = _dedent(
        f'''
        # {organization}/{spec.name}

        {spec.description}

        This repository is the `{spec.suite}` deep-test suite for `{primary}`. It is intentionally dependency-light and deterministic so failures can be reproduced locally without production credentials or customer data.

        ## Run

        ```bash
        PYTHONPATH=src python -m unittest discover -s tests -v
        python scripts/verify_repository.py
        ```

        The initial model is executable rather than a placeholder. Product adapters should be added through focused pull requests while preserving the reference-model tests as an oracle.

        Tracking: {fleet.tracking_issue}
        '''
    )
    strategy = _dedent(
        f'''
        # Deep test strategy

        ## Scope

        Suite: `{spec.suite}`
        Test organization: `{organization}`
        Primary organization: `{primary}`

        ## Invariants

        - every randomized test uses an explicit deterministic seed;
        - retries, duplicates, migrations, and rejected inputs are observable assertions, not sleeps;
        - test data is synthetic and contains no production credentials or customer payloads;
        - the suite runs without network access by default;
        - a product adapter must preserve the reference model and publish the seed and minimized trace on failure;
        - scheduled CI is defense in depth; pull-request and main-branch checks remain authoritative.

        ## Expansion path

        1. Add a versioned adapter for the primary repository contract.
        2. Add sanitized golden fixtures owned by the canonical interface repository.
        3. Run the same trace against the reference model and implementation.
        4. Retain failing seeds as regression tests.
        5. Link behavior changes to the matching Linear issue and repository PR.
        '''
    )
    agents = _dedent(
        f'''
        # AGENTS.md

        Owner: `{organization}`
        Primary product context: `{primary}`
        Tracking: `{fleet.tracking_issue}`

        ## Semantic conflict resolution

        Never force-push shared history and never resolve a conflict by wholesale selection of `ours`, `theirs`, current, or incoming. Inspect the merge base, reread every affected file completely, and examine 3–10 relevant commits from both sides when available. Include the primary organization's contracts, schemas, migrations, fixtures, CI behavior, and related repositories where they materially constrain the result.

        Resolve conflicts semantically: preserve compatible intent from both sides, add or update regression tests for the reconciled behavior, scan the full tree for unresolved conflict markers, and rerun the complete deterministic suite before requesting merge. Fail closed when intent remains ambiguous.
        '''
    )
    workflow = _dedent(
        f'''
        name: Deep test suite

        on:
          pull_request:
          push:
            branches: [main]
          schedule:
            - cron: "23 9 * * 3"
          workflow_dispatch:

        permissions:
          contents: read

        concurrency:
          group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
          cancel-in-progress: true

        jobs:
          verify:
            runs-on: ubuntu-24.04
            timeout-minutes: 15
            steps:
              - uses: actions/checkout@{CHECKOUT_SHA}
                with:
                  persist-credentials: false
              - uses: actions/setup-python@{SETUP_PYTHON_SHA}
                with:
                  python-version: '3.12'
                  cache: ''
              - name: Compile
                run: python -m compileall -q src tests scripts
              - name: Verify repository contract
                run: python scripts/verify_repository.py
              - name: Run deterministic deep suite
                env:
                  PYTHONPATH: src
                  DEEP_TEST_SEEDS: '0-49'
                run: python -m unittest discover -s tests -v
        '''
    )
    verifier = _dedent(
        r'''
        #!/usr/bin/env python3
        from __future__ import annotations

        import json
        import re
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        metadata = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
        required = {
            "README.md",
            "AGENTS.md",
            "project.json",
            "pyproject.toml",
            ".zpkg.toml",
            "docs/test-strategy.md",
            "scripts/verify_repository.py",
            ".github/workflows/deep-tests.yml",
            "src/deep_tests/__init__.py",
        }
        missing = sorted(path for path in required if not (ROOT / path).exists())
        if missing:
            raise SystemExit(f"missing required paths: {missing}")
        if not (ROOT / "tests").is_dir() or not list((ROOT / "tests").glob("test_*.py")):
            raise SystemExit("at least one executable test module is required")

        marker = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
        credential = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY")
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if marker.search(text):
                raise SystemExit(f"unresolved conflict marker: {path.relative_to(ROOT)}")
            if credential.search(text):
                raise SystemExit(f"credential-shaped content: {path.relative_to(ROOT)}")

        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for phrase in ("merge base", "3–10 relevant commits", "ours", "theirs", "Fail closed"):
            if phrase not in agents:
                raise SystemExit(f"semantic conflict policy missing phrase: {phrase}")

        workflow = (ROOT / ".github/workflows/deep-tests.yml").read_text(encoding="utf-8")
        if "permissions:\n  contents: read" not in workflow or "pull_request_target" in workflow:
            raise SystemExit("workflow permission boundary is unsafe")
        action_pattern = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
        actions = [line.split("uses:", 1)[1].strip() for line in workflow.splitlines() if "uses:" in line]
        if len(actions) < 2 or any(not action_pattern.fullmatch(action) for action in actions):
            raise SystemExit(f"workflow actions are not immutably pinned: {actions}")

        if metadata.get("bootstrap_operation") != "deep-test-fleet-20260808":
            raise SystemExit("bootstrap operation identity drift")
        if not str(metadata.get("organization", "")).endswith("-test"):
            raise SystemExit("repository is not bound to a test organization")
        print(f"validated {metadata['organization']}/{metadata['repository']} suite={metadata['suite']}")
        '''
    )
    pyproject = _dedent(
        f'''
        [project]
        name = "{organization}-{spec.name}"
        version = "0.1.0"
        description = "{spec.description}"
        requires-python = ">=3.11"
        dependencies = []

        [tool.deep-tests]
        suite = "{spec.suite}"
        deterministic = true
        network_default = "disabled"
        '''
    )
    zpkg = _dedent(
        f'''
        [package]
        name = "{organization}/{spec.name}"
        version = "0.1.0"
        type = "test-suite"

        [develop]
        commands = [
          "PYTHONPATH=src python -m unittest discover -s tests -v",
          "python scripts/verify_repository.py"
        ]
        '''
    )
    return {
        "README.md": readme,
        "AGENTS.md": agents,
        "SECURITY.md": _dedent(
            """
            # Security

            Use synthetic data only. Do not commit credentials, customer payloads, production snapshots, private endpoints, or presigned URLs. Report vulnerabilities through the owning organization's private security channel rather than a public fixture or issue.
            """
        ),
        "project.json": json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        "pyproject.toml": pyproject,
        ".zpkg.toml": zpkg,
        ".gitignore": ".env\n.env.*\n!.env.example\n__pycache__/\n*.py[cod]\n.coverage\nhtmlcov/\n.venv/\n",
        "docs/test-strategy.md": strategy,
        "scripts/verify_repository.py": verifier,
        ".github/workflows/deep-tests.yml": workflow,
        "src/deep_tests/__init__.py": '"""Deterministic deep-test reference models."""\n',
    }


def generate_repository_files(organization: str, spec: SuiteSpec, fleet: Fleet) -> dict[str, str]:
    if organization not in fleet.organizations:
        raise ValueError(f"organization is not allowlisted: {organization}")
    files = _common_files(organization, spec, fleet)
    specialized = {
        "contract": _contract_files,
        "chaos": _chaos_files,
        "upgrade": _upgrade_files,
        "security": _security_files,
    }[spec.suite]()
    overlap = set(files) & set(specialized)
    if overlap:
        raise ValueError(f"template path collision: {sorted(overlap)}")
    files.update(specialized)
    validate_generated_files(files, organization, spec)
    return dict(sorted(files.items()))


def validate_generated_files(files: dict[str, str], organization: str, spec: SuiteSpec) -> None:
    if len(files) < 12:
        raise ValueError(f"generated repository is too shallow: {organization}/{spec.name}")
    markers = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
    credentials = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY")
    for path, content in files.items():
        if path.startswith("/") or ".." in Path(path).parts or not content.endswith("\n"):
            raise ValueError(f"unsafe generated path/content: {path}")
        if markers.search(content):
            raise ValueError(f"conflict marker in generated file: {path}")
        if credentials.search(content):
            raise ValueError(f"credential-shaped generated content: {path}")
    workflow = files[".github/workflows/deep-tests.yml"]
    actions = [line.split("uses:", 1)[1].strip() for line in workflow.splitlines() if "uses:" in line]
    if len(actions) != 2 or any(not PINNED_ACTION.fullmatch(action) for action in actions):
        raise ValueError(f"unpinned workflow action: {actions}")
    if "permissions:\n  contents: read" not in workflow or "pull_request_target" in workflow:
        raise ValueError("unsafe workflow permission/event boundary")


def materialize_repository(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        if relative.startswith("scripts/"):
            destination.chmod(0o755)


def run_generated_suite(organization: str, spec: SuiteSpec, fleet: Fleet) -> None:
    files = generate_repository_files(organization, spec, fleet)
    with tempfile.TemporaryDirectory(prefix=f"deep-{spec.suite}-") as temporary:
        root = Path(temporary)
        materialize_repository(root, files)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(root / "src")
        subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"],
            cwd=root,
            env=environment,
            check=True,
            timeout=30,
        )
        subprocess.run(
            [sys.executable, "scripts/verify_repository.py"],
            cwd=root,
            env=environment,
            check=True,
            timeout=30,
        )
        subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=root,
            env=environment,
            check=True,
            timeout=60,
        )


def validate_all_templates(fleet: Fleet) -> None:
    representative = fleet.organizations[0]
    for spec in fleet.repositories:
        run_generated_suite(representative, spec, fleet)
    for organization in fleet.organizations:
        for spec in fleet.repositories:
            generate_repository_files(organization, spec, fleet)
