.. image:: http://www.repostatus.org/badges/latest/wip.svg
    :target: http://www.repostatus.org/#wip
    :alt: Project Status: WIP — Initial development is in progress, but there
          has not yet been a stable, usable release suitable for the public.

.. image:: https://img.shields.io/github/license/jwodder/gamdam.svg
    :target: https://opensource.org/licenses/MIT
    :alt: MIT License

`GitHub <https://github.com/jwodder/gamdam>`_
| `Issues <https://github.com/jwodder/gamdam/issues>`_

``gamdam`` is the Git-Annex Mass Downloader and Metadata-er.  It takes a stream
of JSON Lines describing what to download and what metadata each file has,
downloads them in parallel to a git-annex_ repository, attaches the metadata
using git-annex's metadata facilities, and commits the results.

This program was written as an experiment/proof-of-concept for a larger
program.  It is my first time using trio_, so there may be some oddness in the
code.

.. _git-annex: https://git-annex.branchable.com
.. _trio: https://github.com/python-trio/trio


Installation
============
``gamdam`` requires Python 3.8 or higher.  Just use `pip
<https://pip.pypa.io>`_ for Python 3 (You have pip, right?) to install
``gamdam`` and its dependencies::

    python3 -m pip install git+https://github.com/jwodder/gamdam.git


Usage
=====

::

    gamdam [<options>] [<input-file>]

``gamdam`` reads a series of JSON entries from a file (or from standard input
if no file is specified) following the `input format`_ described below.  It
feeds the URLs and output paths to ``git-annex addurl``, and once each file has
finished downloading, it attaches any listed metadata and extra URLs using
``git-annex metadata`` and ``git-annex registerurl``, respectively.

Options
-------

-C DIR, --chdir DIR             The directory in which to download files;
                                defaults to the current directory.  If the
                                directory does not exist, it will be created.
                                If the directory does not belong to a Git or
                                git-annex repository, it will be initialized as
                                one.

-J INT, --jobs INT              Number of parallel jobs for ``git-annex
                                addurl`` to use  [default: 10]

-l LEVEL, --log-level LEVEL     Set the log level to the given value.  Possible
                                values are "``CRITICAL``", "``ERROR``",
                                "``WARNING``", "``INFO``", "``DEBUG``" (all
                                case-insensitive) and their Python integer
                                equivalents.  [default: INFO]

-m TEXT, --message TEXT         The commit message to use when saving.  This
                                may contain a ``{downloaded}`` placeholder
                                which will be replaced with the number of files
                                successfully downloaded.

--save, --no-save               Whether to commit the downloaded files once
                                they've all been downloaded  [default: --save]


Input Format
------------

Input is a series of JSON objects, one per line (a.k.a. "JSON Lines").  Each
object has the following fields:

``url``
    *(required)* A URL to download

``path``
    *(required)* A relative path where the contents of the URL should be saved.
    If a file already exists at this path, ``git-annex`` will try to register
    the URL as an additional location for the file, failing if the resource at
    the URL is not the same size as the extant file.

``metadata``
    A collection of metadata in the form used by ``git-annex metadata``, i.e.,
    a ``dict`` mapping key names to lists of string values.

``extra_urls``
    A list of alternative URLs for the resource, to be attached to the
    downloaded file with ``git-annex registerurl``.  Note that this operation
    can only be performed on files tracked by git-annex; if you, say, have
    configured git-annex to not track text files, then any text files
    downloaded will not have any alternative URLs registered.

If a given input line is invalid, it is discarded, and a warning is emitted.
