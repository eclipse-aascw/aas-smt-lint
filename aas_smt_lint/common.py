# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""Provide common functionality shared across modules."""

import enum
import io
import re
import textwrap
import xml.etree.ElementTree
from typing import Iterable, cast, Final, Sequence, Union, List, Optional

import inflect
from icontract import ensure, require
from typing_extensions import assert_never


@ensure(lambda text, result: text.startswith("\n") or not result.startswith("\n"))
def indent_but_first_line(text: str, indent: str = "  ") -> str:
    """
    Indent all but the first line.

    Examples:
    >>> indent_but_first_line("Line 1\\nLine 2\\nLine 3")
    'Line 1\\n  Line 2\\n  Line 3'

    >>> indent_but_first_line("Single line only")
    'Single line only'

    >>> indent_but_first_line("First\\nSecond", indent=">> ")
    'First\\n>> Second'

    >>> indent_but_first_line("")
    ''

    >>> indent_but_first_line("Just one line\\n")
    'Just one line\\n'
    """
    split = text.split("\n", 1)
    if len(split) == 1:
        return text
    first, rest = split
    return first + "\n" + textwrap.indent(rest, indent)


def bullet_points(points: Iterable[str]) -> str:
    """Make a bullet point list out of the given points."""
    indent = "  "
    return "\n".join(f"* {indent_but_first_line(point, indent)}" for point in points)


class Rstripped(str):
    """
    Represent a block of text without trailing whitespace.

    The block can be both single-line or multi-line.
    """

    @require(
        lambda block: not block.endswith("\n")
        and not block.endswith(" ")
        and not block.endswith("\t")
    )
    def __new__(cls, block: str) -> "Rstripped":
        return cast(Rstripped, block)


def is_stripped(text: str) -> bool:
    """Check that the ``text`` does not have leading and trailing whitespace."""
    return (
        not text.startswith("\n")
        and not text.startswith(" ")
        and not text.startswith("\t")
    ) and (
        not text.endswith("\n") and not text.endswith(" ") and not text.endswith("\t")
    )


class Stripped(Rstripped):
    """
    Represent a block of text without leading and trailing whitespace.

    The block of text can be both single-line and multi-line.
    """

    @require(lambda block: is_stripped(block))
    def __new__(cls, block: str) -> "Stripped":
        return cast(Stripped, block)


class NonNegativeInt(int):
    """Represent a non-negative integer, *i.e.*, ``>= 0``."""

    @require(lambda value: value >= 0)
    def __new__(cls, value: int) -> "NonNegativeInt":
        return cast(NonNegativeInt, value)


