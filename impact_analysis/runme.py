# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""Analyze quantitatively our linting on official IDTA submodel templates."""

import argparse
import collections
import itertools
import os
import pathlib
import pickle
import sys
from typing import (
    Sequence,
    Final,
    Mapping,
    MutableMapping,
    cast,
    Callable,
    Iterator,
    List,
)

import fsdag
import matplotlib.pyplot
import matplotlib.figure
from aas_core3 import (
    types as aas_types,
    verification as aas_verification,
)
from icontract import require

import aas_smt_lint.main
from aas_smt_lint.common import bullet_points, NonNegativeInt


# pylint: disable=missing-docstring


class ReadAndDeserializeEnvironment(fsdag.Node[aas_types.Environment]):
    def __init__(
        self, serialization_path: pathlib.Path, pickled_path: pathlib.Path
    ) -> None:
        super().__init__()
        self.serialized_path = serialization_path
        self.pickled_path = pickled_path

    def _path(self) -> pathlib.Path:
        return self.pickled_path

    def _save(self, artefact: aas_types.Environment) -> None:
        self.pickled_path.parent.mkdir(exist_ok=True, parents=True)
        with self.pickled_path.open("wb") as fid:
            # noinspection PyTypeChecker
            pickle.dump(artefact, fid)

    def _load(self) -> aas_types.Environment:
        with self.pickled_path.open("rb") as fid:
            result = pickle.load(fid)
            assert isinstance(result, aas_types.Environment)
            return result

    def _compute(self) -> aas_types.Environment:
        environment, read_or_deserialize_error = (
            aas_smt_lint.main.read_and_deserialize_environment(self.serialized_path)
        )

        if read_or_deserialize_error is not None:
            raise RuntimeError(read_or_deserialize_error)

        assert environment is not None
        return environment


class CountMetaModelViolations(fsdag.Node[int]):
    def __init__(
        self,
        environment_node: fsdag.Node[aas_types.Environment],
        count_path: pathlib.Path,
    ) -> None:
        super().__init__()
        self.environment_node = environment_node
        self.count_path = count_path

    def _path(self) -> pathlib.Path:
        return self.count_path

    def _save(self, artefact: int) -> None:
        self._path().parent.mkdir(exist_ok=True, parents=True)
        self._path().write_text(str(artefact), encoding="utf-8")

    def _load(self) -> int:
        return int(self._path().read_text(encoding="utf-8"))

    def _compute(self) -> int:
        return sum(1 for _ in aas_verification.verify(self.environment_node.resolve()))


class MergedEnvironmentAndIdMapping:
    environment: Final[aas_types.Environment]
    id_to_path: Final[Mapping[str, pathlib.Path]]

    def __init__(
        self, environment: aas_types.Environment, id_to_path: Mapping[str, pathlib.Path]
    ) -> None:
        self.environment = environment
        self.id_to_path = id_to_path


class MergeEnvironments(fsdag.Node[MergedEnvironmentAndIdMapping]):
    # fmt: off
    @require(
        lambda environment_nodes, paths:
        len(environment_nodes) == len(paths),
        "Paths must coincide with environments."
    )
    # fmt: on
    def __init__(
        self,
        environment_nodes: Sequence[fsdag.Node[aas_types.Environment]],
        paths: Sequence[pathlib.Path],
        pickled_path: pathlib.Path,
    ) -> None:
        super().__init__()
        self.environment_nodes = environment_nodes
        self.paths = paths
        self.pickled_path = pickled_path

    def _path(self) -> pathlib.Path:
        return self.pickled_path

    def _save(self, artefact: MergedEnvironmentAndIdMapping) -> None:
        self.pickled_path.parent.mkdir(exist_ok=True, parents=True)
        with self.pickled_path.open("wb") as fid:
            # noinspection PyTypeChecker
            pickle.dump(artefact, fid)

    def _load(self) -> MergedEnvironmentAndIdMapping:
        with self.pickled_path.open("rb") as fid:
            result = pickle.load(fid)
            assert isinstance(result, MergedEnvironmentAndIdMapping)
            return result

    def _compute(self) -> MergedEnvironmentAndIdMapping:
        big_environment, id_to_path, merge_errors = (
            aas_smt_lint.main.merge_environments(
                environments=[
                    environment_node.resolve()
                    for environment_node in self.environment_nodes
                ],
                paths=self.paths,
            )
        )

        if merge_errors is not None:
            raise RuntimeError(
                f"Failed to merge file(s) into a single environment:\n"
                f"{bullet_points(merge_errors)}"
            )

        assert big_environment is not None
        assert id_to_path is not None

        return MergedEnvironmentAndIdMapping(
            environment=big_environment, id_to_path=id_to_path
        )


