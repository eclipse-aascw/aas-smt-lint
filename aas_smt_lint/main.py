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
    Set,
)

from aas_core3 import (
    jsonization as aas_jsonization,
    types as aas_types,
    xmlization as aas_xmlization,
    verification as aas_verification,
)
import inflect
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


# NOTE (mristin):
# We split _deserialize_environment and _read_and_deserialize_environment both for
# easier testing and for keeping the code minimal in the main routine.


def read_and_deserialize_environment(
    path: pathlib.Path,
) -> Tuple[Optional[aas_types.Environment], Optional[str]]:
    """
    Read the AAS environment from the given path and de-serialize it from JSON or XML.

    The de-serialization format is based on the first non-whitespace character.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exception:
        return None, f"Failed to read from {path}: {exception}"

    environment, deserialize_error = _deserialize_environment(text)
    if deserialize_error is not None:
        return None, f"Failed to de-serialize {path}: {deserialize_error}"

    return environment, None


@require(
    lambda environments, paths: len(environments) == len(paths),
    "Paths must coincide with environments.",
)
# fmt: off
@ensure(
    lambda result:
    (result[0] is not None and result[1] is not None) ^ (result[2] is not None),
    "Either a result or an error",
)
# fmt: on
@ensure(
    lambda result: not (result[2] is not None) or (len(result[2]) > 0),
    "There must be errors if errors is not None",
)
def merge_environments(
    environments: Sequence[aas_types.Environment], paths: Sequence[pathlib.Path]
) -> Tuple[
    Optional[aas_types.Environment],
    Optional[Mapping[str, pathlib.Path]],
    Optional[List[str]],
]:
    """
    Merge the AAS environments from the ``paths`` into one big environment.

    Return the merged AAS environment and identifier to path map, or errors if any.
    """
    merged_environment = aas_types.Environment()

    id_to_path: Dict[str, pathlib.Path] = dict()

    errors: List[str] = []

    assert len(environments) == len(paths)
    for environment, path in zip(environments, paths):
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


def verify_all_submodels_are_templates(
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
        prefix = Path(fragments=[submodel.id])

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


def verify_redefinitions(
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

        # NOTE (mristin):
        # We ignore the elements with arbitrary structures.
        if global_id == "https://admin-shell.io/SMT/General/Arbitrary":
            continue

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


def verify_whitespace(environment: aas_types.Environment) -> Iterator[Message]:
    """Verify no unexpected whitespace in the attributes of submodels."""
    checker = _WhitespaceChecker()
    for submodel in environment.over_submodels_or_empty():
        yield from checker.check_submodel(submodel)


# endregion Check whitespace


_DEPRECATED_CARDINALITY_QUALIFIERS_SET = {"Multiplicity", "Cardinality"}


def verify_deprecated(environment: aas_types.Environment) -> Iterator[Message]:
    """
    Verify that no deprecated features are used.

    The standard has been around for some time now and some of its features are,
    as of now, considered as deprecated. This looks for the use of deprecated features
    and reports them as warnings.

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
                    text=f'Use of deprecated qualifier "{qualifier.type}".',
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


def verify_qualifier_kinds(environment: aas_types.Environment) -> Iterator[Message]:
    """Check that qualifiers have the correct kind set."""
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
            ) and (
                qualifier.kind_or_default()
                is not aas_types.QualifierKind.TEMPLATE_QUALIFIER
            ):
                yield Message(
                    level=Level.ERROR,
                    text=(
                        f"Wrong qualifier kind for qualifier {qualifier.type!r}, "
                        f"got {qualifier.kind_or_default().value!r}, "
                        f"but expected "
                        f"{aas_types.QualifierKind.TEMPLATE_QUALIFIER.value!r}."
                    ),
                    path=path,
                )


_FOUND_ARBITRARY_ELEMENT_MESSAGE_TEXT: Final[str] = (
    "Found annotation of an arbitrary element."
)


