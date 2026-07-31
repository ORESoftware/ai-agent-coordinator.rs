#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contract import validate_operations, validate_policy
from io_helpers import digest, load_json, write_json
from templates import dart, rust, typescript


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def generate(base: Path) -> dict[str, object]:
    operations_doc = load_json(base / "contract/operations.json")
    policy_doc = load_json(base / "contract/client-policy.json")
    operations = validate_operations(operations_doc)
    validate_policy(policy_doc)
    contract_digest = digest({"operations": operations_doc, "policy": policy_doc})
    sources = {
        "rust": (base / "clients/rust/src/generated.rs", rust(operations, contract_digest)),
        "dart": (base / "clients/dart/lib/src/generated.dart", dart(operations, contract_digest)),
        "typescript": (base / "clients/typescript/src/generated.ts", typescript(operations, contract_digest)),
    }
    output_digests: dict[str, str] = {}
    for language, (path, content) in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        output_digests[language] = digest(content)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "generator": "scripts/generate_clients.py",
        "contract_digest": contract_digest,
        "operation_count": len(operations),
        "outputs": output_digests,
    }
    write_json(base / "generated/manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root())
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv or sys.argv[1:])
    manifest = generate(args.root)
    print(json.dumps(manifest, indent=None if args.compact else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
