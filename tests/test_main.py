# pylint: disable=missing-docstring

import unittest
from typing import Iterator

import aas_core3.types as aas_types

# noinspection PyProtectedMember
from aas_smt_lint.main import (
    _deserialize_environment,
    Path,
    GlobalIdentifier,
    _WhitespaceChecker,
    Message,
    _verify_id_shorts_for_plural_singular,
    Level,
)


class TestDeserializeEnvironment(unittest.TestCase):
    def test_empty_text(self) -> None:
        environment, error = _deserialize_environment("")
        self.assertIsNone(environment)
        self.assertEqual(error, "No text to be parsed")

    def test_whitespace_only(self) -> None:
        environment, error = _deserialize_environment("   \n\t  ")
        self.assertIsNone(environment)
        self.assertEqual(error, "No text to be parsed")

    def test_invalid_json(self) -> None:
        environment, error = _deserialize_environment('{"invalid": json}')
        self.assertIsNone(environment)
        assert error is not None
        self.assertIn("Failed to parse JSON", error)

    def test_unrecognized_format(self) -> None:
        environment, error = _deserialize_environment("random text")
        self.assertIsNone(environment)
        assert error is not None
        self.assertIn("Unrecognized first character", error)

    def test_valid_json_environment(self) -> None:
        json_text = '{"submodels": []}'
        environment, error = _deserialize_environment(json_text)
        self.assertIsNone(error)
        assert environment is not None
        self.assertIsInstance(environment, aas_types.Environment)


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


def messages_to_string(messages: Iterator[Message]) -> str:
    """Convert iterator of messages to newline-joined lines with format '{path}: {text}'."""
    return "\n".join(f"{msg.path}: {msg.text}" for msg in messages)


class TestWhitespaceChecker(unittest.TestCase):
    def test_clean_submodel_no_warnings(self) -> None:
        submodel = aas_types.Submodel(
            id="clean_submodel", kind=aas_types.ModellingKind.TEMPLATE
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        self.assertEqual("", result)

    def test_submodel_with_leading_whitespace_in_id(self) -> None:
        submodel = aas_types.Submodel(
            id=" leading_space", kind=aas_types.ModellingKind.TEMPLATE
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        expected = " leading_space: Surrounding whitespace in id"
        self.assertEqual(expected, result)

    def test_submodel_with_trailing_whitespace_in_id(self) -> None:
        submodel = aas_types.Submodel(
            id="trailing_space ", kind=aas_types.ModellingKind.TEMPLATE
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        expected = "trailing_space : Surrounding whitespace in id"
        self.assertEqual(expected, result)

    def test_submodel_with_whitespace_in_middle_of_id(self) -> None:
        submodel = aas_types.Submodel(
            id="has space inside", kind=aas_types.ModellingKind.TEMPLATE
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        expected = "has space inside: Whitespace in the middle of submodel id"
        self.assertEqual(expected, result)

    def test_property_with_leading_whitespace_in_value(self) -> None:
        prop = aas_types.Property(
            id_short="test_prop",
            value_type=aas_types.DataTypeDefXSD.STRING,
            value=" leading_space",
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[prop],
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        expected = "test_submodel.test_prop: Surrounding whitespace in value"
        self.assertEqual(expected, result)

    def test_property_with_trailing_whitespace_in_value(self) -> None:
        prop = aas_types.Property(
            id_short="test_prop",
            value_type=aas_types.DataTypeDefXSD.STRING,
            value="trailing_space ",
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[prop],
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        expected = "test_submodel.test_prop: Surrounding whitespace in value"
        self.assertEqual(expected, result)

    def test_multiple_whitespace_issues(self) -> None:
        prop = aas_types.Property(
            id_short="bad_prop",
            value_type=aas_types.DataTypeDefXSD.STRING,
            value=" bad_value ",
        )
        submodel = aas_types.Submodel(
            id=" bad_submodel ",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[prop],
        )
        checker = _WhitespaceChecker()
        result = messages_to_string(checker.check_submodel(submodel))
        expected = """\
 bad_submodel : Surrounding whitespace in id
 bad_submodel .bad_prop: Surrounding whitespace in value"""
        self.assertEqual(expected, result)


class TestVerifyIdShortsForPluralSingular(unittest.TestCase):
    def test_submodel_element_list_with_singular_id_short(self) -> None:
        submodel_list = aas_types.SubmodelElementList(
            id_short="item",
            type_value_list_element=aas_types.AASSubmodelElements.PROPERTY,
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[submodel_list],
        )
        environment = aas_types.Environment(submodels=[submodel])

        messages = list(_verify_id_shorts_for_plural_singular(environment))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level, Level.WARNING)
        self.assertEqual(
            messages[0].text,
            "Expected the ID-short in plural for aggregations, "
            "but got singular: 'item'",
        )

    def test_property_with_plural_id_short(self) -> None:
        prop = aas_types.Property(
            id_short="items",
            value_type=aas_types.DataTypeDefXSD.STRING,
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[prop],
        )
        environment = aas_types.Environment(submodels=[submodel])

        messages = list(_verify_id_shorts_for_plural_singular(environment))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level, Level.WARNING)
        self.assertEqual(
            messages[0].text,
            "Expected the ID-short in singular for non-aggregations, "
            "but got plural: 'items'",
        )

    def test_submodel_element_list_with_plural_id_short_no_error(self) -> None:
        submodel_list = aas_types.SubmodelElementList(
            id_short="items",
            type_value_list_element=aas_types.AASSubmodelElements.PROPERTY,
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[submodel_list],
        )
        environment = aas_types.Environment(submodels=[submodel])

        messages = list(_verify_id_shorts_for_plural_singular(environment))
        self.assertEqual(len(messages), 0)

    def test_property_with_singular_id_short_no_error(self) -> None:
        prop = aas_types.Property(
            id_short="item",
            value_type=aas_types.DataTypeDefXSD.STRING,
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[prop],
        )
        environment = aas_types.Environment(submodels=[submodel])

        messages = list(_verify_id_shorts_for_plural_singular(environment))
        self.assertEqual(len(messages), 0)

    def test_camel_case_with_underscore_plural_for_property(self) -> None:
        prop = aas_types.Property(
            id_short="dataItems",
            value_type=aas_types.DataTypeDefXSD.STRING,
        )
        submodel = aas_types.Submodel(
            id="test_submodel",
            kind=aas_types.ModellingKind.TEMPLATE,
            submodel_elements=[prop],
        )
        environment = aas_types.Environment(submodels=[submodel])

        messages = list(_verify_id_shorts_for_plural_singular(environment))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].level, Level.WARNING)
        self.assertEqual(
            messages[0].text,
            "Expected the ID-short in singular for non-aggregations, "
            "but got plural: 'dataItems'",
        )


if __name__ == "__main__":
    unittest.main()
