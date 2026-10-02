# Release versions

`VERSION` is the public version shared by the CLI, firmware, and release manifest.
Keep the stable format compatible with already-installed updaters:

- Stable: `MAJOR.MINOR.PATCH`, for example `0.1.31` or `0.1.100`.
- Development: `MAJOR.MINOR.PATCH-dev.N`, for example `0.1.31-dev.1`.
- Tags add a `v` prefix, for example `v0.1.31` or `v0.1.31-dev.1`.

Each component contains digits, with no fixed width. Other suffixes such as
`-prod` and `-beta.1`, and build metadata such as `+build`, are rejected.

Run `python3 release/check-version.py` before building. CI runs this check on
every change, and the release workflow also validates the tag and manifest.
Artifact assembly and integrity verification enforce the same version rules.

Development releases are published with `--prerelease --latest=false`. Normal
`tinytouch update` uses GitHub's latest stable release and never selects a dev
release. To test development firmware, select Development firmware in the web
flasher. A dev CLI also uses the stable update channel.

Do not rename the stable format without a migration plan for installed clients.
CLI compatibility tests check that the repository version and future versions
with wider numeric components are accepted by the updater. The old
`0.1.25-prod` updater requires the legacy `-prod` suffix and needs a one-time
CLI reinstall to enter the current stable channel.
