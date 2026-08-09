from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_artifact_recovery_wave6 import ValidationError, validate

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data/artifact-recovery-wave6.json"


class Wave6RecoveryValidationTests(unittest.TestCase):
    maxDiff = None

    def clone_fixture(self) -> tuple[Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / "wave6"
        for source in ROOT.rglob("*"):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(ROOT)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        ledger = json.loads((target / "data/artifact-recovery-wave6.json").read_text(encoding="utf-8"))
        return target, ledger

    @staticmethod
    def write_ledger(root: Path, ledger: dict) -> Path:
        path = root / "data/artifact-recovery-wave6.json"
        path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def rewrite_safe_payload(root: Path, ledger: dict, payload: bytes) -> None:
        wrapper = ledger["executionWrapper"]
        old_parts = [root / value for value in wrapper["payloadParts"]]
        for path in old_parts:
            path.unlink(missing_ok=True)
        compressed = gzip.compress(payload, compresslevel=9, mtime=0)
        encoded = base64.b64encode(compressed).decode("ascii")
        part_paths = []
        for index, offset in enumerate(range(0, len(encoded), 7600), start=1):
            relative = f"data/zed-fleet-reconcile-no-force.part{index:02d}.b64"
            (root / relative).write_text(encoded[offset : offset + 7600], encoding="utf-8")
            part_paths.append(relative)
        old_sha = wrapper["payloadDecodedSha256"]
        new_sha = hashlib.sha256(payload).hexdigest()
        wrapper_path = root / wrapper["path"]
        wrapper_text = wrapper_path.read_text(encoding="utf-8").replace(old_sha, new_sha)
        wrapper_path.write_text(wrapper_text, encoding="utf-8")
        wrapper.update(
            {
                "bytes": len(wrapper_text.encode()),
                "sha256": hashlib.sha256(wrapper_text.encode()).hexdigest(),
                "payloadParts": part_paths,
                "payloadCompressedSha256": hashlib.sha256(compressed).hexdigest(),
                "payloadDecodedSha256": new_sha,
                "payloadDecodedBytes": len(payload),
            }
        )
        artifact = next(
            item
            for item in ledger["artifacts"]
            if item["origin"] == "semantic-no-force-derivative"
        )
        artifact.update(
            {
                "path": "scripts/zed-fleet-reconcile-no-force.payload.sh",
                "bytes": len(payload),
                "sha256": new_sha,
                "carrierParts": part_paths,
            }
        )

    def test_checked_in_wave_is_valid_and_not_promotion_ready(self) -> None:
        result = validate(ROOT, LEDGER)
        self.assertTrue(result["valid"])
        self.assertFalse(result["forcePushUsed"])
        self.assertFalse(result["promotionReady"])
        self.assertEqual(result["artifactCount"], 6)
        self.assertEqual(result["missingRepositoryCount"], 4)

    def test_rejects_tampered_artifact_digest(self) -> None:
        root, ledger = self.clone_fixture()
        ledger["artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "digest mismatch"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_force_push_in_safe_derivative(self) -> None:
        root, ledger = self.clone_fixture()
        wrapper = ledger["executionWrapper"]
        encoded = b"".join((root / value).read_bytes() for value in wrapper["payloadParts"])
        payload = gzip.decompress(base64.b64decode(encoded, validate=True))
        self.rewrite_safe_payload(root, ledger, payload + b"\ngit push --force origin bad\n")
        with self.assertRaisesRegex(ValidationError, "unsafe history"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_rebase_in_safe_derivative(self) -> None:
        root, ledger = self.clone_fixture()
        wrapper = ledger["executionWrapper"]
        encoded = b"".join((root / value).read_bytes() for value in wrapper["payloadParts"])
        payload = gzip.decompress(base64.b64decode(encoded, validate=True))
        self.rewrite_safe_payload(root, ledger, payload + b"\ngit rebase origin/main\n")
        with self.assertRaisesRegex(ValidationError, "unsafe history"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_original_as_executable(self) -> None:
        root, ledger = self.clone_fixture()
        next(item for item in ledger["artifacts"] if item["path"].endswith("original.sh"))["executeAllowed"] = True
        with self.assertRaisesRegex(ValidationError, "must remain non-executable"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_missing_repository_completion_claim(self) -> None:
        root, ledger = self.clone_fixture()
        ledger["missingRepositories"][0]["verified"] = "created"
        with self.assertRaisesRegex(ValidationError, "not fail-closed"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_dancing_dragons_target_drift(self) -> None:
        root, ledger = self.clone_fixture()
        ledger["excludedTargets"] = []
        with self.assertRaisesRegex(ValidationError, "excluded target drifted"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_recovery_as_acceptance(self) -> None:
        root, ledger = self.clone_fixture()
        ledger["safety"]["recoveryIsAcceptance"] = True
        with self.assertRaisesRegex(ValidationError, "recoveryIsAcceptance must be false"):
            validate(root, self.write_ledger(root, ledger))

    def test_rejects_fabricated_zed_implementation_claim(self) -> None:
        root, ledger = self.clone_fixture()
        item = next(entry for entry in ledger["semanticReconciliation"] if entry["subject"] == "Zed dependency-inventory post-merge audit")
        item["resolution"] = "implementation complete"
        with self.assertRaisesRegex(ValidationError, "non-implementation claim"):
            validate(root, self.write_ledger(root, ledger))


if __name__ == "__main__":
    unittest.main()
