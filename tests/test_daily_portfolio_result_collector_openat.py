#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import collect_daily_portfolio_results as collector  # noqa: E402


@unittest.skipUnless(
    os.open in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY"),
    "secure openat traversal is unavailable on this platform",
)
class DailyPortfolioOpenAtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "results"
        self.root.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()

    def test_parent_directory_swap_cannot_redirect_final_open(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        expected = b'{"trusted":true}'
        (lane / "result.json").write_bytes(expected)
        (self.outside / "result.json").write_bytes(b'{"attacker":true}')

        root_descriptor = collector._open_result_root(self.root)
        self.addCleanup(os.close, root_descriptor)
        original_open = collector.os.open
        swapped = False

        def swapping_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal swapped
            if path == "result.json" and dir_fd is not None and not swapped:
                lane.rename(self.root / "lane-original")
                (self.root / "lane").symlink_to(
                    self.outside,
                    target_is_directory=True,
                )
                swapped = True
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(collector.os, "open", side_effect=swapping_open):
            observed = collector._read_relative_regular_file(
                root_descriptor,
                "lane/result.json",
                maximum=collector.MAX_LANE_BYTES,
                label="swapped lane result",
            )

        self.assertTrue(swapped)
        self.assertEqual(expected, observed)

    def test_existing_parent_symlink_fails_closed(self) -> None:
        (self.outside / "result.json").write_bytes(b"outside")
        (self.root / "lane").symlink_to(
            self.outside,
            target_is_directory=True,
        )
        root_descriptor = collector._open_result_root(self.root)
        self.addCleanup(os.close, root_descriptor)

        with self.assertRaisesRegex(
            collector.CollectorError,
            "cannot securely open parent",
        ):
            collector._read_relative_regular_file(
                root_descriptor,
                "lane/result.json",
                maximum=collector.MAX_LANE_BYTES,
                label="symlink lane result",
            )

    def test_missing_relative_path_returns_none(self) -> None:
        root_descriptor = collector._open_result_root(self.root)
        self.addCleanup(os.close, root_descriptor)
        self.assertIsNone(
            collector._read_relative_regular_file(
                root_descriptor,
                "missing/result.json",
                maximum=collector.MAX_LANE_BYTES,
                label="missing lane result",
            )
        )


if __name__ == "__main__":
    unittest.main()
