# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

# pylint: disable=missing-docstring


import unittest

from aas_smt_lint.common import Path, GlobalIdentifier


class TestPath(unittest.TestCase):
    def test_path_str_single_fragment(self) -> None:
        path = Path(["root"])
        self.assertEqual(str(path), "root")

    def test_path_str_multiple_fragments(self) -> None:
        path = Path(["root", "child", 0, "grandchild"])
        self.assertEqual(str(path), "root.child[0].grandchild")


class TestGlobalIdentifier(unittest.TestCase):
    def test_valid_global_identifier(self) -> None:
        global_id = GlobalIdentifier("valid_identifier")
        self.assertEqual(global_id, "valid_identifier")

    def test_global_identifier_with_whitespace_raises(self) -> None:
        with self.assertRaises(Exception):  # icontract violation
            GlobalIdentifier(" has_whitespace ")


if __name__ == "__main__":
    unittest.main()
