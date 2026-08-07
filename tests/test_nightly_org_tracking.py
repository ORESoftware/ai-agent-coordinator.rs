from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "nightly_org_maintenance.py"
SPEC = importlib.util.spec_from_file_location("nightly_org_maintenance_tracking", MODULE_PATH)
assert SPEC and SPEC.loader
maintenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = maintenance
SPEC.loader.exec_module(maintenance)


class NightlyTrackingTests(unittest.TestCase):
    def test_documentation_detection_covers_common_paths(self) -> None:
        self.assertTrue(maintenance._change_has_documentation({"changed_files": ["docs/operations.md"]}))
        self.assertTrue(maintenance._change_has_documentation({"changed_files": ["README.md"]}))
        self.assertTrue(maintenance._change_has_documentation({"changed_files": ["guide/setup.rst"]}))
        self.assertFalse(maintenance._change_has_documentation({"changed_files": ["src/lib.rs", "tests/lib.rs"]}))

    def test_project_selection_prefers_exact_owner_title(self) -> None:
        selected = maintenance._select_project(
            "opto-sync",
            [
                {"number": 1, "title": "Portfolio", "url": "https://example.test/1"},
                {"number": 2, "title": "github.com/opto-sync", "url": "https://example.test/2"},
            ],
        )
        self.assertEqual(2, selected["number"])

    def test_project_selection_accepts_only_open_project(self) -> None:
        selected = maintenance._select_project(
            "opto-sync",
            [{"number": 7, "title": "Delivery", "url": "https://example.test/7"}],
        )
        self.assertEqual(7, selected["number"])

    def test_project_selection_rejects_ambiguity(self) -> None:
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance._select_project(
                "opto-sync",
                [
                    {"number": 1, "title": "Delivery", "url": "https://example.test/1"},
                    {"number": 2, "title": "Roadmap", "url": "https://example.test/2"},
                ],
            )

    def test_normalized_projects_rejects_invalid_shape(self) -> None:
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance._normalized_projects("not-a-list")


if __name__ == "__main__":
    unittest.main()