def verify_arbitraries(environment: aas_types.Environment) -> Iterator[Message]:
    """Warn for elements marked as arbitrary."""
    for submodel_element, path in _over_elements_of_environment(environment):
        # NOTE (empwilli 2025-10-29):
        # Not all SMTs properly implement arbitrary annotations, so we are a little bit
        # lenient for the semantic ID check and check the ID-short, as well.
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
                    text=_FOUND_ARBITRARY_ELEMENT_MESSAGE_TEXT,
                    path=path,
                )
            case _ if "arbitrary" in (submodel_element.id_short or "").lower():
                yield Message(
                    level=Level.WARNING,
                    text=_FOUND_ARBITRARY_ELEMENT_MESSAGE_TEXT,
                    path=path,
                )
            case _:
                continue


_CARDINALITY_QUALIFIERS_SET = {
    "SMT/Cardinality"
} | _DEPRECATED_CARDINALITY_QUALIFIERS_SET


def verify_arity_of_non_list_elements(
    environment: aas_types.Environment,
) -> Iterator[Message]:
    """Check for non-list elements with arity not ZeroToOne or OneToMany."""
    for container, path in _over_elements_of_environment(environment):
        if not isinstance(
            container, (aas_types.Submodel, aas_types.SubmodelElementCollection)
        ):
            continue

        for elem, elem_path in _over_elements_of_container_non_recursive(
            container, path
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
                    text=(
                        f"Found arity of {cardinality_qualifier.value} "
                        f"for non-list element."
                    ),
                    path=elem_path,
                )


_CAMEL_CASE_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+")


def _split_id_short_in_parts(text: str) -> List[str]:
    """
    Split on underscores and camel case.

    >>> _split_id_short_in_parts('do_something')
    ['do', 'something']

    >>> _split_id_short_in_parts('camelCaseURL')
    ['camel', 'Case', 'URL']

    >>> _split_id_short_in_parts('HTTPRequestParser')
    ['HTTP', 'Request', 'Parser']

    >>> _split_id_short_in_parts('simpleXMLParser')
    ['simple', 'XML', 'Parser']

    >>> _split_id_short_in_parts('B10')
    ['B', '10']

    >>> _split_id_short_in_parts('XML2Text')
    ['XML', '2', 'Text']
    """
    parts = text.split("_")

    result = []  # type: List[str]

    for part in parts:
        camel_case_parts = _CAMEL_CASE_PART_RE.findall(part)
        result.extend(camel_case_parts)

    return result


