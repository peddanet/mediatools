# Changelog

All notable changes are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Three stray test log files (`o1`, `o2`, `o3`) committed by mistake are
  removed.
- Shrink databases are matched by file name within their directory
  instead of the stored absolute path. Databases written before the
  collection was moved were not recognised: `progress` reported every
  video as todo, and `shrink` would have encoded processed files again.

### Added
- `shrink`: plan first (report files, volume, estimated time; ask once),
  `--order largest|smallest|path` (default: largest first),
  `--min-seconds S` to leave out short clips, progress line with
  remaining time, measured speed kept in `state/shrink_throughput.json`.
- `shrink`: first Ctrl+C finishes the current file and stops, second
  Ctrl+C aborts it without touching the original; ffmpeg runs in its own
  process group.
- `shrink`: journal marker around the copy-back; an interrupted
  replacement (power failure) is completed by the next run.
- `progress`: "size changed since" category, database rows without a
  matching file, time estimate.
- Privacy gate `tools/privacy_scan.py` in `tools/check.sh`: refuses
  tracked files, paths or commit messages containing locally listed terms;
  locally listed allowed phrases are exempt.
- CI workflow can be started manually (`workflow_dispatch`).

### Added
- `triage`: counts the matching videos first and reports batches, total
  length and an estimated review time before asking to start; durations
  are cached in `state/triage_durations.tsv`; `-y` skips only the start
  question.
- `triage`: a removed video is replaced by an empty placeholder (0 bytes)
  under its original name, so downloaders that skip existing names do not
  fetch it again (`--no-placeholder` to disable); removals are recorded in
  `state/triage_discarded.tsv`.
- `config --set SECTION.KEY=VALUE`: change defaults without an editor;
  comments in the file are kept.
- `[sync] source`: own default source for `sync` (e.g. the home
  directory), independent of `[collection] root`.

### Changed
- `rename-db OLD NEW [PATH]`: both names on the command line, no
  confirmation question; NEW is stored as `[shrink] db_name`.
- `tools/check.sh` stops with an installation hint if ffmpeg, rsync or
  rsnapshot are missing instead of skipping integration tests.
- Wording of the triage instructions: close a window = remove, leave it
  open = keep.
- 0-byte files are ignored by `shrink`, `progress`, `flatten` and `triage`.
- `sync` uses `rsync --min-size=1`, so a placeholder never replaces the
  full file on the backup.

### Fixed
- A path given after options (`rename-db OLD --dry-run PATH`) was rejected
  as "unrecognized argument".

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
