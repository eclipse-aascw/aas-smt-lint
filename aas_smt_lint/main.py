# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""Assess automatically the quality of AAS Submodel Templates."""

import argparse
import enum
import glob
import pathlib
import sys
from typing import List

from typing_extensions import assert_never

import aas_smt_lint
import aas_smt_lint.v3_0
import aas_smt_lint.v3_1

assert aas_smt_lint.__doc__ == __doc__


class AasVersion(enum.Enum):
    """Enumerate the supported versions of the AAS meta-model."""

    V3_0 = "3.0"
    V3_1 = "3.1"


DEFAULT_AAS_VERSION = AasVersion.V3_1


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
        "--aas-version",
        help=(
            f"Version of the AAS meta-model to verify against "
            f"(default: {DEFAULT_AAS_VERSION.value}). "
            f"XML files are checked to be in the namespace of this version, "
            f"while JSON files do not indicate the version, so they are "
            f"simply assumed to be of this version."
        ),
        choices=[aas_version.value for aas_version in AasVersion],
        default=DEFAULT_AAS_VERSION.value,
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

    aas_version = AasVersion(args.aas_version)

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

    if aas_version is AasVersion.V3_0:
        return aas_smt_lint.v3_0.run(paths)
    elif aas_version is AasVersion.V3_1:
        return aas_smt_lint.v3_1.run(paths)
    else:
        assert_never(aas_version)


def entry_point() -> int:
    """Provide an entry point for a console script."""
    return main(prog="aas-smt-lint")


if __name__ == "__main__":
    sys.exit(main(prog="aas-smt-lint"))
