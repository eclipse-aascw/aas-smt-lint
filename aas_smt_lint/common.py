"""Provide common functionality shared across modules."""

import textwrap
from typing import Iterable, cast

from icontract import ensure, require


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