class VerificationNode(
    fsdag.Node[Mapping[pathlib.Path, Sequence[aas_smt_lint.main.Message]]]
):
    def __init__(
        self,
        verification_func: Callable[
            [aas_types.Environment], Iterator[aas_smt_lint.main.Message]
        ],
        merged_environment_node: fsdag.Node[MergedEnvironmentAndIdMapping],
        messages_path: pathlib.Path,
    ):
        super().__init__()
        self.verification_func = verification_func
        self.merged_environment_node = merged_environment_node
        self.messages_path = messages_path

    def _path(self) -> pathlib.Path:
        return self.messages_path

    def _save(
        self, artefact: Mapping[pathlib.Path, Sequence[aas_smt_lint.main.Message]]
    ) -> None:
        with self.messages_path.open("wb") as fid:
            # noinspection PyTypeChecker
            pickle.dump(artefact, fid)

    def _load(self) -> Mapping[pathlib.Path, Sequence[aas_smt_lint.main.Message]]:
        with self.messages_path.open("rb") as fid:
            # noinspection PyTypeChecker
            result = pickle.load(fid)

            assert isinstance(result, collections.abc.Mapping)
            assert all(isinstance(key, pathlib.Path) for key in result)
            assert all(
                isinstance(value, collections.abc.Sequence) for value in result.values()
            )
            assert all(
                isinstance(message, aas_smt_lint.main.Message)
                for messages in result.values()
                for message in messages
            )

            return cast(
                Mapping[pathlib.Path, Sequence[aas_smt_lint.main.Message]], result
            )

    def _compute(self) -> Mapping[pathlib.Path, Sequence[aas_smt_lint.main.Message]]:
        merged_environment = self.merged_environment_node.resolve()

        path_to_messages = (
            dict()
        )  # type: MutableMapping[pathlib.Path, List[aas_smt_lint.main.Message]]

        # NOTE (mristin):
        # We explicitly record empty messages for submodel templates in order to track
        # their statistics as well.
        for path in merged_environment.id_to_path.values():
            path_to_messages[path] = []

        for message in self.verification_func(merged_environment.environment):
            assert len(message.path.fragments) >= 1 and isinstance(
                message.path.fragments[0], str
            ), "The first fragment of a message path must be a submodel identifier."
            submodel_id = message.path.fragments[0]

            # NOTE (mristin):
            # The ``submodel_template_path`` here represents a path to the submodel
            # template file, not an ID-short path to the element.
            smt_path = merged_environment.id_to_path[submodel_id]

            path_to_messages[smt_path].append(message)

        return path_to_messages


class StringifyVerificationsNode(fsdag.Node[str]):
    def __init__(
        self, verification_node: VerificationNode, text_path: pathlib.Path
    ) -> None:
        super().__init__()
        self.verification_node = verification_node
        self.text_path = text_path

    def _path(self) -> pathlib.Path:
        return self.text_path

    def _save(self, artefact: str) -> None:
        self._path().write_text(artefact, encoding="utf-8")

    def _load(self) -> str:
        return self._path().read_text(encoding="utf-8")

    def _compute(self) -> str:
        verification = self.verification_node.resolve()

        blocks = []  # type: List[str]
        for path in sorted(verification.keys()):
            messages = verification[path]

            if len(messages) == 0:
                blocks.append(f"{path.name}: 0")
            else:
                points = [
                    f"{message.level.value} {message.path}: {message.text}"
                    for message in messages
                ]
                blocks.append(f"{path.name} {len(points)}:\n{bullet_points(points)}")

        return "\n".join(blocks)


