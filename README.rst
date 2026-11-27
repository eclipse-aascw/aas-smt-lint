************
aas-smt-lint
************

.. image:: https://github.com/eclipse-aascw/aas-smt-lint/actions/workflows/ci.yml/badge.svg
    :target: https://github.com/eclipse-aascw/aas-smt-lint/actions/workflows/ci.yml
    :alt: Continuous integration

Assess automatically the quality of AAS Submodel Templates.

Installation
============
Check out the repository and change to its directory.

Create a virtual environment:

.. code-block::

    python -m venv venv

Activate it (on Windows):

.. code-block::

    venv/Scripts/activate

... or on Unix:

.. code-block::

    source venv/bin/activate

Install the dependencies:

.. code-block::

    pip3 install .

You can now run ``aas-smt-lint`` (see ``--help`` section below).

``--help``
==========

.. Help starts: aas-smt-lint --help
.. code-block::

    usage: aas-smt-lint [-h] [--version] [--aas-version {3.0,3.1}]
                        paths [paths ...]

    Assess automatically the quality of AAS Submodel Templates.

    positional arguments:
      paths                 One or more paths to AAS environments in JSON or XML
                            to verify; glob patterns including `*` and `**` are
                            allowed.

    options:
      -h, --help            show this help message and exit
      --version             Show the current version and exit
      --aas-version {3.0,3.1}
                            Version of the AAS meta-model to verify against
                            (default: 3.1). XML files are checked to be in the
                            namespace of this version, while JSON files do not
                            indicate the version, so they are simply assumed to be
                            of this version.

.. Help ends: aas-smt-lint --help

Contributing
============
See [CONTRIBUTING.rst](CONTRIBUTING.rst).
