#!/usr/bin/env python3
"""Build the reviewed, exact-blob Zed v0.2.3 fleet migration plan.

The input is an authenticated default-branch inventory captured before any
mutation.  This generator is deliberately network-free: it accepts only the
downloaded blob bytes and repository inventory, rewrites known contract drift,
validates every changed result with the caller-provided released ``zed``
binary, and emits immutable before/after Git blob identities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

OPERATION = "zed-manifest-v023-fleet-migration-2026-08-14"
TRACKING_ISSUE = "DEN-3733"
INTERFACE_REVISION = "8428bc574111fa148e590c8350c7855035ce2046"
CLI_TAG = "v0.2.3"
CLI_COMMIT = "9dae597bcf22970e97b90c5ea336db19a9f02255"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
LEGACY_E2E_IDENTITIES = {
    "apostille-me/apme-e2e": "Apostille.me",
    "embedded-alerts/eal-e2e": "Embedded Alerts",
    "evento-globolo/evgl-e2e": "Evento Globolo",
    "hacker-house-medellin/hhm-e2e": "Hacker House Medellin",
}
LIB_COORDINATES = {
    "apostille-me/apme-lib": "apostille-me/apme-libs",
    "embedded-alerts/eal-lib": "embedded-alerts/eal-libs",
    "evento-globolo/evgl-lib": "evento-globolo/evgl-libs",
    "hacker-house-medellin/hhm-lib": "hacker-house-medellin/hhm-libs",
}
DEPENDENCY_COORDINATES = {
    "3FA-app/3fa-clients": "threefa/threefa-clients",
    "3FA-app/3fa-interfaces": "threefa/threefa-interfaces",
    "StreemPilot/streempilot-clients": "streempilot/streempilot-clients",
    "declarative-migrations/declarative-postgres-migrate.rs": (
        "declarative-migrations/declarative-postgres-migrate"
    ),
    "opto-sync/syncer.c": "opto-sync/syncer-c",
    "opto-sync/syncer.rs": "opto-sync/syncer-rs",
    "opto-sync/syncer": "opto-sync/syncer-packages",
    "quaestor/quaestor-clients": "quaestor-ledger/quaestor-clients",
    "streempilot/sp-sync": "streempilot/streempilot-sync",
}
ALREADY_RECONCILED = {
    ("opto-sync-test/contract-conformance-tests", ".zpkg.toml"): {
        "snapshot_blob": "4e0489fb54a1af197cc69d3aafc45fa324e81371",
        "current_blob": "d6b62cdca75610f87c2528f81f65505fc7768dbb",
        "merge_commit": "58ecba6d48b3f776d613353f61d83bea111c9949",
        "pull_request": "https://github.com/opto-sync-test/contract-conformance-tests/pull/5",
    }
}
IMMUTABLE_SNAPSHOTS = {
    ("3fa-app-test/clients-consumer-matrix", "proof/den-2612/source/.zpkg.toml"): {
        "snapshot_blob": "1a05a2c6850a375eb5720ff6bf23883b0a5fb63d",
        "proof_source_blob": "9fb75f27a614b3a15dd6fe23a6f72fbf0a28e5ba",
        "reason": (
            "DEN-2612 preserves byte-identical source evidence from 3FA-app/3fa-clients "
            "PR 37; proof-source.json and verify_snapshot.py require this historical blob"
        ),
    }
}


class PlanError(RuntimeError):
    """The input snapshot or a proposed rewrite violated a reviewed invariant."""


@dataclass(frozen=True)
class Instance:
    repository: str
    default_branch: str
    path: str
    blob_sha: str
    size: int


@dataclass(frozen=True)
class Repository:
    full_name: str
    default_branch: str
    archived: bool
    fork: bool
    private: bool


def git_blob_sha(content: str) -> str:
    raw = content.encode("utf-8")
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()  # noqa: S324


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PlanError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def parse_bool(value: str, field: str) -> bool:
    if value not in {"true", "false"}:
        raise PlanError(f"{field} must be true or false")
    return value == "true"


def load_instances(path: Path) -> list[Instance]:
    instances: list[Instance] = []
    seen: set[tuple[str, str]] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 6 or fields[0] != "MANIFEST":
            raise PlanError(f"malformed manifest scan line {number}")
        _, repository, branch, manifest_path, blob_sha, size = fields
        if (
            not REPOSITORY_PATTERN.fullmatch(repository)
            or ".." in repository
            or not SHA_PATTERN.fullmatch(blob_sha)
        ):
            raise PlanError(f"malformed repository or blob on scan line {number}")
        key = (repository.lower(), safe_path(manifest_path))
        if key in seen:
            raise PlanError(f"duplicate manifest instance: {repository}:{manifest_path}")
        seen.add(key)
        instances.append(Instance(repository, branch, manifest_path, blob_sha, int(size)))
    if not instances:
        raise PlanError("manifest scan is empty")
    return instances


def load_repositories(path: Path) -> dict[str, Repository]:
    repositories: dict[str, Repository] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 7:
            raise PlanError(f"malformed repository inventory line {number}")
        full_name, default_branch, archived, fork, private, _pushed_at, _size = fields
        if not REPOSITORY_PATTERN.fullmatch(full_name) or ".." in full_name:
            raise PlanError(f"malformed repository name on inventory line {number}")
        key = full_name.lower()
        if key in repositories:
            raise PlanError(f"duplicate repository inventory entry: {full_name}")
        repositories[key] = Repository(
            full_name=full_name,
            default_branch=default_branch,
            archived=parse_bool(archived, "archived"),
            fork=parse_bool(fork, "fork"),
            private=parse_bool(private, "private"),
        )
    return repositories


def section_bounds(lines: list[str], header: str) -> tuple[int, int] | None:
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        return None
    end = next(
        (index for index in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[index])),
        len(lines),
    )
    return start, end


def replace_package_field(text: str, field: str, value: str) -> str:
    lines = text.splitlines()
    bounds = section_bounds(lines, "[package]")
    if bounds is None:
        raise PlanError("manifest has no [package] table")
    start, end = bounds
    for index in range(start + 1, end):
        if re.match(rf"^\s*{re.escape(field)}\s*=", lines[index]):
            lines[index] = f'{field} = {json.dumps(value, ensure_ascii=False)}'
            return "\n".join(lines).rstrip() + "\n"
    raise PlanError(f"manifest package has no {field} field")


def remove_section(text: str, header: str) -> str:
    lines = text.splitlines()
    bounds = section_bounds(lines, header)
    if bounds is None:
        return text
    start, end = bounds
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "\n".join(lines[:start] + lines[end:]).rstrip() + "\n"


def collapse_scripts(text: str, recipes: set[str]) -> str:
    data = tomllib.loads(text)
    scripts = data.get("scripts")
    if not scripts:
        return text
    if not isinstance(scripts, dict) or not all(isinstance(value, str) for value in scripts.values()):
        raise PlanError("scripts table must contain only string commands")
    if list(scripts) == ["test"]:
        return text
    command = " && ".join(scripts.values())
    lines = text.splitlines()
    bounds = section_bounds(lines, "[scripts]")
    if bounds is None:
        raise AssertionError("parsed scripts table has no textual section")
    start, end = bounds
    assignments = [
        index
        for index in range(start + 1, end)
        if re.match(r"^\s*[A-Za-z0-9_-]+\s*=", lines[index])
    ]
    trailing = lines[max(assignments) + 1 : end] if assignments else lines[start + 1 : end]
    lines[start:end] = ["[scripts]", f"test = {json.dumps(command, ensure_ascii=False)}", *trailing]
    recipes.add("collapse-scripts-to-test")
    return "\n".join(lines).rstrip() + "\n"


def migrate_develop(text: str, recipes: set[str]) -> str:
    data = tomllib.loads(text)
    develop = data.get("develop")
    if not develop:
        return text
    commands = develop.get("commands") if isinstance(develop, dict) else None
    if not isinstance(commands, list) or not commands or not all(isinstance(value, str) for value in commands):
        raise PlanError("legacy develop table is not the reviewed commands shape")
    replacement = "[scripts]\ntest = " + json.dumps(" && ".join(commands), ensure_ascii=False) + "\n"
    migrated, count = re.subn(r"(?ms)^\[develop\]\n.*?(?=^\[|\Z)", replacement, text)
    if count != 1:
        raise PlanError("legacy develop table could not be replaced exactly")
    recipes.add("develop-commands-to-scripts-test")
    return migrated.rstrip() + "\n"


def canonicalize_root_targets(text: str, recipes: set[str]) -> str:
    lines = text.splitlines()
    index = 0
    changed = False
    while index < len(lines):
        if not re.match(r"^\[targets\.[^]]+\]$", lines[index].strip()):
            index += 1
            continue
        end = next(
            (cursor for cursor in range(index + 1, len(lines)) if re.match(r"^\s*\[", lines[cursor])),
            len(lines),
        )
        if any(re.match(r'^\s*dir\s*=\s*"\."\s*$', lines[cursor]) for cursor in range(index + 1, end)):
            kept: list[str] = []
            for cursor, line in enumerate(lines):
                if index < cursor < end and re.match(r"^\s*name\s*=", line):
                    changed = True
                    continue
                kept.append(line)
            lines = kept
        index = end
    if changed:
        recipes.add("canonical-root-target-name")
    return "\n".join(lines).rstrip() + "\n"


def canonical_e2e(identity: str) -> str:
    org, name = identity.split("/", 1)
    label = LEGACY_E2E_IDENTITIES[identity]
    prefix = {"apostille-me": "apme", "embedded-alerts": "eal", "evento-globolo": "evgl", "hacker-house-medellin": "hhm"}[org]
    dependencies = [f"{org}/{prefix}-{suffix}" for suffix in ("clients", "interfaces", "libs", "cli")]
    return (
        f'[package]\norg = "{org}"\nname = "{name}"\nversion = "0.1.0"\n'
        f'description = "End-to-end integration package for {label}"\n'
        'license = "MIT"\nlanguage = "polyglot"\n\n'
        f'[package.repository]\nvcs = "git"\nurl = "https://github.com/{identity}"\n\n'
        '[install]\ndir = ".vendor/.zed"\n\n[dependencies]\n'
        + "".join(f'"{dependency}" = "^0.1.0"\n' for dependency in dependencies)
    )


def canonical_cliptown_lib_core() -> str:
    return '''[package]
org = "cliptown"
name = "cliptown-lib-core"
version = "0.1.0"
description = "Transport-neutral ClipTown core library"
license = "MIT"
language = "rust"

[package.repository]
vcs = "git"
url = "https://github.com/cliptown/cliptown-lib-core"

[install]
dir = ".vendor/.zed"

[dependencies]
"cliptown/cliptown-interfaces" = "^0.1.0"

[publish]
include_readme = true
tag_format = "v{version}"
smoke_test = "cargo test --manifest-path \\"$ZED_PKG_TEST_TARGET/Cargo.toml\\" --locked --all-targets"
exclude = [".env", ".env.*", ".direnv/**", ".zed/**", ".zed-pack/**", ".vendor/.zed/**", "target/**", "**/target/**", "**/node_modules/**", "tmp/**", "**/*.log", ".DS_Store"]

[publish.native]
registry = "crates-io"
package = "cliptown-lib-core"

[scripts]
test = "cargo test --locked --all-targets"
'''


def canonical_vscode_fixture() -> str:
    return '''[package]
org = "zed-pkg"
name = "zed-vscode-extension-host-fixture"
version = "0.0.0"
description = "Extension-host integration fixture for zed-vscode"
license = "MIT"
language = "javascript"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg/zed-vscode"

[install]
dir = ".vendor/.zed"
'''


def transform(source: str) -> tuple[str, list[str]]:
    recipes: set[str] = set()
    data = tomllib.loads(source)
    package = data.get("package")
    if not isinstance(package, dict) or not isinstance(package.get("name"), str):
        raise PlanError("manifest package identity is missing")
    combined_identity = package["name"]
    if combined_identity in LEGACY_E2E_IDENTITIES and "org" not in package:
        source = canonical_e2e(combined_identity)
        recipes.add("split-legacy-e2e-identity")
    elif combined_identity == "cliptown/cliptown-lib-core" and "org" not in package:
        source = canonical_cliptown_lib_core()
        recipes.add("migrate-legacy-cliptown-manifest")
    elif data.get("version") == 1 and combined_identity == "extension-host-fixture":
        source = canonical_vscode_fixture()
        recipes.add("update-zed-vscode-fixture")

    source = migrate_develop(source, recipes)
    parsed = tomllib.loads(source)
    package = parsed["package"]
    name = package["name"]
    org = package.get("org")
    if name.endswith(".rs"):
        source = replace_package_field(source, "name", name[:-3])
        recipes.add("slug-rust-package-name")
    elif name.endswith(".github.io"):
        source = replace_package_field(source, "name", name.replace(".github.io", "-github-io"))
        recipes.add("slug-pages-package-name")
    if org == "ORESoftware":
        source = replace_package_field(source, "org", "oresoftware")
        recipes.add("lowercase-package-org")
    elif org == "3FA-app":
        source = replace_package_field(source, "org", "threefa")
        recipes.add("canonical-threefa-package-org")
    elif org == "fiducia":
        source = replace_package_field(source, "org", "fiducia-cloud")
        recipes.add("canonical-fiducia-package-org")
    elif org == "quaestor":
        source = replace_package_field(source, "org", "quaestor-ledger")
        recipes.add("canonical-quaestor-package-org")

    # Correct dependency table keys without rewriting native package names.
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r'^("([^"]+)"\s*=\s*)"([^"]+)"(.*)$', line)
        if not match:
            continue
        key = match.group(2)
        replacement = DEPENDENCY_COORDINATES.get(key)
        if key.startswith("fiducia/"):
            replacement = "fiducia-cloud/" + key.removeprefix("fiducia/")
        if key == "oresoftware/flags-2-env":
            replacement = "flags-2-env/flags-2-env"
        if replacement:
            requirement = "^0.3.0" if replacement == "flags-2-env/flags-2-env" else match.group(3)
            lines[index] = f'"{replacement}" = "{requirement}"{match.group(4)}'
            recipes.add("canonical-dependency-coordinate")
    source = "\n".join(lines).rstrip() + "\n"
    source = source.replace("oresoftware/flags-2-env", "flags-2-env/flags-2-env")

    # Remove only a duplicate singular lib edge when the real plural package is
    # already declared by the same manifest.
    for singular, plural in LIB_COORDINATES.items():
        if f'"{plural}"' not in source:
            continue
        source, count = re.subn(
            rf'(?m)^"{re.escape(singular)}"\s*=\s*"[^"]+"\n', "", source
        )
        if count:
            recipes.add("remove-duplicate-nonexistent-lib-coordinate")

    parsed = tomllib.loads(source)
    coordinate = f"{parsed['package'].get('org')}/{parsed['package']['name']}"
    if coordinate == "cliptown/cliptown-clients":
        source = source.replace(
            'name = "@cliptown/client"', 'name = "cliptown-clients-nodejs"'
        )
        needle = 'adapter = "node"\n\n[targets.golang]'
        if needle in source and "[targets.nodejs.native]" not in source:
            source = source.replace(
                needle,
                'adapter = "node"\n\n[targets.nodejs.native]\nregistry = "npm"\n'
                'package = "@cliptown/client"\n\n[targets.golang]',
            )
        recipes.add("separate-zed-and-npm-target-identities")
    elif coordinate == "scintilla-run/scintilla-clients":
        for header in ("[targets.deno]", "[targets.bun]", "[targets.edge]"):
            source = remove_section(source, header)
        source = source.replace(
            "# The TypeScript implementation is standards-based and self-contained. Each\n"
            "# runtime target publishes the same root so its runtime metadata and the shared\n"
            "# source remain inside the re-rooted Zed package.",
            "# The TypeScript implementation is standards-based and self-contained. One\n"
            "# canonical Node.js target owns the shared Node.js, Deno, Bun, and edge exports.",
        )
        recipes.add("isolate-scintilla-target-roots")
    elif coordinate == "shared-auth/shared-auth-clients":
        for header in (
            "[targets.gleamlang]",
            "[targets.deno]",
            "[targets.bun]",
            "[targets.edge]",
            "[targets.typescript-nodejs]",
        ):
            source = remove_section(source, header)
        source = source.replace(
            'dir = "clients/python"\nname = "shared-auth-clients-python3"',
            'dir = "clients/python3"\nname = "shared-auth-clients-python3"',
        )
        source = source.replace(
            "# Compatibility target IDs retained for existing consumers and validation.",
            "# Compatibility target IDs retain distinct source roots; the canonical Node.js\n"
            "# target owns the shared Node.js, Deno, Bun, and edge runtime entrypoints.",
        )
        needle = (
            '[targets.nodejs]\ndir = "clients/ts"\n'
            'name = "shared-auth-clients-nodejs"\nadapter = "node"'
        )
        if needle in source and "[targets.nodejs.native]" not in source:
            source = source.replace(
                needle,
                needle
                + '\n\n[targets.nodejs.native]\nregistry = "npm"\n'
                + 'package = "@shared-auth/client"',
            )
        recipes.add("isolate-shared-auth-target-roots")
    elif coordinate == "fiducia-cloud/fiducia-clients":
        for header in ("[targets.deno]", "[targets.bun]", "[targets.edge]"):
            source = remove_section(source, header)
        source = source.replace(
            "# TypeScript is one runtime-neutral Fetch API implementation. All four Zed\n"
            "# runtime targets intentionally publish the same self-contained source root.",
            "# TypeScript is one runtime-neutral Fetch API implementation. The canonical\n"
            "# Node.js target owns its Node.js, Deno, Bun, and edge runtime entrypoints.",
        )
        recipes.add("isolate-fiducia-target-roots")

    if 'adapter = "cargo"' in source:
        source = source.replace('adapter = "cargo"', 'adapter = "rust"')
        recipes.add("canonical-rust-adapter")
    source = collapse_scripts(source, recipes)
    source = canonicalize_root_targets(source, recipes)
    return source, sorted(recipes)


def validate_with_zed(binary: Path, manifest: Path) -> None:
    completed = subprocess.run(
        [str(binary), "validate", "--manifest", str(manifest), "--json"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if completed.returncode:
        detail = (completed.stdout or completed.stderr).strip()[:1000]
        raise PlanError(f"zed rejected {manifest.name}: {detail}")
    report = json.loads(completed.stdout)
    if report.get("valid") is not True or report.get("interface_revision") != INTERFACE_REVISION:
        raise PlanError(f"zed returned unexpected validation provenance for {manifest.name}")


def phase_for(instance: Instance) -> int:
    if instance.repository.endswith("/.github"):
        return 1
    if any(part in instance.path for part in ("repository-blueprints/", "repository-seeds/")):
        return 1
    if instance.repository in {"canonical-cloud/canonical.cloud"} and instance.path != ".zpkg.toml":
        return 1
    return 2


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    instances = load_instances(args.scan)
    repositories = load_repositories(args.repositories)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any(args.output_dir.iterdir()):
        raise PlanError(f"proposal directory must be empty: {args.output_dir}")

    proposals: dict[str, dict[str, Any]] = {}
    mutations: list[dict[str, Any]] = []
    reconciled: list[dict[str, Any]] = []
    immutable_snapshots: list[dict[str, Any]] = []
    for instance in instances:
        immutable = IMMUTABLE_SNAPSHOTS.get((instance.repository, instance.path))
        if immutable:
            if immutable["snapshot_blob"] != instance.blob_sha:
                raise PlanError(
                    f"immutable snapshot provenance drift: {instance.repository}:{instance.path}"
                )
            immutable_snapshots.append(
                {"repository": instance.repository, "path": instance.path, **immutable}
            )
            continue
        already = ALREADY_RECONCILED.get((instance.repository, instance.path))
        if already and already["snapshot_blob"] == instance.blob_sha:
            reconciled.append({"repository": instance.repository, "path": instance.path, **already})
            continue
        repository = repositories.get(instance.repository.lower())
        if repository is None:
            raise PlanError(f"repository inventory is missing {instance.repository}")
        if repository.archived:
            raise PlanError(f"migration target is archived: {repository.full_name}")
        if repository.default_branch != instance.default_branch:
            raise PlanError(f"default branch drift in snapshot: {instance.repository}")
        source_path = args.source_dir / f"{instance.blob_sha}.toml"
        source = source_path.read_text(encoding="utf-8")
        if len(source.encode("utf-8")) != instance.size or git_blob_sha(source) != instance.blob_sha:
            raise PlanError(f"downloaded blob identity mismatch: {instance.blob_sha}")
        proposed, recipes = transform(source)
        if proposed == source:
            continue
        proposed_sha = git_blob_sha(proposed)
        proposal_path = args.output_dir / f"{instance.blob_sha}.toml"
        existing = proposals.get(instance.blob_sha)
        proposal = {
            "source_blob": instance.blob_sha,
            "proposed_blob": proposed_sha,
            "proposed_sha256": sha256(proposed),
            "file": str(proposal_path.relative_to(args.plan.parent)),
            "recipes": recipes,
        }
        if existing is not None and existing != proposal:
            raise PlanError(f"one source blob produced divergent proposals: {instance.blob_sha}")
        if existing is None:
            proposal_path.write_text(proposed, encoding="utf-8")
            validate_with_zed(args.zed, proposal_path)
            proposals[instance.blob_sha] = proposal
        mutations.append(
            {
                "repository": repository.full_name,
                "default_branch": repository.default_branch,
                "private": repository.private,
                "fork": repository.fork,
                "path": instance.path,
                "source_blob": instance.blob_sha,
                "proposed_blob": proposed_sha,
                "proposal": proposal["file"],
                "phase": phase_for(instance),
                "recipes": recipes,
            }
        )

    mutations.sort(key=lambda item: (item["phase"], item["repository"].lower(), item["path"]))
    by_repo: dict[str, set[int]] = {}
    for mutation in mutations:
        by_repo.setdefault(mutation["repository"].lower(), set()).add(mutation["phase"])
    if any(len(phases) != 1 for phases in by_repo.values()):
        raise PlanError("one repository cannot span migration phases")
    return {
        "schema_version": 1,
        "operation": OPERATION,
        "tracking_issue": TRACKING_ISSUE,
        "generated_at": "2026-08-14T18:54:00Z",
        "authenticated_actor": {"login": "ORESoftware", "id": 11139560},
        "zed": {
            "cli_tag": CLI_TAG,
            "cli_commit": CLI_COMMIT,
            "interface_revision": INTERFACE_REVISION,
        },
        "snapshot": {
            "manifest_instances": len(instances),
            "repositories": len({item.repository.lower() for item in instances}),
            "changed_unique_blobs": len(proposals),
            "mutation_instances": len(mutations),
            "mutation_repositories": len(by_repo),
        },
        "immutable_snapshots": immutable_snapshots,
        "already_reconciled": reconciled,
        "proposals": [proposals[key] for key in sorted(proposals)],
        "mutations": mutations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--repositories", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--zed", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.zed.is_file():
        raise PlanError(f"zed binary is missing: {args.zed}")
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    plan = build_plan(args)
    temporary = args.plan.with_suffix(args.plan.suffix + ".tmp")
    temporary.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.plan)
    print(json.dumps(plan["snapshot"], sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError, PlanError) as error:
        print(f"fatal plan error: {error}", file=__import__("sys").stderr)
        raise SystemExit(1) from None
