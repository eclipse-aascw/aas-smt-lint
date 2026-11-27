# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""
Check that the code for the different versions of the AAS meta-model is kept in sync.

We keep one copy of the linting code (and one copy of its tests) per version of
the AAS meta-model so that each copy is fully typed against its own aas-core SDK.
The copies must be *textually* identical, comments included, except for:

* the import statements, which may differ only in the version-specific package
  names, and
* the top-level definitions explicitly listed in :py:data:`EXCEPTIONS`.
"""

import ast
import difflib
import pathlib
import unittest
from typing import Dict, Final, List, Mapping, Optional, Sequence, Tuple

import asttokens

# pylint: disable=missing-docstring

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Pair:
    """Represent two files which need to be kept in sync."""

    reference: Final[pathlib.Path]
    other: Final[pathlib.Path]

    #: Replacements applied to the import statements of :py:attr:`other` so that
    #: they become identical to the import statements of :py:attr:`reference`
    import_replacements: Final[Sequence[Tuple[str, str]]]

    def __init__(
        self,
        reference: pathlib.Path,
        other: pathlib.Path,
        import_replacements: Sequence[Tuple[str, str]],
    ) -> None:
        self.reference = reference
        self.other = other
        self.import_replacements = import_replacements


PAIRS: Sequence[_Pair] = [
    _Pair(
        reference=REPO_ROOT / "aas_smt_lint" / "v3_1.py",
        other=REPO_ROOT / "aas_smt_lint" / "v3_0.py",
        import_replacements=[
            ("aas_core3 ", "aas_core3_1 "),
            ("aas_core3.", "aas_core3_1."),
        ],
    ),
    _Pair(
        reference=REPO_ROOT / "tests" / "test_v3_1.py",
        other=REPO_ROOT / "tests" / "test_v3_0.py",
        import_replacements=[
            ("aas_core3 ", "aas_core3_1 "),
            ("aas_core3.", "aas_core3_1."),
            ("aas_smt_lint.v3_0", "aas_smt_lint.v3_1"),
        ],
    ),
]

#: Top-level definitions which are allowed to differ, mapped from the file
#: (relative to the repository root, as POSIX path) of the reference to
#: the names of the definitions and the reasons for the exception.
EXCEPTIONS: Mapping[str, Mapping[str, str]] = {
    # Example:
    # "aas_smt_lint/v3_1.py": {
    #     "verify_deprecated": "Category is not deprecated anymore in V3.1",
    # },
}


class _Segment:
    """Represent a top-level statement together with the text preceding it."""

    name: Final[str]
    is_import: Final[bool]
    text: Final[str]

    def __init__(self, name: str, is_import: bool, text: str) -> None:
        self.name = name
        self.is_import = is_import
        self.text = text


def _name_of(node: ast.stmt) -> Optional[str]:
    """
    Determine the name of the top-level statement used to report differences.

    Return None if the statement has no name (*e.g.*, a docstring or an if-statement).
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name

    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id

    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id

    return None


def _split_in_segments(path: pathlib.Path) -> List[_Segment]:
    """
    Split the source code at ``path`` into segments, one per top-level statement.

    Each segment includes all the text since the end of the previous statement
    (*e.g.*, comments, decorators and blank lines), so the concatenation of
    the segments gives back the whole source code.
    """
    source = path.read_text(encoding="utf-8")
    atok = asttokens.ASTTokens(source, parse=True)
    assert isinstance(atok.tree, ast.Module)

    segments: List[_Segment] = []
    start = 0

    # NOTE (mristin):
    # We number the unnamed statements instead of using their line numbers so that
    # the names do not shift when a listed exception changes the length of the code.
    unnamed_counts: Dict[str, int] = dict()

    for node in atok.tree.body:
        name = _name_of(node)
        if name is None:
            kind = node.__class__.__name__
            unnamed_counts[kind] = unnamed_counts.get(kind, 0) + 1
            name = f"<{kind} #{unnamed_counts[kind]}>"

        _, end = atok.get_text_range(node)
        segments.append(
            _Segment(
                name=name,
                is_import=isinstance(node, (ast.Import, ast.ImportFrom)),
                text=source[start:end],
            )
        )
        start = end

    segments.append(
        _Segment(name="<end of file>", is_import=False, text=source[start:])
    )

    assert "".join(segment.text for segment in segments) == source

    return segments


def _normalize(segment: _Segment, pair: _Pair) -> str:
    """Normalize the text of ``segment`` from :py:attr:`_Pair.other`."""
    if not segment.is_import:
        return segment.text

    text = segment.text
    for old, new in pair.import_replacements:
        text = text.replace(old, new)

    return text