@require(lambda text: len(text.strip()) > 0, "The text must not be empty")
def _is_singular(text: str, engine: inflect.engine) -> bool:
    """
    Check whether the ``text`` is a noun in plural.

    >>> engine_for_doctest = inflect.engine()
    >>> _is_singular('contact information', engine_for_doctest)
    True

    >>> _is_singular('keys', engine_for_doctest)
    False

    >>> _is_singular('Endpoint Metadata', engine_for_doctest)
    True

    >>> _is_singular('Address', engine_for_doctest)
    True

    >>> _is_singular('Address of contact', engine_for_doctest)
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


def verify_id_shorts_for_plural_singular(
    environment: aas_types.Environment,
) -> Iterator[Message]:
    """Check that ID shorts of lists are in plural and all the other are singular."""
    engine = inflect.engine()

    for element, path in _over_elements_of_environment(environment):
        if element.id_short is None:
            continue

        if len(element.id_short.strip()) == 0:
            yield Message(
                level=Level.ERROR,
                text=(
                    f"ID-short must either be set to a non-empty string or omitted, "
                    f"but we got an empty string: {element.id_short!r}"
                ),
                path=path,
            )
            continue

        human_readable_id_short = " ".join(_split_id_short_in_parts(element.id_short))

        is_singular = _is_singular(human_readable_id_short, engine)
        if isinstance(element, aas_types.SubmodelElementList):
            if is_singular:
                yield Message(
                    level=Level.WARNING,
                    text=(
                        f"Expected the ID-short in plural for aggregations, "
                        f"but got singular: {element.id_short!r}"
                    ),
                    path=path,
                )

        else:
            if not is_singular:
                yield Message(
                    level=Level.WARNING,
                    text=(
                        f"Expected the ID-short in singular for non-aggregations, "
                        f"but got plural for {element.__class__.__name__}: "
                        f"{element.id_short!r}"
                    ),
                    path=path,
                )


def verify_dangling_references(environment: aas_types.Environment) -> Iterator[Message]:
    """Check that all collections without children are defined elsewhere."""
    set_of_ids_of_defined_structures = set()  # type: Set[str]

    # region Collect IDs of the definitions

    for submodel in environment.over_submodels_or_empty():
        set_of_ids_of_defined_structures.add(submodel.id)

    for element, path in _over_elements_of_environment(environment):
        if not isinstance(element, aas_types.SubmodelElementCollection):
            continue

        # NOTE (mristin):
        # If there are no children, we assume that it is a reference.
        if element.value is None or len(element.value) == 0:
            continue

        global_id, error = _try_global_id(element)
        if error is not None:
            yield Message(level=Level.ERROR, text=error, path=path)
            continue

        assert global_id is not None
        set_of_ids_of_defined_structures.add(global_id)

    # endregion

    # region Check collections as references

    for element, path in _over_elements_of_environment(environment):
        if not isinstance(element, aas_types.SubmodelElementCollection):
            continue

        # NOTE (mristin):
        # We assume that only collections without elements are references.
        if element.value is not None and len(element.value) > 0:
            continue

        global_id, error = _try_global_id(element)
        if error is not None:
            yield Message(level=Level.ERROR, text=error, path=path)
            continue

        assert global_id is not None

        if global_id not in set_of_ids_of_defined_structures:
            yield Message(
                level=Level.ERROR,
                text=(
                    f"The collection references the structure {global_id} "
                    f"through its semantic ID, but the structure has not been "
                    f"previously defined (neither as submodel template "
                    f"nor as embedded submodel element collection)."
                ),
                path=path,
            )

    # endregion


def verify(environment: aas_types.Environment) -> Iterator[Message]:
    """Assess the quality of the submodel templates in the given environment."""
    yield from verify_all_submodels_are_templates(environment)

    yield from verify_redefinitions(environment)

    yield from verify_whitespace(environment)

    yield from verify_deprecated(environment)

    yield from verify_qualifier_kinds(environment)

    yield from verify_arbitraries(environment)

    yield from verify_arity_of_non_list_elements(environment)

    yield from verify_id_shorts_for_plural_singular(environment)

    yield from verify_dangling_references(environment)


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

    had_path_error = False
    for path in paths:
        if not path.exists():
            print(f"Path does not exist: {path}", file=sys.stderr)
            had_path_error = True
        elif not path.is_file():
            print(f"Path does not point to a file: {path}", file=sys.stderr)
            had_path_error = True

    if had_path_error:
        return 1

    failed_reading_and_deserializing = False
    environments = []  # type: List[aas_types.Environment]
    for path in paths:
        environment, read_or_deserialize_error = read_and_deserialize_environment(path)

        if read_or_deserialize_error is not None:
            failed_reading_and_deserializing = True
            print(read_or_deserialize_error, file=sys.stderr)
            continue

        assert environment is not None
        environments.append(environment)

    if failed_reading_and_deserializing:
        return 1

    caught_something = False

    assert len(environments) == len(paths)
    for environment, path in zip(environments, paths):
        # NOTE (mristin):
        # We separate the meta-model violations from linting errors as their paths are
        # incompatible. The meta-model violations consider all the model instances of
        # an environment (including all the attributes), while our linting only considers
        # submodels and submodel elements.

        violations_as_bullets = bullet_points(
            f"{violation.path}: {violation.cause}"
            for violation in aas_verification.verify(environment)
        )
        if len(violations_as_bullets) > 0:
            # NOTE (mristin):
            # We use the XML namespace as an identifier of the meta-model. This applies
            # here both for JSON and XML de-serialization even though JSON
            # de-serailization does not indicate the namespace in the serialization
            # format.
            print(
                f"""\
There were one or more violations of the AAS meta-model {aas_xmlization.NAMESPACE} in {path}:
{violations_as_bullets}""",
                file=sys.stderr,
            )
            caught_something = True

    big_environment, id_to_path, merge_errors = merge_environments(
        environments=environments, paths=paths
    )
    if merge_errors is not None:
        print(
            f"Failed to merge file(s) into a single environment:\n"
            f"{bullet_points(merge_errors)}",
            file=sys.stderr,
        )
        return 1

    assert big_environment is not None
    assert id_to_path is not None

    for message in verify(big_environment):
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
