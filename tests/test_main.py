# pylint: disable=missing-docstring

import unittest

# noinspection PyProtectedMember
from aas_smt_lint.main import (
    _deserialize_environment,
    Path,
    GlobalIdentifier,
)


class TestDeserializeEnvironment(unittest.TestCase):
    def test_empty_text(self) -> None:
        result_env, error = _deserialize_environment("")
        self.assertIsNone(result_env)
        self.assertEqual(error, "No text to be parsed")

    def test_whitespace_only(self) -> None:
        result_env, error = _deserialize_environment("   \n\t  ")
        self.assertIsNone(result_env)
        self.assertEqual(error, "No text to be parsed")

    def test_invalid_json(self) -> None:
        result_env, error = _deserialize_environment('{"invalid": json}')
        self.assertIsNone(result_env)
        assert error is not None
        self.assertIn("Failed to parse JSON", error)

    def test_unrecognized_format(self) -> None:
        result_env, error = _deserialize_environment("random text")
        self.assertIsNone(result_env)
        assert error is not None
        self.assertIn("Unrecognized first character", error)


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
