from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .contract import (
    CONFLICT_MARKER,
    CONTROL_CHARS,
    EXPECTED_REPOSITORIES,
    SECRET_PATTERNS,
    BootstrapError,
    generic_verify_script,
    manifest_digest,
    repository_contract,
    common_agents,
    common_readme,
    common_security,
    validate_manifest,
    zpkg_manifest,
)
from .scaffolds import repo_specific_files

def seed_files(manifest: dict[str, Any], repo_name: str) -> dict[str, str]:
    validate_manifest(manifest)
    digest = manifest_digest(manifest)
    repo = next((item for item in manifest["repositories"] if item["name"] == repo_name), None)
    if repo is None:
        raise BootstrapError(f"repository is not in the sealed manifest: {repo_name}")
    files = {
        "README.md": common_readme(manifest, repo, digest),
        "AGENTS.md": common_agents(repo),
        "SECURITY.md": common_security(),
        ".editorconfig": "root = true\n\n[*]\ncharset = utf-8\nend_of_line = lf\ninsert_final_newline = true\nindent_style = space\nindent_size = 2\ntrim_trailing_whitespace = true\n",
        ".gitignore": ".env\n.env.*\n!.env.example\n.vendor/\nnode_modules/\ntarget/\nbuild/\ndist/\n.dart_tool/\n.flutter-plugins*\n*.log\n",
        ".zpkg.toml": zpkg_manifest(manifest, repo),
        "repository.contract.json": repository_contract(manifest, repo, digest),
        "scripts/verify.py": generic_verify_script(),
    }
    files.update(repo_specific_files(manifest, repo))
    return dict(sorted(files.items()))


def validate_seed_files(manifest: dict[str, Any], repo_name: str, files: dict[str, str]) -> None:
    required = {"README.md", "AGENTS.md", "SECURITY.md", ".zpkg.toml", "repository.contract.json", "scripts/verify.py"}
    missing = required - set(files)
    if missing:
        raise BootstrapError(f"{repo_name} scaffold misses: {sorted(missing)}")
    for path, text in files.items():
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise BootstrapError(f"unsafe scaffold path: {path}")
        if CONTROL_CHARS.search(text):
            raise BootstrapError(f"control character in {repo_name}/{path}")
        if CONFLICT_MARKER.search(text):
            raise BootstrapError(f"conflict marker in {repo_name}/{path}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise BootstrapError(f"credential-shaped content in {repo_name}/{path}")
        if path.endswith(".json"):
            json.loads(text)
        if path.endswith(".toml"):
            tomllib.loads(text)
    contract = json.loads(files["repository.contract.json"])
    if contract["repository"] != repo_name:
        raise BootstrapError("repository contract identity mismatch")
    if contract["manifest_digest"] != manifest_digest(manifest):
        raise BootstrapError("repository contract manifest digest mismatch")
    if repo_name == "hhaus-clients":
        expected = {f"clients/{language}/README.md" for language in manifest["language_targets"]}
        missing_targets = expected - set(files)
        if missing_targets:
            raise BootstrapError(f"client targets missing: {sorted(missing_targets)}")
    if repo_name == "hhaus-interfaces":
        expected = {f"generated/{language}/README.md" for language in manifest["language_targets"]}
        missing_targets = expected - set(files)
        if missing_targets:
            raise BootstrapError(f"interface targets missing: {sorted(missing_targets)}")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        verify_namespace: dict[str, Any] = {
            "__name__": "not-main",
            "__file__": str(root / "scripts/verify.py"),
        }
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(
                    compile(files["scripts/verify.py"], "verify.py", "exec"),
                    verify_namespace,
                )
                verify_namespace["main"]()
        except SystemExit as error:
            raise BootstrapError(f"{repo_name} generated verifier failed: {error}") from None

        if repo_name == "hhaus-interfaces":
            peer_namespace: dict[str, Any] = {
                "__name__": "not-main",
                "__file__": str(root / "scripts/check_peer_authorities.py"),
            }
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(
                        compile(
                            files["scripts/check_peer_authorities.py"],
                            "check_peer_authorities.py",
                            "exec",
                        ),
                        peer_namespace,
                    )
                    peer_namespace["main"]()
            except SystemExit as error:
                raise BootstrapError(f"interface peer-authority validation failed: {error}") from None


def render_all(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    validate_manifest(manifest)
    records = []
    for name in EXPECTED_REPOSITORIES:
        files = seed_files(manifest, name)
        validate_seed_files(manifest, name, files)
        repo_root = root / name
        for path, text in files.items():
            target = repo_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        records.append(
            {
                "repository": name,
                "files": len(files),
                "content_digest": hashlib.sha256(
                    b"".join(path.encode() + b"\0" + files[path].encode() + b"\0" for path in sorted(files))
                ).hexdigest(),
            }
        )
    result = {"manifest_digest": manifest_digest(manifest), "repositories": records}
    (root / "render-ledger.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
