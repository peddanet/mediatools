# Changelog

All notable changes are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-03

First public release.

### Subcommands
- `shrink`: HEVC (libx265) re-encode in place, file name and inode kept,
  validated by exit code, video stream and duration; per-directory SQLite
  database with precedence over the codec check; `--keep-copies` review
  mode.
- `progress`: strictly read-only done/todo report.
- `triage`: short clips in batches, one player per clip, closing a window
  discards the clip together with its `_flat` hard links (trash by default).
- `flatten`: per-unit `_flat` hard-link views, byte-exact name limit
  (`statvfs`), four naming levels, `_index.tsv`, plans with `--undo`,
  `--prune`.
- `sync`: rsync `-aHu --no-inc-recursive` with dry-run summary, disk picker,
  hard-link test, report of target-only files; never deletes.
- `snapshot`: versioned backups with rsnapshot on a removable disk -
  `check`, `adopt` (existing copy becomes the first snapshot without copying
  data, plus permanent `stand0`), `init`, `run` (dry-run probe, space check,
  deletion limit, due-level rotation), `status`, `install-trigger` (systemd
  user unit started by the disk mount).
- `config`: local INI file `mediatools.ini` for all machine-specific paths
  and names; every command falls back to `[collection] root`.
- `rename-db`: one-time renaming of shrink databases and their backups to
  `[shrink] db_name`, never overwrites.

### Quality
- One class per module, Google-style docstrings throughout.
- Quality gates in `tools/check.sh` (pre-push hook and CI): pycodestyle,
  pylint 10.00/10, mypy, bandit, pytest with branch coverage >= 80 % (exact
  comparison) and resource leaks as errors.
- git-flow branching: `main` releases only, `develop` integration,
  `feature/*` via pull requests; CI checks that release tags lie on `main`
  and match `__version__`.
