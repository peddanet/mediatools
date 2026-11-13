# Changelog

All notable changes are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.6.0] - 2026-11-13

### Added
- `mediatools shrink --jobs N` (default `[shrink] jobs`, else 1): encodes N
  videos at the same time. Only ffmpeg runs in worker threads; checks,
  replacement, databases and the display stay in the main thread. Hard
  links of one file never run together; stop and reload start no further
  file and wait for the running ones; a second Ctrl+C aborts all of them.
  The display draws one file line per running encode.
- Filters `--min-size SIZE` (e.g. `300M`, decimal units) and
  `--newer-than YYYY-MM-DD` (modification time); the plan summary counts
  what each filter left out.
- Ledger `~/.local/share/mediatools/ledger.tsv` (mode 600, append only):
  one line per finished video with session, status, sizes, times, playing
  time, modification time and path. Filled in once from the result tables
  of the existing shrink logs, so nothing counted before is lost.
- Jobs: a job is the root plus the filters; CRF, order and `--jobs` are
  settings of a run. `state/jobs.json` remembers every job with its
  settings and remainder. A plain `mediatools shrink` on a terminal lists
  the jobs with progress bars and continues the chosen one (`n`: new job
  with the defaults); a later `reload` continues that very job.
- GUI: list of the shrink jobs with progress in the shrink tab (selecting
  fills the form, *Continue* or a double click starts it); menu *View* with
  text size (Ctrl++, Ctrl+-, Ctrl+0) and a light or dark scheme
  (`[gui] scale`, `[gui] theme`), a flat style on ttk `clam` with an accent
  colour; a bar offers *Restart now* when a newer release is installed.
- `mediatools where` reports the state of the application menu entry.

### Changed
- The first progress line counts the **job** (root and filters), not all
  shrink logs: a run on a subtree or with other filters shows its own
  percentage and ETA; it is labelled `job`. The space line shows the
  session and the total from the ledger (`total since <date>`).
- Each run writes its job and settings as JSON into the log
  (`shrink job: {...}`).
- `--install-desktop` remembers the wish (`[gui] desktop_entry = yes`);
  the GUI start and `mediatools update` then put back a missing or
  outdated entry and its icons.

## [1.5.1] - 2026-11-13

### Changed
- CI: pull requests are tested on Python 3.10 (oldest supported) and 3.14
  (newest) instead of 3.10, 3.12, 3.13, 3.14; the pre-push hook still
  tests the PC's version. Required checks: `gates (3.10)`, `gates (3.14)`.
- CI: a tag push runs only `release-check` (tag on `main`, tag = version);
  the gates no longer repeat the run of the pull request for the same tree.
  One full gate run less per release.
- CI: pip downloads are cached by `actions/setup-python` (key: hash of
  `pyproject.toml`); `actions/checkout@v5` (Node.js 24) removes the
  deprecation annotation of v4.
- `mediatools update` reads the Python versions it announces from the
  workflow file.

### Fixed
- `mediatools update`: a patch that does not fit onto `main` (usually
  because an earlier release is missing in the download folder) no longer
  leaves a half-applied `git am` session behind that blocked the next run
  at "update main"; the session is aborted, the local hotfix branch
  removed and the likely cause printed. A session left by an interrupted
  run is cleared at the start.

## [1.5.0] - 2026-11-13

### Changed
- Folders follow the XDG Base Directory Specification: configuration
  (`mediatools.ini`, privacy lists) in `~/.config/mediatools`, logs and
  runtime state (`logs/`, `state/`, `reports/`, `snapshot/`) in
  `~/.local/state/mediatools`, kept data (`plans/`, `releases/`) in
  `~/.local/share/mediatools`. Absolute `XDG_CONFIG_HOME`,
  `XDG_STATE_HOME` and `XDG_DATA_HOME` are honoured. The installed release
  stays in `~/scripts/mediatools.d/venv`. `MEDIATOOLS_HOME` still keeps
  everything in one folder with the old names.