class Level(enum.Enum):
    """Define the level of the assessment message."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class Path:
    """Represent an ID-short path."""

    fragments: Final[Sequence[Union[str, int]]]

    @require(
        lambda fragments: len(fragments) > 0 and isinstance(fragments[0], str),
        "ID-short paths must start with an identifier, "
        "coming from an AAS identifiable",
    )
    def __init__(self, fragments: Sequence[Union[str, int]]) -> None:
        self.fragments = fragments

    def __str__(self) -> str:
        if len(self.fragments) == 0:
            return ""

        parts: List[str] = []

        iterator = iter(self.fragments)

        first = next(iterator)
        assert first is not None

        parts.append(str(first))

        for fragment in iterator:
            if isinstance(fragment, str):
                parts.append(f".{fragment}")
            elif isinstance(fragment, int):
                parts.append(f"[{fragment}]")
            else:
                # noinspection PyTypeChecker
                assert_never(fragment)

        return "".join(parts)


class Message:
    """Represent assessment messages presented to the user."""

    level: Final[Level]
    text: Final[str]
    path: Final[Path]

    def __init__(self, level: Level, text: str, path: Path) -> None:
        self.level = level
        self.text = text
        self.path = path


class GlobalIdentifier(Stripped):
    """Represent a non-empty string with no leading or trailing whitespace."""

    @require(lambda value: len(value) > 0)
    @require(lambda value: is_stripped(value))
    def __new__(cls, value: str) -> "GlobalIdentifier":
        return cast(GlobalIdentifier, value)


def human_readable_attribute_name(name: str) -> str:
    """Convert the Python attribute name into a human-readable form."""
    return name.replace("_", " ")


def has_surrounding_whitespace(text: str) -> bool:
    """
    Check whether the given text contains surrounding whitespace.

    Surrounding whitespace means a leading or a trailing space, tab or newline.

    >>> has_surrounding_whitespace('no')
    False

    >>> has_surrounding_whitespace(' yes')
    True

    >>> has_surrounding_whitespace('yes ')
    True

    >>> has_surrounding_whitespace(' yes ')
    True
    """
    if len(text) == 0:
        return False

    first = text[0]
    last = text[-1]

    return (
        first == " "
        or first == "\t"
        or first == "\n"
        or last == " "
        or last == "\t"
        or last == "\n"
    )


_WHITESPACE_IN_MIDDLE_RE = re.compile(r"^\s*[^\s]+\s+[^\s]+")


def has_whitespace_in_middle(text: str) -> bool:
    """
    Check whether the ``text`` contains any whitespace within it.

    >>> has_whitespace_in_middle('no')
    False

    >>> has_whitespace_in_middle(' no ')
    False

    >>> has_whitespace_in_middle('\\nno\\n')
    False

    >>> has_whitespace_in_middle('space inside')
    True

    >>> has_whitespace_in_middle('has space inside')
    True

    >>> has_whitespace_in_middle('yes\\nyes')
    True
    """
    return _WHITESPACE_IN_MIDDLE_RE.match(text) is not None


DEPRECATED_CARDINALITY_QUALIFIERS_SET = {"Multiplicity", "Cardinality"}


TEMPLATE_QUALIFIERS = {
    "SMT/Cardinality",
    "SMT/EitherOr",
    "SMT/InitialValue",
    "SMT/DefaultValue",
    "SMT/ExampleValue",
    "SMT/AllowedRange",
    "SMT/AllowedIdShort",
    "SMT/AllowedValue",
    "SMT/AccessMode",
    "FormTitle",
    "FormInfo",
    "FormUrl",
    "EditIdShort",
    "EditDescription",
    "FormChoices",
}


CARDINALITY_QUALIFIERS_SET = {"SMT/Cardinality"} | DEPRECATED_CARDINALITY_QUALIFIERS_SET


class Cardinality(enum.Enum):
    """Enumerate the cardinalities given by the cardinality qualifiers."""

    ZERO_TO_ONE = "ZeroToOne"
    ONE = "One"
    ZERO_TO_MANY = "ZeroToMany"
    ONE_TO_MANY = "OneToMany"


CARDINALITY_BY_VALUE = {cardinality.value: cardinality for cardinality in Cardinality}


_CAMEL_CASE_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+")


def split_id_short_in_parts(text: str) -> List[str]:
    """
    Split on underscores and camel case.

    >>> split_id_short_in_parts('do_something')
    ['do', 'something']

    >>> split_id_short_in_parts('camelCaseURL')
    ['camel', 'Case', 'URL']

    >>> split_id_short_in_parts('HTTPRequestParser')
    ['HTTP', 'Request', 'Parser']

    >>> split_id_short_in_parts('simpleXMLParser')
    ['simple', 'XML', 'Parser']

    >>> split_id_short_in_parts('B10')
    ['B', '10']

    >>> split_id_short_in_parts('XML2Text')
    ['XML', '2', 'Text']
    """
    parts = text.split("_")

    result = []  # type: List[str]

    for part in parts:
        camel_case_parts = _CAMEL_CASE_PART_RE.findall(part)
        result.extend(camel_case_parts)

    return result


@require(lambda text: len(text.strip()) > 0, "The text must not be empty")
def is_singular(text: str, engine: inflect.engine) -> bool:
    """
    Check whether the ``text`` is a noun in plural.

    >>> engine_for_doctest = inflect.engine()
    >>> is_singular('contact information', engine_for_doctest)
    True

    >>> is_singular('keys', engine_for_doctest)
    False

    >>> is_singular('Endpoint Metadata', engine_for_doctest)
    True

    >>> is_singular('Address', engine_for_doctest)
    True

    >>> is_singular('Address of contact', engine_for_doctest)
    True
    """
    # NOTE (mristin):
    # We have to handle special cases here that ``inflect`` library does not.
    parts = text.split(" ")
    if len(parts) >= 1:
        if (
            parts[-1].lower() in ("data", "meta-data", "metadata")
            or parts[0].lower() == "address"
        ):
            return True

    # NOTE (mristin):
    # The ``singular`` here refers to the singular form. If singular is False, that
    # means that the ``text`` is already in singular form.
    # noinspection PyTypeChecker
    singular_form = engine.singular_noun(text)

    return singular_form is False or singular_form == text


def sniff_xml_namespace(text: str) -> Optional[str]:
    """
    Determine the XML namespace of the root element in ``text``.

    Return None if the root element has no namespace or ``text`` could not be parsed.
    Only the beginning of ``text`` is parsed, up to the start of the root element.

    >>> sniff_xml_namespace('<environment xmlns="https://admin-shell.io/aas/3/1"/>')
    'https://admin-shell.io/aas/3/1'

    >>> sniff_xml_namespace('<environment/>') is None
    True

    >>> sniff_xml_namespace('<broken') is None
    True
    """
    try:
        for _, element in xml.etree.ElementTree.iterparse(
            io.StringIO(text), events=("start",)
        ):
            if element.tag.startswith("{"):
                return element.tag[1 : element.tag.index("}")]

            return None
    except xml.etree.ElementTree.ParseError:
        return None

    return None
