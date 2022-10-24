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