def _diff(reference: str, other: str, pair: _Pair) -> str:
    return "".join(
        difflib.unified_diff(
            reference.splitlines(keepends=True),
            other.splitlines(keepends=True),
            fromfile=str(pair.reference.relative_to(REPO_ROOT)),
            tofile=str(pair.other.relative_to(REPO_ROOT)),
        )
    )


def _exceptions_for(pair: _Pair) -> Mapping[str, str]:
    return EXCEPTIONS.get(pair.reference.relative_to(REPO_ROOT).as_posix(), dict())


class TestVersionModulesInSync(unittest.TestCase):
    def test_exceptions_refer_to_known_files(self) -> None:
        known = {pair.reference.relative_to(REPO_ROOT).as_posix() for pair in PAIRS}
        for key in EXCEPTIONS:
            self.assertIn(key, known)

    def test_same_definitions_in_same_order(self) -> None:
        for pair in PAIRS:
            with self.subTest(reference=pair.reference, other=pair.other):
                # NOTE (mristin):
                # The import statements are checked in
                # ``test_imports_differ_only_in_package``.
                reference_names = [
                    segment.name
                    for segment in _split_in_segments(pair.reference)
                    if not segment.is_import
                ]
                other_names = [
                    segment.name
                    for segment in _split_in_segments(pair.other)
                    if not segment.is_import
                ]

                self.assertListEqual(reference_names, other_names)

    def test_imports_differ_only_in_package(self) -> None:
        for pair in PAIRS:
            with self.subTest(reference=pair.reference, other=pair.other):
                reference_imports = "".join(
                    segment.text
                    for segment in _split_in_segments(pair.reference)
                    if segment.is_import
                )
                other_imports = "".join(
                    _normalize(segment, pair)
                    for segment in _split_in_segments(pair.other)
                    if segment.is_import
                )

                if reference_imports != other_imports:
                    self.fail(
                        f"The import statements differ in more than the package names "
                        f"{pair.import_replacements!r}:\n"
                        f"{_diff(reference_imports, other_imports, pair)}"
                    )

    def test_definitions_identical_except_listed(self) -> None:
        for pair in PAIRS:
            with self.subTest(reference=pair.reference, other=pair.other):
                exceptions = _exceptions_for(pair)

                # NOTE (mristin):
                # We match the segments by name, not by position, so that a single
                # added or removed definition does not misalign all the following
                # ones. Missing definitions are reported by
                # ``test_same_definitions_in_same_order``.
                other_segments = {
                    segment.name: segment
                    for segment in _split_in_segments(pair.other)
                    if not segment.is_import
                }

                diffs: List[str] = []
                for reference_segment in _split_in_segments(pair.reference):
                    if reference_segment.is_import or (
                        reference_segment.name in exceptions
                    ):
                        continue

                    other_segment = other_segments.get(reference_segment.name, None)
                    if other_segment is None:
                        continue

                    other_text = _normalize(other_segment, pair)
                    if reference_segment.text != other_text:
                        diffs.append(
                            f"{reference_segment.name}:\n"
                            f"{_diff(reference_segment.text, other_text, pair)}"
                        )

                if len(diffs) > 0:
                    self.fail(
                        "The following definitions differ, but are not listed "
                        "in EXCEPTIONS of tests/test_version_modules_in_sync.py:\n\n"
                        + "\n".join(diffs)
                    )

    def test_no_stale_exceptions(self) -> None:
        for pair in PAIRS:
            with self.subTest(reference=pair.reference, other=pair.other):
                exceptions = _exceptions_for(pair)

                reference_segments = {
                    segment.name: segment
                    for segment in _split_in_segments(pair.reference)
                }
                other_segments = {
                    segment.name: segment for segment in _split_in_segments(pair.other)
                }

                for name in exceptions:
                    self.assertIn(
                        name,
                        reference_segments,
                        f"The exception {name!r} refers to no definition "
                        f"in {pair.reference}",
                    )

                    self.assertIn(
                        name,
                        other_segments,
                        f"The exception {name!r} refers to no definition "
                        f"in {pair.other}",
                    )

                    if reference_segments[name].text == _normalize(
                        other_segments[name], pair
                    ):
                        self.fail(
                            f"The exception {name!r} is listed, but the definition is "
                            f"identical in {pair.reference} and {pair.other}; "
                            f"please remove it from EXCEPTIONS."
                        )


if __name__ == "__main__":
    unittest.main()