- The privacy lists are `privacy/denylist.txt` and `privacy/allowlist.txt`
  in the configuration folder (folder mode 700, files 600);
  `tools/privacy_scan.py` finds them through `DataDir`.
- `mediatools update` writes its log to the state folder and keeps applied
  archives in the data folder.
- README and handbook describe mediatools by its purpose - keeping a
  dynamically growing video collection manageable, on disk space first -
  without naming any platform; example folder names are neutral.

### Added
- `mediatools where`: every folder and file location and the layout in
  use. `mediatools where --migrate` moves the data of releases before 1.5.0
  out of `~/scripts/mediatools.d` (refuses while any mediatools process
  runs or a target exists; leaves `WHERE-IS-THE-DATA.txt`). Until then the
  old folder stays in use.
- `mediatools privacy [--edit deny|allow]`: paths and sizes of the privacy
  lists; `--edit` opens a list in `$EDITOR` and creates it if missing.
- GUI: tab *where* (confirmation only with `--migrate`); the *files* tab
  reads `plans/` from the data folder; About names all three folders.

## [1.4.2] - 2026-11-13

### Changed
- First progress line: `ETA | since <date>: <read> GiB read (-> <after>
  GiB after shrinking), <to go> GiB to go | <n> files done, <m> to go`, all
  source bytes of the whole job since the first shrink log; the percentage
  is read / (read + to go). Read and files done include all earlier runs
  (from the shrink logs), so they no longer start at zero after a start or
  a `mediatools reload`.
  The ETA was and is computed from the source GiB to go and the measured
  speed.

## [1.4.1] - 2026-11-12

### Added
- Own icon (`gui/icons/mediatools.svg`, PNG 64 and 256 px): window icon of
  the GUI, and with `mediatools gui --install-desktop` (or the GUI menu
  *mediatools > Add to application menu*) copied to
  `~/.local/share/icons/hicolor` (scalable and 256x256). The menu entry
  uses `Icon=mediatools` and `StartupWMClass=Mediatools`, so the dock or
  panel shows the running window under the entry; `--remove-desktop`
  removes entry and icons.

## [1.4.0] - 2026-11-12

### Added
- `mediatools savings [--by session|day]`: freed space per shrink session
  or per calendar day, with files, read, written, freed and output share,
  and the total since the first shrunk file. Source: the result tables of
  the shrink logs (no new data file; works for all logs back to the first
  release with result tables).
- GUI tab *savings*: the same table, per session or per day, newest first.

### Changed
- Space line of the progress display and of the GUI shrink tab:
  `session since <start>` (everything shrunk since the session started; a
  session survives `mediatools reload`) and `since <date>` (all sessions)
  instead of `this run` (reset by every reload) and `overall`
  (`shrink_throughput.json`, without a start date). Without a running
  shrink the GUI shows the last session.

## [1.3.0] - 2026-11-12

### Added
- `mediatools gui`: graphical interface (Tk) with a tab per task. The shrink
  tab shows the running shrink (state, release, progress lines with a
  progress bar, space of all runs, log end) with *Stop* and *Reload*, and
  starts new runs. Every other command has a form with a field per option,
  the command line to copy, *Run* and *Preview* (`--dry-run`, `--list`,
  `--report-only`), and a live output pane in which questions are
  answered; *Cancel* acts like Ctrl+C. A configuration editor checks
  `mediatools.ini` before saving; a file browser shows logs, reports and
  plans and hands a plan to flatten for an undo. Forms are read from the
  argument parser, so the GUI always offers the same options as the
  command line; commands that may change files ask for a confirmation
  first; the last values per form are kept in `state/gui.json`.
- `mediatools gui --install-desktop` / `--remove-desktop`: entry in the
  application menu (`~/.local/share/applications/mediatools.desktop`).

### Changed
- Test environment: the GUI tests need `python3-tk` and a display;
  `tools/check.sh` starts itself under `xvfb-run` when no display is
  available, and the CI installs `xvfb`.

## [1.2.16] - 2026-11-10

