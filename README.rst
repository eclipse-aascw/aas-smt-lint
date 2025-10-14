************
aas-smt-lint
************

.. image:: https://github.com/mristin/aas-smt-lint/actions/workflows/ci.yml/badge.svg
    :target: https://github.com/mristin/aas-smt-lint/actions/workflows/ci.yml
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

    usage: aas-smt-lint [-h] [--version] paths [paths ...]

    Assess automatically the quality of AAS Submodel Templates.

    positional arguments:
      paths       One or more paths to AAS environments in JSON or XML to verify;
                  glob patterns including `*` and `**` are allowed.

    options:
      -h, --help  show this help message and exit
      --version   Show the current version and exit

.. Help ends: aas-smt-lint --help

Contributing
============
See [CONTRIBUTING.rst](CONTRIBUTING.rst).
