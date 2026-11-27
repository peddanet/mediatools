# Changelog

All notable changes are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.16.0] - 2026-11-27

### Added
- Copy text from anywhere in the window: right-click menu *Copy* / *Copy
  all* on status lines, lists (queue, jobs, files) and text areas (log,
  output, results); in text areas the marked text, in lists the selected
  rows (columns separated by tabs); Ctrl+C copies the marking or the
  selected rows.
- `tools/review.sh` of the dev-conventions bundle: four-eyes review of two
  refs in Beyond Compare.

### Changed
- Development tools follow dev-conventions bundle v20. `tools/check.sh`
  prints the gates in the common console look (numbered stages, one
  PASS/FAIL/SKIP line each, result block), shows a progress bar for the
  tests and the five slowest tests (`tools/pytest_progress.py`), and runs a
  single gate with `--only <gate>`. `tools/release.sh`, `tools/same-code.sh`
  and the new `tools/output.sh` come from the bundle with English dialogs
  (`[y/N]`); while it waits for GitHub, `tools/release.sh` shows every
  answer of `gh` and a progress line per CI job, works with gh 2.45 of
  Ubuntu 24.04 (no `gh pr checks --json`) and counts only the required
  checks.
- CI runs one step per gate (`gate: <name>`), so GitHub and the release
  script show which gate runs or failed; triggers unchanged.
- `mediatools update` prints in the same look (colours in the terminal, plain
  log) and asks in English: "Apply <branch> and release it as <part>?
  [y/N]".
- Line length 120 for pycodestyle and pylint (was 100).
- `tools/check.sh` no longer looks for the development venv in
  `~/.venvs/mediatools`; only `.venv` of the project.

## [1.15.4] - 2026-11-26

### Fixed
- A job started with *Continue* showed up twice in the GUI, as running and
  as "1 paused": the job moves into the background with a new process ID,
  and the refresh took the ID of the ended starter for a crash. The queue
  now leaves the record alone while a shrink runs, and the starter ends
  only after the background process has registered. The duplicate would
  have been started again after the run.
- Log area of the shrink tab: it scrolls sideways as well (no wrapping),
  follows new lines only while it shows the end, and otherwise stays at the
  line the user scrolled to. An area that was too small at the first fill
  stayed at the beginning for good.

### Changed
- Shrink tab: status (with all buttons), queue and output pane get the
  height they need, the log the rest; again after a change of the text
  size, and the status part grows when a run starts. The output pane of
  the tab shows 3 lines instead of 6; the window opens at nine tenths of
  the screen (1280 x 900 to 1600 x 1200).
- Default text size 1.1 (11 pt) instead of 1.0; *Normal size* (Ctrl+0)
  returns to it. A `scale` already saved in `mediatools.ini` stays.

## [1.15.3] - 2026-11-25

### Fixed
- `tools/release.sh` tries `gh pr create` and `gh pr merge` up to five
  times (`RELEASE_RETRY_PAUSE`, default 10 s, apart) and stops when no pull
  request number results. A passing GitHub server error went on with an
  empty number and stopped only at "required checks of pull request #
  failed"; GitHub also refuses a merge now and then right after green
  checks.
- `mediatools update` recognises copies of a patch archive by content and
  by the branch in its `patch.info`: a second download of an archive
  already applied ("... (1).tar.gz") is no longer offered and failed at
  the base guard, a browser copy "...1.tar.gz" is no longer refused for its
  name, and identical files are offered once. Such files are listed as
  *applied* or *old copy* and removed at the end.

### Changed
- The time in the plan line "to encode: ..." is marked "(at plan time)":
  it is computed once and does not follow the run, unlike the ETA of the
  progress display.

## [1.15.2] - 2026-11-25

### Fixed
- The remaining time (ETA) of a shrink uses the speed of the recent
  running time, read from the ledger (last three hours of encoding;
  parallel encodes count once). Before it was the average since the start
  of the process: a pause (stop, Ctrl+Z, planning) made it too long, it
  hardly went down while the material changed, and every `mediatools
  reload` made it jump.

### Changed
- The GUI window title is "mediatools" without the version; the running
  version stands in a line on the right below the tabs. Launcher, dock and
  window search showed the title "mediatools 1.x.y" next to the menu entry
  "mediatools" like a second application.