### Added
- Third progress line `space`: GiB read (source of the replaced
  originals), written (their shrunk versions) and freed (the difference),
  for this run and overall. Overall adds the totals of
  `state/shrink_throughput.json` (kept since 1.2.1). `mediatools attach`
  shows the line as well; the end of a run logs both views with the output
  share in percent.

### Changed
- The GiB of the first progress line are labelled `GiB source`. They are
  the volume read and encoded in this run, not the space freed, and start
  at zero after every start, restart or `mediatools reload`.

## [1.2.15] - 2026-11-10

### Added
- `mediatools status`: whether a shrink runs (PID), the release it runs
  with (last `mediatools X (pid N)` line of its own log) and since when,
  the installed release, what to do (`up to date`, `mediatools reload`,
  or stop and restart for releases before 1.2.10), the log path and
  pending reload/stop requests. Exit code 0 while a shrink runs, 1
  otherwise. `mediatools update` prints it after installing.

## [1.2.14] - 2026-11-10

### Fixed
- `mediatools update` no longer stops at a short loss of the connection
  to GitHub (seen as "error connecting to api.github.com" while waiting
  for CI 1/3). Reading `gh` calls are repeated up to five times, 10 s
  apart; an interrupted watch of a CI run is resumed until GitHub reports
  the run as completed. A red run still stops the update at once.
- The failure message of the script named exit code 0 instead of the
  real one (already corrected in 1.2.13; listed for completeness).

### Documentation
- The CI guide is part of the repository: `docs/ci-guide.md` and
  `docs/ci-guide.pdf` (workflow, runners, Python versions, containers,
  `gh` and the GitHub API, branch protection, lane diagrams). It is built
  with the other documents and checked by the quality gates.

## [1.2.13] - 2026-11-10

### Changed
- `mediatools update` announces every run of the quality gates before it
  waits for it: local run of the pre-push hook (branch, Python version)
  and CI run on GitHub (`CI 1/3` ... `CI 3/3`, trigger, commit, what it
  checks, why, URL). A table of all CI runs with their result closes the
  output, also when a step fails.
- CI starts only for pull requests into `main`/`develop` and for release
  tags. The runs on every push to `main` and `develop` are gone: they
  tested trees already tested by the pull request (5 instead of 3 runs per
  hotfix before).
- CI matrix extended by Python 3.14; `actions/setup-python` v5 -> v6.
  Python 3.10 (end of life October 2026) stays as long as
  `requires-python >= 3.10` holds.

### Documentation
- New section "CI runs: which run checks what" in `docs/developer.md`
  with a diagram of the feature, release and hotfix flows.

## [1.2.12] - 2026-11-10

### Changed
- Every encoded output is decoded **completely** before it replaces the
  original (`DecodeCheck.verify`, `ffmpeg -v error -i OUT -f null -` at
  nice 19). A single error line, a non-zero exit, a missing ffmpeg or a
  timeout (`max(600 s, 2 x duration)`) makes the file FAILED and keeps the
  original. Before, only the first 10 s of the output were decoded.
  Measured cost: 4.0 s full decode against 52.3 s encode for a 30 s
  1080p clip (7.7 %; 5.2 % more than the former 10-s check).

## [1.2.11] - 2026-11-10

### Fixed
- Originals restored over their shrunk version (e.g. by a sync from the
  backup disk in the wrong direction) were never encoded again, because
  the database matches by file name. A recorded video that is now at least
  1.2 times as large as every recorded size (`ShrinkDatabase.status` =
  `regrown`) and whose video is not HEVC is planned again; the plan
  summary reports the number. `progress` lists such videos separately
  (`progress_regrown_*.txt`) and adds them to the volume to encode.

## [1.2.10] - 2026-11-09

### Added
- Release switch during a run: `mediatools reload` (also called by
  `mediatools update` after the installation) asks a running shrink to
  continue with the newly installed release. Between two files it checks
  that the release starts (`--version`), closes table and summary and
  replaces itself (`os.execv`, same PID, session, console and log file)
  by the new release with the same options plus `--foreground -y`. The new
  release plans again from databases and caches and continues with the
  files left. A release that does not start, or the same version, is not
  switched to.
