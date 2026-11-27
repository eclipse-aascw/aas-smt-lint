# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

# pylint: disable=missing-docstring

import contextlib
import io
import pathlib
import tempfile
import unittest
import unittest.mock
from typing import List, Tuple

import aas_smt_lint.main

_NAMESPACE_V3_0 = "https://admin-shell.io/aas/3/0"
_NAMESPACE_V3_1 = "https://admin-shell.io/aas/3/1"


def _run_main(args: List[str]) -> Tuple[int, str]:
    """Run the main routine with ``args`` and return the exit code and STDERR."""
    stderr = io.StringIO()
    with (
        unittest.mock.patch("sys.argv", ["aas-smt-lint"] + args),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = aas_smt_lint.main.main(prog="aas-smt-lint")

    return exit_code, stderr.getvalue()


def _write_empty_xml_environment(directory: pathlib.Path, namespace: str) -> str:
    path = directory / "environment.xml"
    path.write_text(f'<environment xmlns="{namespace}"/>', encoding="utf-8")
    return str(path)


class TestMain(unittest.TestCase):
    def test_default_is_v3_1(self) -> None:
        self.assertIs(
            aas_smt_lint.main.AasVersion.V3_1, aas_smt_lint.main.DEFAULT_AAS_VERSION
        )

    def test_v3_1_xml_with_default_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_empty_xml_environment(pathlib.Path(tmp_dir), _NAMESPACE_V3_1)
            exit_code, stderr = _run_main([path])

        self.assertEqual("", stderr)
        self.assertEqual(0, exit_code)

    def test_v3_0_xml_with_explicit_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_empty_xml_environment(pathlib.Path(tmp_dir), _NAMESPACE_V3_0)
            exit_code, stderr = _run_main(["--aas-version", "3.0", path])

        self.assertEqual("", stderr)
        self.assertEqual(0, exit_code)

    def test_v3_0_xml_with_default_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write_empty_xml_environment(pathlib.Path(tmp_dir), _NAMESPACE_V3_0)
            exit_code, stderr = _run_main([path])

        self.assertEqual(1, exit_code)
        self.assertIn(f"in the XML namespace {_NAMESPACE_V3_0!r}", stderr)
        self.assertIn("did you specify the correct AAS version?", stderr)


if __name__ == "__main__":
    unittest.main()