## [1.15.1] - 2026-11-25

### Added
- `tools/check.sh` checks each tree in full once: after a full green run
  the key (working-copy tree, Python version, installed packages) is kept
  in `.git/checked-trees`; a known tree runs only privacy and shell.
  `CHECK_FULL=1` forces a full run, CI always runs in full
  (dev-conventions `templates/check-cache.sh`).

### Fixed
- `install.sh` points the menu entry to the new link when it named the
  old one, and removes the old link only when neither the menu entry nor
  the systemd unit names it any more.
- The GUI start keeps an existing menu entry current also without
  `[gui] desktop_entry = yes`, so the entry never starts a program that is
  gone.

## [1.15.0] - 2026-11-24

### Added
- `mediatools stop --now` and the GUI button *Abort now* (after a
  question): abort the current file at once; its partial output is
  deleted, the original stays untouched.
- `examples/` with samples of every local file (`config.ini.sample`,
  `privacy/denylist.sample.txt`, `privacy/allowlist.sample.txt`), kept in
  step by tests; CONTRIBUTING.md: table "Local files of a developer" with
  the copy commands; GitHub social preview `docs/images/social-preview.png`
  with its SVG source (dev-conventions handover v7, repository
  completeness).

### Changed
- Icons, menu entry and systemd unit are real files of the package
  (`src/mediatools/data/`: `mediatools.svg`, PNGs, `mediatools.desktop.in`,
  `mediatools-snapshot.service.in`) instead of strings in the code; the
  written files are unchanged.
- Privacy gate: fails without local terms (exit 2; only CI skips it) and
  checks the identities - author, committer and tagger are searched for
  the terms, and every address must be a no-reply address or stand alone
  in the allow list; findings show the address masked (handover v8). A
  no-reply address and a name equal to its login are not searched for
  terms (they are the prescribed public identity).
- Tests never touch the real desktop: `tools/check.sh` always runs them on
  Xvfb, `conftest.py` sets its own runtime folder, Wayland name and data
  folders; every wait for a child process has a time limit.

### Fixed
- A stopped or aborted queue job no longer disappears: it goes back to the
  queue as first entry, marked *paused* (after *Stop, then next* behind the
  job that starts); *Continue* goes on with it, *Remove* drops it. A job
  ended by an error or left by a crash comes back the same way.

## [1.14.1] - 2026-11-21

### Changed
- `mediatools update`: archives no longer needed are shown as `released`
  (version already released) or `old copy` (older copy of the same
  archive) instead of `stale`, each with "(removed at the end)".

## [1.14.0] - 2026-11-21

Last release in the versioned patch format; from now on patches carry no
version and `tools/release.sh` sets it (dev-conventions handover v5).

### Changed
- Installation in the XDG layout: `install.sh` installs into
  `~/.local/share/mediatools/venv` and links `~/.local/bin/mediatools`.
  The old venv `~/scripts/mediatools.d/venv` is removed when no process
  runs from it any more; the old link `~/scripts/mediatools` first points
  to the new release and is removed by a later run once `~/.local/bin` is
  in `PATH`; the snapshot trigger unit is rewritten to the new link. Only
  what an earlier `install.sh` created is touched.
- `mediatools update` works in two steps: (1) a newer release on GitHub is
  installed (also without a clone), (2) patch archives
  `mediatools-feature-<name>.tar.gz` / `mediatools-hotfix-<name>.tar.gz`
  with `patch.info` are applied after one question, checked against the
  tree or blob hashes before and after `git am`, and released with
  `tools/release.sh` using the part and message from `patch.info`.
- Caches of probed durations and codecs moved to `~/.cache/mediatools`
  (moved once on first use, never overwritten).
- `tools/check.sh` activates the development venv by itself (`.venv` in
  the project, else `~/.venvs/mediatools`) when none is active; the
  activation ends with the script. `.venv/` is ignored by git.
- The menu entry starts `~/.local/bin/mediatools`.