- New class `SelfRestart`; `RunControl` restart request, `handover()`;
  `RunLog` can continue an existing log file (hidden option
  `--continue-log`).

### Changed
- Documentation: handbook section "New release during a run", sequence
  diagram of the switch, class diagram; both PDFs rebuilt.

## [1.2.9] - 2026-11-09

### Added
- Failure memory: videos that failed (`FAILED`) or cannot be decoded
  (`BROKEN`) are noted in a new table `failures` of their directory's
  database and left out of later runs while their size is unchanged
  (listed as `FAILED BEFORE`). `--retry-failed` includes them; a
  successful retry deletes the row. The table `files` keeps its meaning.
- ffmpeg's complete message file of a failed encode is kept in
  `logs/ffmpeg/<timestamp>_<name>.ffmpeg.log` (before: only the last 8
  lines in the run log).
- A second `mediatools shrink` is refused while a shrink run is alive
  (exit code 3, hints for `attach` and `stop`).

### Changed
- Stricter validation of an encode: the output must be at least 2 % of the
  input's size and its first 10 s must decode without errors, in addition
  to exit code, video stream and duration. A failed validation keeps the
  original as before.
- `shrinker.py` split: planning in `ShrinkPlanner` (planner.py), the
  crash-safe copy-back in `ReplaceJournal` (journal.py). Behaviour
  unchanged.
- Documentation rebuilt: diagrams (classes, shrink sequence and decision
  diagram) and both PDFs.

## [1.2.8] - 2026-11-09

### Added
- Trial decode before encoding (`DecodeCheck`): the first 10 s of each
  video are decoded without output; 10 or more decoder errors mark the
  video as `BROKEN` (data still encrypted or scrambled although container,
  duration and codec tag look normal). Such a video is not encoded, not
  recorded and stays untouched; the run continues, the summary lists it
  under "not decodable", and the next run checks it again. Before, such
  files were encoded for as long as a good one and the output was grey.

## [1.2.7] - 2026-11-09

### Added
- `mediatools update`: applies delivered hotfix patch archives, replacing
  the per-version `apply-mediatools-X.sh`. The script behind it
  (`apply-release.sh`) ships inside the package. It finds the
  archives in the download folder (newest copy per version), asks up to
  which pending version to apply, applies them in order (hotfix branch,
  pull requests, tags, back-merge), installs the release and tidies the
  download folder.

### Changed
- Result table: minimum width 90 columns (was 102), so it also fits a
  half-width terminal window.
- Documentation rebuilt: all diagrams (new: package `update`; shrink
  sequence and decision diagram with background mode, stop request and
  result table) and both PDFs.

### Fixed
- `apply-release.sh` could delete itself when it was started again by a
  program it ran (the tests in the pre-push hook): its control variables
  are no longer exported, and only the script's own temporary copy is ever
  removed.
- bandit warnings about the wording of `# nosec` comments.

## [1.2.6] - 2026-11-09

### Added
- `shrink` blocks sleep and idle actions itself for the length of the run
  (systemd inhibitor lock via a helper that ends with the run, even after
  `kill -9`); the `systemd-inhibit` wrapper is no longer needed. Without
  systemd-inhibit or permission it warns and runs anyway.
- Framed result table: one row per finished video (end time, size before
  and after, ratio, playing time, time spent, status, path). Long paths
  wrap inside their cell. Written to the terminal and to
  `reports/shrink_results_<ts>.txt` (160 columns, flushed per row).

- `shrink` moves into the background after the confirmation: the prompt
  returns, closing the terminal no longer ends the run.
  `mediatools attach` (alias `front`) shows the log and the progress lines
  of the background run; Ctrl+C leaves the view. `--foreground` keeps the
  run in the terminal.