class CountVerifications(fsdag.Node[Sequence[NonNegativeInt]]):
    def __init__(
        self, verification_node: VerificationNode, counts_path: pathlib.Path
    ) -> None:
        super().__init__()
        self.verification_node = verification_node
        self.counts_path = counts_path

    def _path(self) -> pathlib.Path:
        return self.counts_path

    def _save(self, artefact: Sequence[NonNegativeInt]) -> None:
        with self._path().open("wb") as fid:
            # noinspection PyTypeChecker
            pickle.dump(artefact, fid)

    def _load(self) -> Sequence[NonNegativeInt]:
        with self._path().open("rb") as fid:
            result = pickle.load(fid)
            assert isinstance(result, collections.abc.Sequence)
            assert all(isinstance(item, int) for item in result)
            assert all(item >= 0 for item in result)

            return cast(Sequence[NonNegativeInt], result)

    def _compute(self) -> Sequence[NonNegativeInt]:
        path_to_messages = self.verification_node.resolve()

        return sorted(
            NonNegativeInt(len(path_to_messages[smt_path]))
            for smt_path in sorted(path_to_messages.keys())
        )


def render_statistics(counts: Sequence[int]) -> str:
    """
    Render statistics for a table.

    >>> render_statistics([2, 2, 5, 0, 1])
    '2.0 ± 1.9, p25=1.0, p50=2.0, p75=2.0, max. 5'
    """
    histogram = [
        sum(1 for count in counts if count == 0),
        sum(1 for count in counts if 1 <= count <= 5),
        sum(1 for count in counts if 6 <= count <= 10),
        sum(1 for count in counts if count > 10),
    ]

    return (
        f"{len(counts)} sample(s), "
        f"histogram (0, 1-5, 6-10, >10): {histogram}, total: {sum(counts)}"
    )


class DrawHistogram(fsdag.Node[matplotlib.figure.Figure]):
    def __init__(
        self,
        counts_node: CountVerifications,
        figure_path: pathlib.Path,
        image_path: pathlib.Path,
    ) -> None:
        super().__init__()
        self.counts_node = counts_node
        self.figure_path = figure_path
        self.image_path = image_path

    def _path(self) -> pathlib.Path:
        return self.figure_path

    def _save(self, artefact: matplotlib.figure.Figure) -> None:
        with self._path().open("wb") as fid:
            # noinspection PyTypeChecker
            pickle.dump(artefact, fid)

        artefact.savefig(str(self.image_path))

    def _load(self) -> matplotlib.figure.Figure:
        with self._path().open("rb") as fid:
            # noinspection PyTypeChecker
            result = pickle.load(fid)
            assert isinstance(result, matplotlib.figure.Figure)
            return result

    def _compute(self) -> matplotlib.figure.Figure:
        counts: Sequence[NonNegativeInt] = self.counts_node.resolve()
        histogram = [
            sum(1 for count in counts if count == 0),
            sum(1 for count in counts if 1 <= count <= 5),
            sum(1 for count in counts if 6 <= count <= 10),
            sum(1 for count in counts if count > 10),
        ]

        labels = ["0", "1–5", "6–10", ">10"]
        colors = ["white", "lightgrey", "darkgrey", "black"]

        fig, ax = matplotlib.pyplot.subplots(figsize=(3.5, 1.0))

        ax.bar(
            labels,
            histogram,
            color=colors,
            edgecolor="black",
            linewidth=0.8,
            width=0.8,
        )

        # Remove axes, ticks, grid, and labels for a clean, compact look
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(False)

        fig.tight_layout()
        return fig