### Added
- `tools/release.sh` (GitHub variant of the dev-conventions script):
  feature, develop and hotfix releases through pull requests with required
  checks, release branch `release/X.Y.Z`, tag, release check, merge back
  through `merge/vX.Y.Z`, bit-identity check, installation and
  `tools/after-install.sh` (reload of a running shrink); `--merge-only`.
- `tools/same-code.sh`: are branches and tags bit-identical (tree hashes)?
- Shrink tab: button *Stop, then next* - the running job stops after its
  current file and the selected waiting job starts.
- Quality gate `shell`: shellcheck for every shell script (CI installs it).

### Fixed
- The privacy gate refuses to run while privacy lists of the layout before
  1.5.0 still exist, instead of passing without terms.

## [1.13.1] - 2026-11-21

### Changed
- GUI, shrink tab: the queue replaces the list of earlier jobs - the
  running job first, then the waiting jobs in the order of processing,
  each with folder, options and progress; buttons *Continue* (start the
  selected waiting job now), *Add*, *Change*, *Remove*, *Up*, *Down*. The
  shrink form left the tab.
- New job dialog (*Add*, *Change*): every shrink job so far with *Add to
  queue*, *Remove* and *Remove finished*, and the shrink form with folder
  chooser and every option (*Run*, *Add to queue*, *Save changes* for a
  waiting job). The dialog of *Run* (`JobPicker`) is gone; the queue has
  no tab of its own any more.
- Every part of the shrink tab (state, queue, log, output) is separated
  by a sash and can be resized; the lists scroll and the arrow keys work
  once a list has the focus.

### Added
- `JobRegistry.remove`, `ShrinkQueue.replace`; `queue --add` drops
  `--foreground` and `-y`.

### Documentation
- Handbook: new screenshot of the shrink tab, new screenshot of the job
  dialog; text of the shrink tab and the job dialog.

## [1.13.0] - 2026-11-21

### Added
- `mediatools queue`: shrink jobs that run one after the other. `--add
  "FOLDER OPTIONS"` (checked with the parser of `shrink`, folder stored as
  an absolute path), `--first`, `--note`, `--remove N`, `--move N --to M`,
  `--clear`, `--run`; without options the list. A shrink job that ends
  normally (exit code 0 or 2, not declined) starts the next queued job in
  the same background process; `mediatools stop` pauses the queue.
  `mediatools status` names the number of waiting jobs.
- `estimate --suggest N`: proposes up to N concrete shrink jobs (level and
  `--min-seconds`), the most space per hour first, each with files, GiB
  freed, time and running totals (greedy: every job finds the earlier ones
  done). `--queue` puts them into the queue in that order, with CRF,
  preset, parallel jobs and threads of the most recently active job.

### Fixed
- The remainder of a remembered job is now also reduced when *another*
  job shrinks one of its videos, e.g. a job on the parent folder shrinking
  the rest of a sub-folder job; before, only the running job's remainder
  shrank and the sub-folder job kept the figure of its last plan.

## [1.12.0] - 2026-11-20

### Fixed
- `shrink` re-encodes **only the picture**: ffmpeg now runs with
  `-map 0 -c copy -c:V libx265`, so every audio track, subtitle, chapter,
  data stream and cover picture is copied unchanged. Up to 1.11.1 ffmpeg's
  automatic stream selection kept one stream per type and re-encoded the
  audio (e.g. AAC 130 kbit/s became 69 kbit/s; a second audio track and
  subtitles were dropped).
- Validation rejects an output that has fewer streams of any type than the
  source (`output lost stream(s) (audio 2 -> 1)`); the original is kept.
  A stream the container cannot take makes ffmpeg fail; the original is
  kept as well.
- `broken` repairs keep the former behaviour on purpose (one stream per
  type, audio re-encoded), because damaged audio packets cannot be copied
  into a clean file.

## [1.11.1] - 2026-11-20

### Fixed
- `estimate`: the time of a video is modelled from its **playing time**
  (`took = a + b_q x length`, one slope per bit-rate class, a shared fixed
  part `a`), not from its size; the encoder works frame by frame, so a
  small video with a low bit rate is not fast. New class `TimeModel`.