### Changed
- One class per module also for `PlanEntry` (`flatten/planentry.py`) and
  `ShrinkState` (`shrink/shrinkstate.py`); `FileResult` has its own module.
- ffmpeg command, validation and database record of each file are written
  to the log file only (DEBUG); `--verbose` shows them on the terminal as
  well. Log files now always contain DEBUG details.

## [1.2.5] - 2026-11-09

### Added
- Every log file names the running version and process ID in its second
  line, e.g. `mediatools 1.2.5 (pid 12345)`, so a log shows which release
  produced it.

## [1.2.4] - 2026-11-09

### Changed
- Progress display has two lines: the whole run, and the current file with
  percentage, size, encoded part and remaining time. Both lines are cut to
  the terminal width, so narrow windows no longer produce a new line per
  update.

## [1.2.3] - 2026-11-09

### Added
- `mediatools stop`: asks a running `shrink` from any other terminal to
  finish its current file and end (like the first Ctrl+C, but independent
  of which window has the keyboard focus). Returns at once; exit code 1 and
  no leftover request if no shrink run is active. The run shows
  `STOPPING after this file` on its progress line, also after Ctrl+C.

### Changed
- Planning stores newly probed durations and codecs every 500 files, not
  only at the end of each check, so an interrupted first check keeps its
  work; the progress messages show how many answers came from the cache.

## [1.2.2] - 2026-11-09

### Fixed
- Progress line and time estimate no longer count videos that need no
  encoding: already-HEVC videos are set aside during planning (codec cached
  in `state/shrink_codecs.tsv`), reported separately, recorded after the
  start question, and files that became HEVC during the run (hard links of
  a file shrunk earlier) are removed from the totals. In `--keep-copies`
  mode HEVC videos are no longer recorded either.

## [1.2.1] - 2026-11-09

### Fixed
- Replacement journal is durable: the validated output, the marker file and
  its directory are flushed to disk (`fsync`) before the original is
  overwritten. Before, a power failure during the copy could leave a
  partly overwritten original without a marker.
- Leftover `newShrinkedFiles/` folders of aborted runs are no longer
  deleted during planning; they are listed in the plan summary (folders,
  files, size; each folder with `-v`) and deleted only after the start
  question is confirmed.
- A closed terminal (SIGHUP) aborts the current file cleanly like a second
  Ctrl+C instead of ending the program without clean-up.
- `--threads` now limits the libx265 thread pool as well
  (`-x265-params pools=N`); ffmpeg's `-threads` alone did not, so x265 used
  every CPU. The default is the number of logical CPUs minus one, and
  ffmpeg runs with the lowest CPU priority (`nice -n 19`).

### Added
- Plan summary forecasts the size after shrinking and the saving, from the
  measured output/source ratio of earlier files
  (`state/shrink_throughput.json`, new keys `ratio_src`, `ratio_out`,
  `files`).

## [1.2.0] - 2026-11-08

### Changed
- `install.sh` installs a release tag from GitHub (newest `vX.Y.Z`, or the
  tag given; `--list` shows all) into its own virtual environment
  `$MEDIATOOLS_HOME/venv` and links `~/scripts/mediatools` to it. The
  launcher of 1.1.0, which ran the working files of a clone, is replaced.
- `mediatools --version` names a source checkout ("source checkout PATH,
  not an installed release") instead of showing a bare version number.
- CI runs on pull requests, on pushes to `main` and `develop` and on tags;
  the extra push trigger for work branches (every gate ran twice) is gone.
- Coverage gate raised from 80 % to 90 % (new tests for the disk choice,
  CLI errors and failure paths of sync, removal and encoding).

### Documentation
- Branch protection for `main` and `develop` and a release procedure that
  goes through pull requests (`docs/developer.md`).
- README and handbook: purpose in one minute (problem table, why file
  names are never taken away), warning that `shrink` is lossy, two
  installation options (pipx from GitHub, clone and launcher into
  `~/.local/bin`) with update and removal, `MEDIATOOLS_HOME`, and a
  "First steps" sequence that looks before it changes anything.

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
