# SPDX-FileCopyrightText: 2025 aas-smt-lint AUTHORS
#
# SPDX-License-Identifier: MIT

"""Check that the version in ``pyproject.toml`` and ``__init__.py`` coincide."""

import importlib.metadata
import sys

import aas_smt_lint


def main() -> None:
    """Execute the main routine."""
    init_version = aas_smt_lint.__version__

    pyproject_toml_version = importlib.metadata.version("aas-smt-lint")

    if init_version != pyproject_toml_version:
        print(
            f"The version in {aas_smt_lint.__file__} is: {init_version!r}, "
            f"but the version in pyproject.toml is: {pyproject_toml_version!r}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
