"""Assess automatically the quality of AAS Submodel Templates."""

import argparse
import enum
import glob
import itertools
import json
import pathlib
import re
import sys
from typing import (
    List,
    Tuple,
    Optional,
    Dict,
    Sequence,
    Iterator,
    Final,
    Union,
    get_args,
    cast,
    MutableMapping,
    Mapping,
)

import aas_core3.jsonization as aas_jsonization
import aas_core3.types as aas_types
import aas_core3.xmlization as aas_xmlization
from icontract import ensure, require
from typing_extensions import assert_never

import aas_smt_lint
from aas_smt_lint.common import bullet_points, Stripped, is_stripped

assert aas_smt_lint.__doc__ == __doc__


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _deserialize_environment(
    text: str,
) -> Tuple[Optional[aas_types.Environment], Optional[str]]:
    """Deserialize the environment from the given text either as JSON or XML."""
    first_non_space = None  # type: Optional[str]
    for char in text:
        if char.isspace():
            continue

        first_non_space = char
        break

    if first_non_space is None:
        return None, "No text to be parsed"

    environment: aas_types.Environment

    if first_non_space == "{":
        try:
            jsonable = json.loads(text)
        except json.JSONDecodeError as exception:
            return None, f"Failed to parse JSON: {exception}"

        try:
            environment = aas_jsonization.environment_from_jsonable(jsonable)
        except aas_jsonization.DeserializationException as exception:
            return None, f"Failed to parse: {exception}"

    elif first_non_space == "<":
        try:
            environment = aas_xmlization.environment_from_str(text)
        except aas_xmlization.DeserializationException as exception:
            return None, f"Failed to parse: {exception}"

    else:
        return (
            None,
            f"Unrecognized first character, "
            f"so we do not know how to parse: {first_non_space!r}",
        )

    return environment, None


@ensure(
    lambda result: (result[0] is not None and result[1] is not None)
    ^ (result[2] is not None),
    "Either a result or an error",
)
@ensure(
    lambda result: not (result[2] is not None) or (len(result[2]) > 0),
    "There must be errors if errors is not None",
)
def _read_and_merge_environments(
    paths: Sequence[pathlib.Path],
) -> Tuple[
    Optional[aas_types.Environment],
    Optional[Mapping[str, pathlib.Path]],
    Optional[List[str]],
]:
    """
    Read the AAS environments from the ``paths`` and merge them into a big environment.

    Return the merged AAS environment and identifier to path map, or errors if any.
    """
    merged_environment = aas_types.Environment()

    id_to_path: Dict[str, pathlib.Path] = dict()

    errors: List[str] = []

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exception:
            errors.append(f"Failed to read from {path}: {exception}")
            continue

        environment, deserialize_error = _deserialize_environment(text)
        if deserialize_error is not None:
            errors.append(f"Failed to de-serialize {path}: {deserialize_error}")
            continue

        assert environment is not None

        has_conflict = False

        # noinspection PyTypeChecker
        for identifiable in itertools.chain(
            environment.over_asset_administration_shells_or_empty(),
            environment.over_submodels_or_empty(),
            environment.over_concept_descriptions_or_empty(),
        ):
            identifier = identifiable.id

            conflicting_path = id_to_path.get(identifier, None)
            if conflicting_path is not None:
                if conflicting_path != path:
                    errors.append(
                        f"The identifier of {identifiable.__class__.__name__}, "
                        f"{identifier!r}, from {path} "
                        f"conflicts with another identifiable in {conflicting_path}."
                    )
                else:
                    errors.append(
                        f"The identifier of {identifiable.__class__.__name__}, "
                        f"{identifier!r} "
                        f"conflicts with another identifiable in {conflicting_path}."
                    )
                has_conflict = True
                continue

            id_to_path[identifier] = path

        if has_conflict:
            continue

        for shell in environment.over_asset_administration_shells_or_empty():
            if merged_environment.asset_administration_shells is None:
                merged_environment.asset_administration_shells = []

            merged_environment.asset_administration_shells.append(shell)

        for submodel in environment.over_submodels_or_empty():
            if merged_environment.submodels is None:
                merged_environment.submodels = []

            merged_environment.submodels.append(submodel)

        for concept_description in environment.over_concept_descriptions_or_empty():
            if merged_environment.concept_descriptions is None:
                merged_environment.concept_descriptions = []

            merged_environment.concept_descriptions.append(concept_description)

    if len(errors) > 0:
        return None, None, errors

    return merged_environment, id_to_path, None


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


