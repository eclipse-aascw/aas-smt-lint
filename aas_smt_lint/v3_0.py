# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""
Assess the quality of AAS Submodel Templates for one version of the meta-model.

The version of the meta-model is given by the imported aas-core SDK. The modules
for the different versions are kept identical apart from the SDK imports and
the explicitly listed exceptions, see ``tests/test_version_modules_in_sync.py``.
"""

import itertools
import json
import pathlib
import sys
from typing import (
    List,
    Tuple,
    Optional,
    Dict,
    Sequence,
    Iterator,
    Union,
    get_args,
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

from aas_smt_lint.common import (
    bullet_points,
    Level,
    Path,
    Message,
    GlobalIdentifier,
    human_readable_attribute_name,
    has_surrounding_whitespace,
    has_whitespace_in_middle,
    DEPRECATED_CARDINALITY_QUALIFIERS_SET,
    TEMPLATE_QUALIFIERS,
    CARDINALITY_QUALIFIERS_SET,
    Cardinality,
    CARDINALITY_BY_VALUE,
    split_id_short_in_parts,
    is_singular,
    sniff_xml_namespace,
)


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
        # NOTE (mristin):
        # The XML namespace tells us the version of the meta-model, so we can give
        # a clear message instead of a confusing de-serialization error if the user
        # selected the wrong version.
        namespace = sniff_xml_namespace(text)
        if namespace is not None and namespace != aas_xmlization.NAMESPACE:
            return None, (
                f"The root element is in the XML namespace {namespace!r}, "
                f"but expected the namespace {aas_xmlization.NAMESPACE!r}; "
                f"did you specify the correct AAS version?"
            )

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
            if has_surrounding_whitespace(attr_val):
                yield (
                    f"Surrounding whitespace in "
                    f"{human_readable_attribute_name(attr_name)}"
                )


def _check_reference_for_whitespace(
    reference: Optional[aas_types.Reference],
) -> Iterator[str]:
    """
    Check that no reference key contains whitespaces.

    Yield message texts if there is surrounding whitespace.
    """
    if reference is None:
        return

    for key in reference.keys:
        if has_surrounding_whitespace(key.value):
            yield "Surrounding whitespace in a key value."
        if has_whitespace_in_middle(key.value):
            yield "Whitespace in the middle of a key value."


def _check_submodel_element_references(
    elem: aas_types.SubmodelElement,
) -> Iterator[str]:
    """
    Check all references of a submodel element that they contain no offending whitespaces.

    Yield message texts if there are offending whitespace.
    """

    references: List[Optional[aas_types.Reference]] = [elem.semantic_id]

    references.extend(elem.supplemental_semantic_ids or [])

    for qualifier in elem.qualifiers or []:
        references.append(qualifier.semantic_id)

    if isinstance(elem, aas_types.ReferenceElement):
        references.append(elem.value)
    elif isinstance(elem, aas_types.SubmodelElementList):
        references.append(elem.semantic_id_list_element)

    for reference in references:
        yield from _check_reference_for_whitespace(reference)


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

        for message in _check_submodel_element_references(that):
            yield Message(
                level=Level.WARNING,
                text=message,
                path=Path(self._path[:]),
            )

    def check_submodel(self, that: aas_types.Submodel) -> Iterator[Message]:
        """Check the attributes of ``that`` submodel and recurse into its elements."""
        self._path.append(that.id)
        try:
            for text in _check_attributes_for_surrounding_whitespace(that):
                yield Message(level=Level.WARNING, text=text, path=Path(self._path[:]))

            if has_whitespace_in_middle(that.id):
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
            if qualifier.type in DEPRECATED_CARDINALITY_QUALIFIERS_SET:
                yield Message(
                    level=Level.WARNING,
                    text=f'Use of deprecated qualifier "{qualifier.type}".',
                    path=path,
                )


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
                in TEMPLATE_QUALIFIERS | DEPRECATED_CARDINALITY_QUALIFIERS_SET
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


def _is_arbitrary_element(element: aas_types.SubmodelElement) -> bool:
    # NOTE (empwilli 2025-10-29):
    # Not all SMTs properly implement arbitrary annotations, so we are a little bit
    # lenient for the semantic ID check and check the ID-short, as well.
    match element.semantic_id:
        case aas_types.Reference(
            type=aas_types.ReferenceTypes.EXTERNAL_REFERENCE,
            keys=[
                aas_types.Key(
                    type=aas_types.KeyTypes.GLOBAL_REFERENCE,
                    value=value,
                )
            ],
        ) if value.startswith("https://admin-shell.io/SMT/General/Arbitrary"):
            return True
        case _ if "arbitrary" in (element.id_short or "").lower():
            return True
        case _:
            return False


def verify_arbitraries(environment: aas_types.Environment) -> Iterator[Message]:
    """Warn for elements marked as arbitrary."""
    for submodel_element, path in _over_elements_of_environment(environment):
        if _is_arbitrary_element(submodel_element):
            yield Message(
                level=Level.WARNING,
                text="Found annotation of an arbitrary element.",
                path=path,
            )


@ensure(
    lambda result: not (result[0] is not None) or (result[1] is None),
    "No error if result",
)
def _determine_cardinality(
    element: aas_types.SubmodelElement,
) -> Tuple[Optional[Cardinality], Optional[str]]:
    """
    Determine the cardinality based on one of the cardinality qualifiers.

    Return the cardinality (or none, if not specified), or an error, if the cardinality
    could not be defined (*e.g.*, in case of multiple cardinality qualifiers).
    """
    cardinality_qualifiers = [
        qualifier
        for qualifier in element.over_qualifiers_or_empty()
        if qualifier.type in CARDINALITY_QUALIFIERS_SET
    ]

    if len(cardinality_qualifiers) == 0:
        return None, None

    elif len(cardinality_qualifiers) == 1:
        cardinality_qualifier = cardinality_qualifiers[0]

        if cardinality_qualifier.value is None:
            return None, "Unexpected cardinality qualifier without value"

        cardinality = CARDINALITY_BY_VALUE.get(cardinality_qualifier.value, None)
        if cardinality is None:
            return None, f"Unexpected cardinality value {cardinality_qualifier.value!r}"

        return cardinality, None

    elif len(cardinality_qualifiers) > 1:
        return None, "Unexpected multiple cardinality qualifiers"

    else:
        raise AssertionError(f"Unexpected case {len(cardinality_qualifiers)=}")


def _verify_cardinality_recursively(
    container: Union[aas_types.Submodel, aas_types.SubmodelElementCollection],
    prefix: Path,
) -> Iterator[Message]:
    for elem, path in _over_elements_of_container_non_recursive(
        container,
        prefix,
    ):
        cardinality, cardinality_error = _determine_cardinality(elem)
        if cardinality_error is not None:
            yield Message(
                level=Level.ERROR,
                text=(f"Failed to determine cardinality: {cardinality_error}"),
                path=path,
            )

        if cardinality is not None:
            # NOTE (empwilli):
            # While we discourage the use of arbitrary elements, it makes sense to use a
            # ZeroToOne/OneToMany qualifier for them. As such we don't bother checking
            # for them.
            if _is_arbitrary_element(elem):
                pass

            else:
                if cardinality not in (Cardinality.ZERO_TO_ONE, Cardinality.ONE):
                    yield Message(
                        level=Level.WARNING,
                        text=(
                            f"Unexpected cardinality of {cardinality.value!r}; "
                            f"only {Cardinality.ZERO_TO_ONE.value!r} or "
                            f"{Cardinality.ONE.value!r} are expected "
                            f"for an element of a composition"
                        ),
                        path=path,
                    )

        if isinstance(elem, aas_types.SubmodelElementList):
            # NOTE (mristin):
            # We only check the cardinality if there is a clear template item element.
            # We do not know what to do in light of many template items.
            if elem.value is not None and len(elem.value) == 1:
                child = elem.value[0]

                child_cardinality, child_cardinality_error = _determine_cardinality(
                    child
                )
                if child_cardinality_error is not None:
                    yield Message(
                        level=Level.ERROR,
                        text=child_cardinality_error,
                        path=Path(list(path.fragments) + [0]),
                    )
                else:
                    if (
                        child_cardinality is not None
                        and child_cardinality is not Cardinality.ONE_TO_MANY
                    ):
                        yield Message(
                            level=Level.WARNING,
                            text=(
                                f"Expected cardinality "
                                f"of {Cardinality.ONE_TO_MANY.value!r} for a template "
                                f"item of a list, but got {child_cardinality.value!r}"
                            ),
                            path=Path(fragments=list(path.fragments) + [0]),
                        )

            for i, child in enumerate(elem.over_value_or_empty()):
                if isinstance(child, aas_types.SubmodelElementCollection):
                    yield from _verify_cardinality_recursively(
                        container=child,
                        prefix=Path(fragments=list(path.fragments) + [i]),
                    )

        elif isinstance(elem, aas_types.SubmodelElementCollection):
            yield from _verify_cardinality_recursively(container=elem, prefix=path)

        else:
            # NOTE (mristin):
            # Recursion is only possible on lists and collections.
            pass


def verify_cardinality(
    environment: aas_types.Environment,
) -> Iterator[Message]:
    """
    Check that all elements have valid cardinality set.

    We enforce that all children of submodels and collections can have either
    ``ZeroToOne`` or ``One`` cardinality set.

    When it comes to lists, the rules are still a bit blurry. For now, we only check
    lists containing a single prototype item element, and enforce that the item element
    has cardinality ``OneToMany``.

    To our current knowledge, there is no use of ``ZeroToMany`` qualifier.
    """
    for submodel in environment.over_submodels_or_empty():
        yield from _verify_cardinality_recursively(
            container=submodel, prefix=Path([submodel.id])
        )


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

        human_readable_id_short = " ".join(split_id_short_in_parts(element.id_short))

        singular = is_singular(human_readable_id_short, engine)
        if isinstance(element, aas_types.SubmodelElementList):
            if singular:
                yield Message(
                    level=Level.WARNING,
                    text=(
                        f"Expected the ID-short in plural for aggregations, "
                        f"but got singular: {element.id_short!r}"
                    ),
                    path=path,
                )

        else:
            if not singular:
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


def _verify_value_type(
    environment: aas_types.Environment,
    bad_value_type: aas_types.DataTypeDefXSD,
    message_text: str,
) -> Iterator[Message]:
    """
    Warn that we should replace ``bad_value_type`` with something else.

    This check considers Properties and Ranges.
    """
    for element, path in _over_elements_of_environment(environment):
        if not isinstance(element, (aas_types.Property, aas_types.Range)):
            continue

        if element.value_type is bad_value_type:
            yield Message(level=Level.WARNING, text=message_text, path=path)


def verify_no_xs_decimal(environment: aas_types.Environment) -> Iterator[Message]:
    """Warn that ``xs:decimal`` is used as a value type."""
    yield from _verify_value_type(
        environment,
        bad_value_type=aas_types.DataTypeDefXSD.DECIMAL,
        message_text=(
            f"Consider using value type {aas_types.DataTypeDefXSD.DOUBLE.value!r} "
            f"instead of {aas_types.DataTypeDefXSD.DECIMAL.value!r} since the latter "
            f"implies arbitrary precision, while the former is fixed precision."
        ),
    )


def verify_no_xs_integer(environment: aas_types.Environment) -> Iterator[Message]:
    """Warn that ``xs:integer`` is used as a value type."""
    yield from _verify_value_type(
        environment,
        bad_value_type=aas_types.DataTypeDefXSD.INTEGER,
        message_text=(
            f"Consider using value type {aas_types.DataTypeDefXSD.INT.value!r} or "
            f"{aas_types.DataTypeDefXSD.LONG.value!r}"
            f"instead of {aas_types.DataTypeDefXSD.INTEGER.value!r} since the latter "
            f"implies arbitrary precision, while the former two are fixed precision."
        ),
    )


def verify_no_xs_float(environment: aas_types.Environment) -> Iterator[Message]:
    """Warn that ``xs:float`` is used as a value type."""
    yield from _verify_value_type(
        environment,
        bad_value_type=aas_types.DataTypeDefXSD.FLOAT,
        message_text=(
            f"Consider using value type {aas_types.DataTypeDefXSD.DOUBLE.value!r} "
            f"instead of {aas_types.DataTypeDefXSD.FLOAT.value!r} since the latter "
            f"requires 32-bit representation, which can incur conversion overhead "
            f"on modern hardware operating with 64-bits."
        ),
    )


def verify(environment: aas_types.Environment) -> Iterator[Message]:
    """Assess the quality of the submodel templates in the given environment."""
    yield from verify_all_submodels_are_templates(environment)

    yield from verify_redefinitions(environment)

    yield from verify_whitespace(environment)

    yield from verify_deprecated(environment)

    yield from verify_qualifier_kinds(environment)

    yield from verify_arbitraries(environment)

    yield from verify_cardinality(environment)

    yield from verify_id_shorts_for_plural_singular(environment)

    yield from verify_dangling_references(environment)

    yield from verify_no_xs_integer(environment)

    yield from verify_no_xs_decimal(environment)

    yield from verify_no_xs_float(environment)


def run(paths: Sequence[pathlib.Path]) -> int:
    """
    Read, verify and lint the AAS environments at ``paths``.

    The messages are printed to STDERR.

    :param paths: to the files containing the AAS environments as JSON or XML
    :return: exit code
    """
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
