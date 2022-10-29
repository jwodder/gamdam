v0.4.0 (2022-10-29)
-------------------
- Fix how URLs set with `git-annex registerurl` are displayed in log messages
- Support Python 3.11
- Improve formatting of certain multiline git-annex error messages
- Get tests to pass on macOS and Windows
    - Fix an issue with setting metadata on Windows or in an unlocked branch
        - Metadata is no longer set on files that aren't annexed
- Warn if metadata or URLs couldn't be attached to a downloaded file due to the
  file not being assigned a git-annex key

v0.3.1 (2022-10-24)
-------------------
- Fix a minor bug that could cause `git-annex init` to be run in an
  already-initialized git-annex repository
- Fix `--failures` option (broken in 0.3.0)

v0.3.0 (2022-08-05)
-------------------
- The `subscriber` argument to `download()` is now an optional
  `anyio.abc.ObjectSendStream[DownloadResult]`

v0.2.0 (2022-07-17)
-------------------
- Update for changes to the `registerurl` command in git-annex 10.20220222
    - `gamdam` now requires git-annex v10.20220222 or higher to run
- Switch from trio to anyio

v0.1.0 (2021-10-24)
-------------------
Initial release