- The fixed part per video counts as *measured* only when the ledger holds
  at least 20 videos shorter than 5 min. Before that it is extrapolated from
  long videos (1.11.0 showed 52 s per video as if measured); the output then
  says so, shows the fixed part as `0 to N s` and every time as a range
  (optimistic to cautious); groups are ranked by the cautious time.
- Per bit-rate class the basis lines show the encoding speed as a multiple
  of real time; the report has the columns `seconds_low` and
  `playing_seconds`.

## [1.11.0] - 2026-11-20

### Added
- `estimate --up N` / `--up-to DIR`: estimates the given folder (`L1`) and
  its parent folders (`L2`, `L3` ...) in one run. Every level counts only
  what it adds to the level inside it, so the rows answer what going one
  level up brings; the thresholds of `--min-seconds` are shown per level.
- Fixed time per video: the ledger time of a video is fitted as
  `a + b x size`; `a` (start, checks, validation) is added once per video,
  so many short videos are no longer estimated too fast. `--overhead
  SECONDS` sets it by hand, e.g. to see the effect of 5 or 10 s.

### Changed
- `estimate` walks the tree once for all levels and probes a video missing
  in the caches with **one** ffprobe call for duration and codec (about
  half the time of a first check); the default of `--probe-jobs` is the
  number of CPU threads, at most 16 (was 8).
- `estimate` counts a video with several hard-linked names once (in the
  innermost level); the number of further names is shown.
- The console output of `estimate` names the levels only as `L1`, `L2` ...;
  the folders are in the report file, which also has a `level` column.

## [1.10.0] - 2026-11-19

### Changed
- Python 3.12 is the minimum (`requires-python = ">=3.12"`, `install.sh`,
  pylint `py-version`, mypy `python_version`); Python 3.10 reached its end
  of life on 2026-10-01. The CI matrix is 3.12 and 3.14; the required checks
  of the branch protection become `gates (3.12)` and `gates (3.14)` and must
  be changed before this release is applied (`docs/developer.md`, section
  "Branch protection").

## [1.9.1] - 2026-11-19

### Fixed
- Duration and codec caches, and the stored measurements of `broken`, can
  be saved by two processes at the same time (e.g. a shrink job and
  `estimate`): each process writes through its own temporary file and
  keeps the entries the other one saved meanwhile. Unreadable cache lines
  are skipped instead of stopping the run.

### Documentation
- Handbook: which commands may run at the same time, and how to get more
  parallel encodes (`--jobs`, `--threads`).

## [1.9.0] - 2026-11-19

### Added
- New command `mediatools estimate [PATH]` (also a GUI tab): plans like
  `shrink` without changing anything and groups the videos not shrunk yet
  by playing time and bit rate. For every group it estimates the space
  freed (output share measured per bit-rate class in the ledger) and the
  time (measured speed), ranks the groups by GiB freed per hour, and sums
  up what each `--min-seconds` value would take in. Report in
  `reports/shrink_estimate_<time>.tsv`.
- Progress display: new line `expect` - the GiB still to go times one minus
  the measured output share, and the expected total freed at the end of
  the job.
- Planning probes missing durations and codecs with several ffprobe
  processes at the same time (`probe_jobs`, default 8; `estimate
  --probe-jobs`).

## [1.8.0] - 2026-11-19

### Added
- `broken` reads every `_broken` folder below the given path
  (`QuarantineSet`): a video set aside in a sub-folder is not collected
  again by a run on the parent; listing, backup check, re-measuring and
  review cover all folders. The folders found and the state of the backup
  are named at the start of every run.
- Stored measurements (`AssessmentStore`, `<state>/state/assessments.json`,
  mode 600): a video is decoded again only when its size, modification
  time or the measuring method changed, the entry cannot be read, or with
  the new option `--recheck`.
- Shrink tab: the line `job:` shows root and options of the running job as
  it was started (from its log), not the values of the form.
- Job dialog: says in words what *Continue* and *Run as in the form* start
  and shows the command line *Continue* would run for the selected job.
- A remembered shrink job keeps `--retry-failed` and `--keep-copies`.
- `shrink --retry-failed` warns how many videos recorded as `BROKEN` it
  plans again.

### Changed
- Re-measuring reports every result, damaged or not.
- `--collect --dry-run` also previews the backup check of set-aside videos
  without a result (nothing stored); with `--review` or `--backup-check` it
  says that they are not carried out in a dry run.
