#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "scripts/cargo-canonical-format-shim.sh"

with tempfile.TemporaryDirectory(prefix="cargo-format-shim-") as directory:
    root = Path(directory)
    log = root / "calls.log"
    fake = root / "cargo-real"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\0' \"$@\" >> {log!s}\n"
        f"printf '\\n' >> {log!s}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    env = os.environ.copy()
    env["REAL_CARGO"] = str(fake)
    subprocess.run(
        [
            "bash",
            str(SHIM),
            "+1.88.0",
            "fmt",
            "--manifest-path",
            "/tmp/example/Cargo.toml",
            "--all",
            "--",
            "--check",
        ],
        check=True,
        env=env,
    )
    raw = log.read_bytes().splitlines()
    if len(raw) != 2:
        raise SystemExit(f"expected two cargo calls, observed {len(raw)}")
    calls = [[part.decode() for part in line.split(b"\0") if part] for line in raw]
    expected_format = [
        "+1.88.0",
        "fmt",
        "--manifest-path",
        "/tmp/example/Cargo.toml",
        "--all",
    ]
    expected_check = expected_format + ["--", "--check"]
    if calls != [expected_format, expected_check]:
        raise SystemExit(f"unexpected canonical/check calls: {calls!r}")

    log.unlink()
    subprocess.run(
        ["bash", str(SHIM), "+1.88.0", "test", "--locked"],
        check=True,
        env=env,
    )
    passthrough = [
        part.decode()
        for part in log.read_bytes().rstrip(b"\n").split(b"\0")
        if part
    ]
    if passthrough != ["+1.88.0", "test", "--locked"]:
        raise SystemExit(f"non-format command was not passed through exactly: {passthrough!r}")

source = SHIM.read_text(encoding="utf-8")
for forbidden in ["--force", "git reset", "git rebase", "|| true"]:
    if forbidden in source:
        raise SystemExit(f"forbidden behavior in cargo shim: {forbidden}")

print("cargo canonical-format shim validated")