def main() -> int:
    """Execute the main routine."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.parse_args()

    this_dir = pathlib.Path(os.path.realpath(__file__)).parent
    repo_root = this_dir.parent

    data_dir = this_dir / "data"
    data_dir.mkdir(exist_ok=True, parents=True)

    # NOTE (mristin):
    # We have to take the de-duplicated environments in order to be able to merge them
    # into one big environment.
    templates_dir = repo_root / "test_data" / "idta_submodel_templates" / "deduplicated"
    paths = sorted(templates_dir.glob("**/*.json"))

    if len(paths) == 0:
        print(f"No submodel template files found in {templates_dir}", file=sys.stderr)
        return 1

    environment_nodes = [
        ReadAndDeserializeEnvironment(
            serialization_path=path,
            pickled_path=data_dir / "pickled_environments" / f"{path.name}.pickle",
        )
        for path in paths
    ]

    assert len(environment_nodes) == len(
        paths
    ), "From this point on, we assume that the paths and the environments coincide."

    print(f"Number of considered templates: {len(paths)}")

    meta_model_violations = [
        CountMetaModelViolations(
            environment_node=environment_node,
            count_path=data_dir / "meta_model_violations" / path.name,
        ).resolve()
        for environment_node, path in zip(environment_nodes, paths)
        # NOTE (mristin):
        # We exclude violations in concept descriptions as they skew the statistics
        # too much, so we consider them outliers.
        if path.name != "environment_with_concept_descriptions.json"
    ]

    print(f"Meta-model violations: " f"{render_statistics(meta_model_violations)}")

    merged_environment_node = MergeEnvironments(
        environment_nodes=environment_nodes,
        paths=paths,
        pickled_path=data_dir / "merged_environment_and_id_mapping.pickle",
    )

    for verification_func, identifier, title in [
        (
            aas_smt_lint.main.verify_redefinitions,
            "redefinition",
            "Structure re-definitions",
        ),
        (
            aas_smt_lint.main.verify_whitespace,
            "unexpected_whitespace",
            "Unexpected whitespace",
        ),
        (
            aas_smt_lint.main.verify_deprecated,
            "deprecated_types",
            "Deprecated types",
        ),
        (
            aas_smt_lint.main.verify_qualifier_kinds,
            "incorrect_qualifier_kinds",
            "Incorrect qualifier kinds",
        ),
        (
            aas_smt_lint.main.verify_arbitraries,
            "arbitrary_structures",
            "Arbitrary structures",
        ),
        (
            aas_smt_lint.main.verify_cardinality,
            "unexpected_cardinality",
            "Unexpected cardinalities",
        ),
        (
            aas_smt_lint.main.verify_id_shorts_for_plural_singular,
            "unexpected_id_shorts",
            "Unexpected ID-shorts",
        ),
        (
            aas_smt_lint.main.verify_dangling_references,
            "dangling_references",
            "Dangling references",
        ),
        (
            lambda environment: itertools.chain(
                aas_smt_lint.main.verify_no_xs_integer(environment),
                aas_smt_lint.main.verify_no_xs_decimal(environment),
                aas_smt_lint.main.verify_no_xs_float(environment),
            ),
            "unexpected_precision",
            "Unexpected numeric precision",
        ),
    ]:
        messages_by_path_node = VerificationNode(
            verification_func=verification_func,
            merged_environment_node=merged_environment_node,
            messages_path=data_dir / f"{identifier}_messages_by_path.pickle",
        )

        StringifyVerificationsNode(
            verification_node=messages_by_path_node,
            text_path=data_dir / f"{identifier}_messages_by_path.txt",
        ).resolve()

        counts_node = CountVerifications(
            verification_node=messages_by_path_node,
            counts_path=data_dir / f"{identifier}_counts.pickle",
        )

        (data_dir / "figures").mkdir(exist_ok=True)
        DrawHistogram(
            counts_node=counts_node,
            figure_path=data_dir / "figures" / f"{identifier}.fig",
            image_path=data_dir / "figures" / f"{identifier}.png",
        ).resolve()

        print(f"{title}: {render_statistics(counts_node.resolve())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
