"""Assess automatically the quality of AAS Submodel Templates."""

import argparse
import enum
import glob
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
    Final,
    Union,
    get_args,
    cast,
    MutableMapping,
    Mapping,
)

from icontract import ensure, require
import aas_core3.jsonization as aas_jsonization
import aas_core3.types as aas_types
import aas_core3.xmlization as aas_xmlization
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


def _over_elements_of_container(
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
                yield from _over_elements_of_container(container=element, prefix=path)
    elif isinstance(container, aas_types.SubmodelElementList):
        for i, element in enumerate(container.over_value_or_empty()):
            path = Path(fragments=list(prefix.fragments) + [i])

            yield element, path

            if isinstance(element, _ContainerAsTuple):
                yield from _over_elements_of_container(container=element, prefix=path)
    elif isinstance(container, aas_types.SubmodelElementCollection):
        for element in container.over_value_or_empty():
            assert (
                element.id_short is not None
            ), "All elements of a Submodel Element Collection must have an ID-short."
            path = Path(fragments=list(prefix.fragments) + [element.id_short])

            yield element, path

            if isinstance(element, _ContainerAsTuple):
                yield from _over_elements_of_container(container=element, prefix=path)
    else:
        # noinspection PyTypeChecker
        assert_never(container)


def _over_elements_of_environment(
    environment: aas_types.Environment,
) -> Iterator[Tuple[aas_types.SubmodelElement, Path]]:
    """Iterate over all the submodel elements contained in the environment."""
    for submodel in environment.over_submodels_or_empty():
        prefix = Path(fragments=[])

        yield from _over_elements_of_container(container=submodel, prefix=prefix)


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


def _verify(environment: aas_types.Environment) -> Iterator[Message]:
    """Assess the quality of the submodel templates in the given environment."""
    yield from _verify_all_submodels_are_templates(environment)

    yield from _verify_references_and_definitions(environment)


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
