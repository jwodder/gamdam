.. image:: https://www.repostatus.org/badges/latest/inactive.svg
    :target: https://www.repostatus.org/#inactive
    :alt: Project Status: Inactive – The project has reached a stable, usable
          state but is no longer being actively developed; support/maintenance
          will be provided as time allows.

.. image:: https://github.com/jwodder/gamdam/workflows/Test/badge.svg?branch=master
    :target: https://github.com/jwodder/gamdam/actions?workflow=Test
    :alt: CI Status

.. image:: https://codecov.io/gh/jwodder/gamdam/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/jwodder/gamdam

.. image:: https://img.shields.io/pypi/pyversions/gamdam.svg
    :target: https://pypi.org/project/gamdam/

.. image:: https://img.shields.io/github/license/jwodder/gamdam.svg
    :target: https://opensource.org/licenses/MIT
    :alt: MIT License

`GitHub <https://github.com/jwodder/gamdam>`_
| `PyPI <https://pypi.org/project/gamdam/>`_
| `Issues <https://github.com/jwodder/gamdam/issues>`_
| `Changelog <https://github.com/jwodder/gamdam/blob/master/CHANGELOG.md>`_

``gamdam`` is the Git-Annex Mass Downloader and Metadata-er.  It takes a stream
of JSON Lines describing what to download and what metadata each file has,
downloads them in parallel to a git-annex_ repository, attaches the metadata
using git-annex's metadata facilities, and commits the results.

This program was written as an experiment/proof-of-concept for a larger program
and is now only minimally maintained.

.. _git-annex: https://git-annex.branchable.com


Installation
============
``gamdam`` requires Python 3.8 or higher.  Just use `pip
<https://pip.pypa.io>`_ for Python 3 (You have pip, right?) to install
``gamdam`` and its dependencies::

    python3 -m pip install gamdam

``gamdam`` also requires ``git-annex`` v10.20220222 or higher to be installed
separately in order to run.


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

--addurl-opts OPTIONS           Extra options to pass to the ``git-annex
                                addurl`` command.  Note that multiple options &
                                arguments need to be quoted as a single string,
                                which must also use proper shell quoting
                                internally; e.g., ``--addurl-opts="--user-agent
                                'gamdam via git-annex'"``.

-C DIR, --chdir DIR             The directory in which to download files;
                                defaults to the current directory.  If the
                                directory does not exist, it will be created.
                                If the directory does not belong to a Git or
                                git-annex repository, it will be initialized as
                                one.

-F FILE, --failures FILE        If any files fail to download, write their
                                input records back out to ``FILE``

-J INT, --jobs INT              Number of parallel jobs for ``git-annex
                                addurl`` to use; by default, the process is
                                instructed to use one job per CPU core.

-l LEVEL, --log-level LEVEL     Set the log level to the given value.  Possible
                                values are "``CRITICAL``", "``ERROR``",
                                "``WARNING``", "``INFO``", "``DEBUG``" (all
                                case-insensitive) and their Python integer
                                equivalents.  [default: ``INFO``]

-m TEXT, --message TEXT         The commit message to use when saving.  This
                                may contain a ``{downloaded}`` placeholder
                                which will be replaced with the number of files
                                successfully downloaded.

--no-save-on-fail               Don't commit the downloaded files if any files
                                failed to download

--save, --no-save               Whether to commit the downloaded files once
                                they've all been downloaded  [default:
                                ``--save``]


Input Format
------------

Input is a series of JSON objects, one per line (a.k.a. "JSON Lines").  Each
object has the following fields:

``url``
    *(required)* A URL to download

``path``
    *(required)* A relative path where the contents of the URL should be saved.
    If an entry with a given path is encountered while another entry with the
    same path is being downloaded, the later entry is discarded, and a warning
    is emitted.

    If a file already exists at a given path, ``git-annex`` will try to
    register the URL as an additional location for the file, failing if the
    resource at the URL is not the same size as the extant file.

``metadata``
    A collection of metadata in the form used by ``git-annex metadata``, i.e.,
    a ``dict`` mapping key names to lists of string values.

``extra_urls``
    A list of alternative URLs for the resource, to be attached to the
    downloaded file with ``git-annex registerurl``.  Note that this operation
    can only be performed on files tracked by git-annex; if you, say, have
    configured git-annex to not track text files, then any text files
    downloaded will not have any alternative URLs registered.

If a given input line is invalid, it is discarded, and an error message is
emitted.


Library Usage
=============

``gamdam`` can also be used as a Python library.  It exports the following:

.. code:: python

    async def download(
        repo: pathlib.Path,
        objects: AsyncIterator[Downloadable],
        jobs: Optional[int] = None,
        addurl_opts: Optional[List[str]] = None,
        subscriber: Optional[anyio.abc.ObjectSendStream[DownloadResult]] = None,
    ) -> Report

Download the items yielded by the async iterator ``objects`` to the directory
``repo`` (which must be part of a git-annex repository) and set their metadata.
``jobs`` is the number of parallel jobs for the ``git-annex addurl`` process to
use; a value of ``None`` means to use one job per CPU core.  ``addurl_opts``
contains any additional arguments to append to the ``git-annex addurl``
command.

If ``subscriber`` is supplied, it will be sent a ``DownloadResult`` (see below)
for each completed download, both successful and failed.  This can be used to
implement custom post-processing of downloads.

.. code:: python

   class Downloadable(pydantic.BaseModel):
       path: pathlib.Path
       url: pydantic.AnyHttpUrl
       metadata: Optional[Dict[str, List[str]]] = None
       extra_urls: Optional[List[pydantic.AnyHttpUrl]] = None

``Downloadable`` is a pydantic_ model used to represent files to download; see
`Input Format`_ above for the meanings of the fields.

.. code:: python

    class DownloadResult(pydantic.BaseModel):
        downloadable: Downloadable
        success: bool
        key: Optional[str] = None
        error_messages: Optional[List[str]] = None

``DownloadResult`` is a pydantic_ model used to represent a completed download.
It contains the original ``Downloadable``, a flag to indicate download success,
the downloaded file's git-annex key (only set if the download was successful
and the file is tracked by git-annex) and any error messages from the addurl
process (only set if the download failed).

.. code:: python

    @dataclass
    class Report:
        downloaded: int
        failed: int

``Report`` is used as the return value of ``download()``; it contains the
number of files successfully downloaded and the number of failed downloads.

.. _pydantic: https://pydantic-docs.helpmanual.io
