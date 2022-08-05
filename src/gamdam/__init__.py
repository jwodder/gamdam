"""
Git-Annex Mass Downloader and Metadata-er

``gamdam`` is the Git-Annex Mass Downloader and Metadata-er.  It takes a stream
of JSON Lines describing what to download and what metadata each file has,
downloads them in parallel to a git-annex_ repository, attaches the metadata
using git-annex's metadata facilities, and commits the results.

This program was written as an experiment/proof-of-concept for a larger program
and is now only minimally maintained.

.. _git-annex: https://git-annex.branchable.com

Visit <https://github.com/jwodder/gamdam> for more information.
"""

__version__ = "0.3.0"
__author__ = "John Thorvald Wodder II"
__author_email__ = "gamdam@varonathe.org"
__license__ = "MIT"
__url__ = "https://github.com/jwodder/gamdam"

from .core import Downloadable, DownloadResult, Report, download

__all__ = ["DownloadResult", "Downloadable", "Report", "download"]
