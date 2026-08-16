from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT
    ).decode("utf-8")
    return [path for path in output.split("\0") if path]


class RepositoryHygieneTests(unittest.TestCase):
    def test_runtime_secrets_and_outputs_are_not_tracked(self) -> None:
        violations: list[str] = []
        for path in tracked_files():
            parts = Path(path).parts
            name = Path(path).name
            if "outputs" in parts:
                violations.append(path)
            if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
                violations.append(path)

        self.assertEqual(
            violations,
            [],
            "运行时凭据或数据产物不得进入 Git 跟踪",
        )


if __name__ == "__main__":
    unittest.main()