def _verify_all_submodels_are_templates(
    environment: aas_types.Environment,
) -> Iterator[Message]:
    """Check that all submodels are of kind template."""
    for submodel in environment.over_submodels_or_empty():
        if submodel.kind_or_default() is not aas_types.ModellingKind.TEMPLATE:
            yield Message(
                level=Level.ERROR,
                text="The submodel is of kind instance, but expected only templates.",
                path=Path(fragments=[submodel.id]),
            )


_Container = Union[
    aas_types.Submodel,
    aas_types.SubmodelElementList,
    aas_types.SubmodelElementCollection,
]

_ContainerAsTuple = (
    aas_types.Submodel,
    aas_types.SubmodelElementList,
    aas_types.SubmodelElementCollection,
)

assert _ContainerAsTuple == get_args(_Container)


def _over_elements_of_container_non_recursive(
    container: _Container, prefix: Path
) -> Iterator[Tuple[aas_types.SubmodelElement, Path]]:
    if isinstance(container, aas_types.Submodel):
        for element in container.over_submodel_elements_or_empty():
            assert (
                element.id_short is not None
            ), "All elements of a Submodel must have an ID-short."
            path = Path(fragments=list(prefix.fragments) + [element.id_short])

            yield element, path
    elif isinstance(container, aas_types.SubmodelElementList):
        for i, element in enumerate(container.over_value_or_empty()):
            path = Path(fragments=list(prefix.fragments) + [i])

            yield element, path
    elif isinstance(container, aas_types.SubmodelElementCollection):
        for element in container.over_value_or_empty():
            assert (
                element.id_short is not None
            ), "All elements of a Submodel Element Collection must have an ID-short."
            path = Path(fragments=list(prefix.fragments) + [element.id_short])

            yield element, path
    else:
        # noinspection PyTypeChecker
        assert_never(container)


def _over_elements_of_container_recursively(
    container: _Container, prefix: Path
) -> Iterator[Tuple[aas_types.SubmodelElement, Path]]:
    if isinstance(container, aas_types.Submodel):
        for element in container.over_submodel_elements_or_empty():
            assert (
                element.id_short is not None
            ), "All elements of a Submodel must have an ID-short."
            path = Path(fragments=list(prefix.fragments) + [element.id_short])

            yield element, path

            if isinstance(element, _ContainerAsTuple):
                yield from _over_elements_of_container_recursively(
                    container=element, prefix=path
                )
    elif isinstance(container, aas_types.SubmodelElementList):
        for i, element in enumerate(container.over_value_or_empty()):
            path = Path(fragments=list(prefix.fragments) + [i])

            yield element, path

            if isinstance(element, _ContainerAsTuple):
                yield from _over_elements_of_container_recursively(
                    container=element, prefix=path
                )
    elif isinstance(container, aas_types.SubmodelElementCollection):
        for element in container.over_value_or_empty():
            assert (
                element.id_short is not None
            ), "All elements of a Submodel Element Collection must have an ID-short."
            path = Path(fragments=list(prefix.fragments) + [element.id_short])

            yield element, path

            if isinstance(element, _ContainerAsTuple):
                yield from _over_elements_of_container_recursively(
                    container=element, prefix=path
                )
    else:
        # noinspection PyTypeChecker
        assert_never(container)