- `--backup-check` checks only set-aside videos without a result, or whose
  backup copy changed after the check (`--recheck`: all).
- A real `--collect` without new finds still checks the backup of set-aside
  videos.
- The console of the GUI says "shrink runs in the background (pid N)"
  when a command hands its job to the background, instead of
  "finished, exit code 0".

## [1.7.1] - 2026-11-18

### Added
- `mediatools broken` checks the backup disk before anything is repaired
  or deleted. `[broken] backup` names the backup copy of the home
  directory (same relative paths; `[broken] backup_of` if it mirrors
  another directory). Every damaged video is looked for at the same path,
  then by file name in the whole backup; a copy counts only if it decodes
  completely without a decoder error. Results (`intact`, `damaged`,
  `missing`) are kept in the manifest. The disk must be mounted (exists,
  other file system than the collection, mirror of the shrink root
  present), otherwise `broken` says so and decides nothing. The check runs
  after `--collect` (also in a dry run), with the new option
  `--backup-check`, and with `c` in the review.
- Review: **recover** (`b`) copies the intact backup version into place
  under all names (written next to the original, flushed, size compared,
  renamed) and clears the failure entry, so shrink encodes it again.
  Repair and delete are offered only after a backup check without an
  intact copy; the hopeless group can check, recover all, and delete all
  only those without a copy.
- Review in the GUI: every question appears as a dialog with one button
  per answer (`ChoiceDialog`); commands started by the GUI announce their
  questions on a hidden line (`MEDIATOOLS_GUI=1`). Also the yes/no
  questions of the other commands get a dialog.
- The player of the review starts with sound at `[broken] volume` percent
  (default 30: VLC `--gain`, mpv `--volume`); `--mute` replaces `--audio`.
- `mediatools reload` waits up to 10 s for the answer of the running job
  and prints it; the job writes `accepted: ...` or `rejected: ...` with the
  reason (exit code and last error line of the installed release) to
  `state/shrink.restart.answer`; `status` shows it in the line `reload:`.

### Fixed
- False alarms of the trial decode: lines of the `null` muxer (irregular
  time stamps, "non monotonically increasing dts") were counted as decoder
  errors, so videos that play normally were recorded as `BROKEN`. They are
  no longer counted (`DecodeCheck.relevant`), in shrink as in `broken`.
  `--collect` removes the `BROKEN` entry of a video that decodes
  completely without errors instead of moving it; videos set aside by
  1.7.0 are measured again (also at the start of the review) and those not
  damaged go back for shrink. The measurement of `broken` now decodes the
  sound as well.
- A parallel shrink asked the installed release for its version every
  half second after it had accepted a reload; it asks once now.

### Changed
- `broken` without options says how to go on; `--dry-run` without
  `--collect` is an error. Repair progress is shown in steps of 5 %.
- `--review` runs in a terminal or in the GUI; at the end of input every
  question ends with its quit answer instead of repeating.
- The command `broken` lives in `mediatools.broken.command`.

## [1.7.0] - 2026-11-17

### Added
- `mediatools broken`: damaged videos of a shrink root are set aside in
  `<root>/_broken/` and reviewed there.
  - `--collect` takes every video recorded as `BROKEN` in the shrink
    databases (its data failed the trial decode) and, with `--report`, the
    videos of a report file (column `path`; with a column `check` only
    rows naming an error or a broken file). Videos recorded as `FAILED`
    (only the encode failed) are not taken. Each video is measured once
    (full trial decode at the lowest CPU priority: decodable share, frozen
    or black parts, decoder errors) and classified: *hopeless* if it does
    not open, if less than 10 % of it decodes or if more than 90 % of the
    decoded part is a still or black picture; otherwise *promising*.
  - The videos move with `rename` (no copy) into
    `_broken/aussichtsreich/` or `_broken/hoffnungslos/`, keeping their
    path relative to the root, so the unit and every folder of the origin
    stay visible. All hard link names inside the root move along (also
    `_flat`); `_broken/manifest.jsonl` (mode 600) records every name pair,
    the measurement and the reason. Videos whose output lies in shrink's
    work folder (running or interrupted encode) stay in place.
  - `--review` shows the promising videos first, the most usable first,
    each with play, repair, keep, delete, skip, quit; then the hopeless
    ones as a group (view samples, delete all, one by one).
  - Repair encodes the decodable part with error-tolerant input into a
    clean HEVC file (same quality settings as shrink), validated by a full
    decode without errors; after playing it, it can be accepted (it
    replaces the damaged video under all its names and is recorded as
    shrunk) or discarded. Advised from 50 % usable content on.
  - Keep puts the video back unchanged and records it as `BROKEN` with
    the reason "kept after review", so neither shrink nor `--collect`
    takes it again while its size is unchanged.
  - Delete removes all names (trash, or `--delete`) and, if
    `[broken] downloader_db` names a downloader database relative to a
    unit folder, marks the video there as not downloaded, so the
    downloader fetches it again.
  - Without `--collect` / `--review` the command lists what is set aside.
    `--review` needs a terminal.