def _over_elements_of_environment(
    environment: aas_types.Environment,
) -> Iterator[Tuple[aas_types.SubmodelElement, Path]]:
    """Iterate over all the submodel elements contained in the environment."""
    for submodel in environment.over_submodels_or_empty():
        prefix = Path(fragments=[])

        yield from _over_elements_of_container_recursively(
            container=submodel, prefix=prefix
        )


class GlobalIdentifier(Stripped):
    """Represent a non-empty string with no leading or trailing whitespace."""

    @require(lambda value: len(value) > 0)
    @require(lambda value: is_stripped(value))
    def __new__(cls, value: str) -> "GlobalIdentifier":
        return cast(GlobalIdentifier, value)


def _try_global_id(
    that: Union[aas_types.Submodel, aas_types.SubmodelElementCollection],
) -> Tuple[Optional[GlobalIdentifier], Optional[str]]:
    if isinstance(that, aas_types.Submodel):
        return GlobalIdentifier(that.id.strip()), None

    elif isinstance(that, aas_types.SubmodelElementCollection):
        if that.semantic_id is None:
            return None, "The semantic ID is missing."

        if (
            that.semantic_id.type is aas_types.ReferenceTypes.EXTERNAL_REFERENCE
            and len(that.semantic_id.keys) == 1
            and that.semantic_id.keys[0].type is aas_types.KeyTypes.GLOBAL_REFERENCE
        ):
            return GlobalIdentifier(that.semantic_id.keys[0].value.strip()), None

        with_id_short_part = (
            f" with ID-short {that.id_short!r}" if that.id_short is not None else ""
        )

        return None, (
            f"The semantic ID of the submodel element collection{with_id_short_part} "
            f"was expected to be an external reference "
            f"with a single global reference key, "
            f"but it was not: {aas_jsonization.to_jsonable(that.semantic_id)}"
        )

    else:
        # noinspection PyTypeChecker
        assert_never(that)


def _verify_references_and_definitions(
    environment: aas_types.Environment,
) -> Iterator[Message]:
    """
    Verify that structures are properly defined only once and re-used elsewhere.

    This includes both submodel templates (re-used as templates for submodel element
    collections) as well as embedded definitions of submodel element collections.
    """
    global_id_to_path: MutableMapping[GlobalIdentifier, Path] = dict()

    for element, path in _over_elements_of_environment(environment):
        if not isinstance(
            element, (aas_types.Submodel, aas_types.SubmodelElementCollection)
        ):
            continue

        global_id, global_id_error = _try_global_id(element)
        if global_id_error is not None:
            yield Message(level=Level.ERROR, text=global_id_error, path=path)
            continue

        assert global_id is not None

        path_to_conflict = global_id_to_path.get(global_id, None)

        if isinstance(element, aas_types.Submodel):
            if path_to_conflict is not None:
                yield Message(
                    level=Level.ERROR,
                    text=(
                        f"Conflicting re-definition of the identifier {global_id} "
                        f"for this submodel and the instance at {path_to_conflict}"
                    ),
                    path=path,
                )
                continue
            else:
                global_id_to_path[global_id] = path
        elif isinstance(element, aas_types.SubmodelElementCollection):
            if path_to_conflict is not None:
                # NOTE (mristin):
                # Empty submodel element collections imply a reference to an entity,
                # not a re-definition.
                if element.value is not None and len(element.value) > 0:
                    yield Message(
                        level=Level.ERROR,
                        text=(
                            f"Conflicting re-definition of the identifier {global_id} "
                            f"for this submodel element collection "
                            f"with non-empty elements "
                            f"and the instance at {path_to_conflict}"
                        ),
                        path=path,
                    )
                    continue
            else:
                global_id_to_path[global_id] = path

        else:
            # noinspection PyTypeChecker
            assert_never(element)


# region Check whitespace


def _human_readable_attribute_name(name: str) -> str:
    return name.replace("_", " ")