### Changed
- `_broken` is a reserved directory like `_flat` and `newShrinkedFiles`:
  no scan of shrink, progress, triage or flatten enters it.
- The privacy scan no longer takes folders that mediatools creates itself
  (`_broken`, `_flat`, `newShrinkedFiles`) in the collection root as
  terms.
- New database helper `ShrinkDatabase.failure_reason()`.

## [1.6.3] - 2026-11-14

### Fixed
- Parallel shrink: every slot shows its own progress. The progress
  callback of a slot read the loop variable of the start loop when ffmpeg
  reported, so all reports went into the cell of the encode started last
  and the other slot stayed at 0 % (the encodes themselves were correct).
- File lines of a parallel run estimate their remaining time from the
  speed of the whole run, so a file that has just started no longer shows
  days left.
- GUI: the settings column of the job list is wide enough for
  "jobs x threads".
- GUI: the shrink panel shows every file line of a parallel run and the
  space line only once (it was cut after three lines and showed the space
  line twice when one encode was running).

### Changed
- `mediatools update` after an interruption (e.g. lost connection while
  waiting for CI): a hotfix or back-merge branch that the interrupted run
  already pushed with exactly the same content (Git tree) on the same
  parent commit(s) is taken over instead of rebuilt and pushed again; the
  local gates are not run a second time and the CI run already started
  for it is reused. Any difference in content or base leads to the full
  path as before; merges still need the required checks green.

## [1.6.2] - 2026-11-14

### Added
- GUI: *Run* in the shrink tab first opens a dialog with the shrink jobs
  so far (from the command line and the GUI), with progress and settings:
  *Continue* or a double click runs the selected job, *Run as in the form*
  the form values, *Cancel* nothing. Without any job the form runs at once.
- Jobs run before 1.6.0 are taken once from the shrink logs (root and
  order; logs since 1.6.0 give the whole job). Their remainder shows as
  `not planned yet` until the job runs again.
- An explicit `--threads` is remembered with the job (list, dialog,
  form and the arguments of a continued job).

### Fixed
- GUI: the log of the shrink tab and the command output no longer jump
  back while new lines arrive; the view follows the end only while the
  end is shown.

## [1.6.1] - 2026-11-13

### Added
- GUI scheme `auto` (new default of `[gui] theme`): follows the light or
  dark preference and the accent colour of the desktop, read from the XDG
  settings portal (`org.freedesktop.appearance`, via `gdbus`) and checked
  every 3 s, so a switch of the desktop takes effect at once. Without a
  portal or preference the window is dark. Menu *View*: *Scheme of the
  desktop*, *Light scheme*, *Dark scheme*.
- `mediatools shrink --preset NAME` (default `[shrink] preset`, else
  `medium`): libx265 preset; `faster` measured 1.57 times as fast at
  nearly the same SSIM on a test excerpt. The preset is a setting of the
  job list and the GUI form.

### Fixed
- Tests: Tk variables left over by GUI tests are detached in the main
  thread, so a later garbage collection inside a worker thread of the
  parallel shrink tests cannot fail with "main thread is not in main loop".

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

### Fixed
- Tests: Tk variables left over by GUI tests are detached in the main
  thread (see 1.6.1).

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