def _has_surrounding_whitespace(text: str) -> bool:
    """
    Check whether the given text contains surrounding whitespace.

    Surrounding whitespace means a leading or a trailing space, tab or newline.

    >>> _has_surrounding_whitespace('no')
    False

    >>> _has_surrounding_whitespace(' yes')
    True

    >>> _has_surrounding_whitespace('yes ')
    True

    >>> _has_surrounding_whitespace(' yes ')
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


def _has_whitespace_in_middle(text: str) -> bool:
    """
    Check whether the ``text`` contains any whitespace within it.

    >>> _has_whitespace_in_middle('no')
    False

    >>> _has_whitespace_in_middle(' no ')
    False

    >>> _has_whitespace_in_middle('\\nno\\n')
    False

    >>> _has_whitespace_in_middle('space inside')
    True

    >>> _has_whitespace_in_middle('has space inside')
    True

    >>> _has_whitespace_in_middle('yes\\nyes')
    True
    """
    return _WHITESPACE_IN_MIDDLE_RE.match(text) is not None


def _check_attributes_for_surrounding_whitespace(
    that: Union[aas_types.Submodel, aas_types.SubmodelElement],
) -> Iterator[str]:
    """
    Check that no string attribute contains surrounding whitespace.

    Yield message texts if there is surrounding whitespace.
    """
    for attr_name in dir(that):
        # NOTE (mristin):
        # We ignore the private/protected/dunder methods.
        if attr_name.startswith("_"):
            continue

        attr_val = getattr(that, attr_name)

        if isinstance(attr_val, str):
            if _has_surrounding_whitespace(attr_val):
                yield (
                    f"Surrounding whitespace in "
                    f"{_human_readable_attribute_name(attr_name)}"
                )


class _WhitespaceChecker:
    def __init__(self) -> None:
        self._path = []  # type: List[Union[int, str]]

    # fmt: off
    @require(
        lambda self: len(self._path) > 0,
        "Expected the path to point to the element before as we can not "
        "rely on ID-short for list elements."
    )
    # fmt: on
    def _check_submodel_element_recursively(
        self, that: aas_types.SubmodelElement
    ) -> Iterator[Message]:
        for text in _check_attributes_for_surrounding_whitespace(that):
            yield Message(level=Level.WARNING, text=text, path=Path(self._path[:]))

        if isinstance(that, aas_types.SubmodelElementList):
            for i, child in enumerate(that.over_value_or_empty()):
                self._path.append(i)
                try:
                    yield from self._check_submodel_element_recursively(child)
                finally:
                    self._path.pop()

        elif isinstance(that, aas_types.SubmodelElementCollection):
            for child in that.over_value_or_empty():
                # NOTE (mristin):
                # Though the meta-model requires all elements in a collection
                # to have ID-short set, we make our code robust to possible template
                # mistakes in order to avoid confusion for the end user and still
                # report *something*.
                self._path.append(child.id_short if child.id_short is not None else "")
                try:
                    yield from self._check_submodel_element_recursively(child)
                finally:
                    self._path.pop()
        else:
            pass

        if isinstance(that, aas_types.Key):
            if _has_whitespace_in_middle(that.value):
                yield Message(
                    level=Level.WARNING,
                    text="Whitespace in the middle of a key value",
                    path=Path(self._path[:]),
                )

    def check_submodel(self, that: aas_types.Submodel) -> Iterator[Message]:
        """Check the attributes of ``that`` submodel and recurse into its elements."""
        self._path.append(that.id)
        try:
            for text in _check_attributes_for_surrounding_whitespace(that):
                yield Message(level=Level.WARNING, text=text, path=Path(self._path[:]))

            if _has_whitespace_in_middle(that.id):
                yield Message(
                    level=Level.WARNING,
                    text="Whitespace in the middle of submodel id",
                    path=Path(self._path[:]),
                )

            for child in that.over_submodel_elements_or_empty():
                # NOTE (mristin):
                # Though the meta-model requires all elements in a submodel
                # to have ID-short set, we make our code robust to possible template
                # mistakes in order to avoid confusion for the end user and still
                # report *something*.
                self._path.append(child.id_short if child.id_short is not None else "")
                try:
                    yield from self._check_submodel_element_recursively(child)
                finally:
                    self._path.pop()
        finally:
            self._path.pop()


def _verify_whitespace(environment: aas_types.Environment) -> Iterator[Message]:
    """Verify no unexpected whitespace in the attributes of submodels."""
    checker = _WhitespaceChecker()
    for submodel in environment.over_submodels_or_empty():
        yield from checker.check_submodel(submodel)


# endregion Check whitespace


_DEPRECATED_CARDINALITY_QUALIFIERS_SET = {"Multiplicity", "Cardinality"}


def _verify_deprecated_types(environment: aas_types.Environment) -> Iterator[Message]:
    """Verify that the submodel doesn't use features that have been marked as deprecated in the
    standard.

    The standard has been around for some time now and some of its features are, as of now,
    considered as deprecated. This looks for the use of deprecated features and reports them as
    warnings.
    Currently, we check for:
    - The use of the Category attribute of Referable
    - The use of Qualifier types (namely outdated cardinality qualifiers).
    """
    for submodel in environment.over_submodels_or_empty():
        if submodel.category is not None:
            yield Message(
                level=Level.WARNING,
                text='Use of deprecated attribute "Category".',
                path=Path(fragments=[submodel.id]),
            )

    for elem, path in _over_elements_of_environment(environment):
        if not isinstance(elem, aas_types.Referable):
            continue

        if elem.category is not None:
            yield Message(
                level=Level.WARNING,
                text='Use of deprecated attribute "Category".',
                path=path,
            )

    for qualifiable, path in _over_elements_of_environment(environment):
        if not isinstance(qualifiable, aas_types.Qualifiable):
            continue

        for qualifier in qualifiable.over_qualifiers_or_empty():
            if qualifier.type in _DEPRECATED_CARDINALITY_QUALIFIERS_SET:
                yield Message(
                    level=Level.WARNING,
                    text=f'Use of deprecated qualifier "{qualifier}".',
                    path=path,
                )


_TEMPLATE_QUALIFIERS = {
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


def _verify_qualifier_types(environment: aas_types.Environment) -> Iterator[Message]:
    """Check that qualifiers have the correct type."""
    qualifiables_with_path = (
        (qualifiable, path)
        for qualifiable, path in _over_elements_of_environment(environment)
        if isinstance(qualifiable, aas_types.Qualifiable)
    )

    for qualifiable, path in qualifiables_with_path:
        for qualifier in qualifiable.over_qualifiers_or_empty():
            if (
                qualifier.type
                in _TEMPLATE_QUALIFIERS | _DEPRECATED_CARDINALITY_QUALIFIERS_SET
            ) and qualifier.kind_or_default() != aas_types.QualifierKind.TEMPLATE_QUALIFIER:
                yield Message(
                    level=Level.ERROR,
                    text=(
                        f'Wrong qualifier type for qualifier "{qualifier.type}", '
                        "must be TemplateQualifier."
                    ),
                    path=path,
                )


def _verify_arbitrary_types(environment: aas_types.Environment) -> Iterator[Message]:
    """Check for properties marked as arbitrary property types."""
    for submodel_element, path in _over_elements_of_environment(environment):
        # NOTE (empwilli 2025-10-29): not all SMTs properly implement arbitrary
        # types, so we are a little bit lenient for the semantic id check and
        # check the id short, as well.
        match submodel_element.semantic_id:
            case aas_types.Reference(
                type=aas_types.ReferenceTypes.EXTERNAL_REFERENCE,
                keys=[
                    aas_types.Key(
                        type=aas_types.KeyTypes.GLOBAL_REFERENCE,
                        value=value,
                    )
                ],
            ) if value.startswith("https://admin-shell.io/SMT/General/Arbitrary"):
                yield Message(
                    level=Level.WARNING,
                    text="Found arbitrary property annotation.",
                    path=path,
                )
            case _ if "arbitrary" in (submodel_element.id_short or "").lower():
                yield Message(
                    level=Level.WARNING,
                    text="Found arbitrary property annotation.",
                    path=path,
                )
            case _:
                continue


_CARDINALITY_QUALIFIERS_SET = {
    "SMT/Cardinality"
} | _DEPRECATED_CARDINALITY_QUALIFIERS_SET


def _verify_arity_of_non_list_elements(
    environment: aas_types.Environment,
) -> Iterator[Message]:
    """Check for non-list elements that have an arity other than ZeroToOne or OneToMany."""
    for container_type, path in _over_elements_of_environment(environment):
        if not isinstance(
            container_type, (aas_types.Submodel, aas_types.SubmodelElementCollection)
        ):
            continue

        for elem, elem_path in _over_elements_of_container_non_recursive(
            container_type, path
        ):
            cardinality_qualifiers = []

            for qualifier in elem.over_qualifiers_or_empty():
                if qualifier.type in _CARDINALITY_QUALIFIERS_SET:
                    cardinality_qualifiers.append(qualifier)

            if len(cardinality_qualifiers) == 0:
                continue

            assert len(cardinality_qualifiers) == 1

            cardinality_qualifier = cardinality_qualifiers[0]

            if cardinality_qualifier.value not in ["ZeroToMany", "OneToMany"]:
                yield Message(
                    level=Level.WARNING,
                    text=f"Found arity of {cardinality_qualifier.value} for non-list element.",
                    path=elem_path,
                )


def _verify(environment: aas_types.Environment) -> Iterator[Message]:
    """Assess the quality of the submodel templates in the given environment."""
    yield from _verify_all_submodels_are_templates(environment)

    yield from _verify_references_and_definitions(environment)

    yield from _verify_whitespace(environment)

    yield from _verify_deprecated_types(environment)

    yield from _verify_qualifier_types(environment)

    yield from _verify_arbitrary_types(environment)

    yield from _verify_arity_of_non_list_elements(environment)


def main(prog: str) -> int:
    """
    Execute the main routine.

    :param prog: name of the program to be displayed in the help
    :return: exit code
    """
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument(
        "--version", help="Show the current version and exit", action="store_true"
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=str,
        help=(
            "One or more paths to AAS environments in JSON or XML to verify; "
            "glob patterns including `*` and `**` are allowed."
        ),
    )

    # NOTE (mristin):
    # The module ``argparse`` is not flexible enough to understand special options such
    # as ``--version`` so we manually hard-wire.
    if "--version" in sys.argv and "--help" not in sys.argv:
        print(aas_smt_lint.__version__)
        return 0

    args = parser.parse_args()

    paths: List[pathlib.Path] = []
    for path_str in args.paths:
        matched_paths = glob.glob(path_str, recursive=True)

        if matched_paths:
            paths.extend([pathlib.Path(p) for p in matched_paths])

    had_error = False
    for path in paths:
        if not path.exists():
            print(f"Path does not exist: {path}", file=sys.stderr)
            had_error = True
        elif not path.is_file():
            print(f"Path does not point to a file: {path}", file=sys.stderr)
            had_error = True

    if had_error:
        return 1

    environment, id_to_path, errors = _read_and_merge_environments(paths=paths)
    if errors is not None:
        print(
            f"Failed to read and merge file(s) into a single environment:\n"
            f"{bullet_points(errors)}",
            file=sys.stderr,
        )
        return 1

    assert environment is not None
    assert id_to_path is not None

    caught_something = False

    for message in _verify(environment):
        identifier = message.path.fragments[0]
        assert isinstance(
            identifier, str
        ), "Paths must always start with an identifiable"

        fs_path = id_to_path[identifier]

        print(
            f"{message.level} {fs_path}: {message.path} {message.text}", file=sys.stderr
        )
        caught_something = True

    if caught_something:
        return 1

    return 0


def entry_point() -> int:
    """Provide an entry point for a console script."""
    return main(prog="aas-smt-lint")


if __name__ == "__main__":
    sys.exit(main(prog="aas-smt-lint"))
