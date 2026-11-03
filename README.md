# mediatools

**Maintenance tools for large local media collections on Linux.**
Shrink videos to HEVC without losing track of what is done, review short
clips in batches, build flat hard-link views of deeply nested download trees,
back everything up to a second disk **without duplicating hard links**, and
keep versioned snapshots of it with rsnapshot.

[![tests](https://github.com/peddanet/mediatools/actions/workflows/tests.yml/badge.svg?branch=develop)](https://github.com/peddanet/mediatools/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![platform](https://img.shields.io/badge/platform-Linux-lightgrey)
![license](https://img.shields.io/badge/license-MIT-green)

```text
mediatools shrink    re-encode videos to HEVC in place, tracked per directory
mediatools progress  read-only report: which videos are already shrunk
mediatools triage    open short clips in batches, close a window to discard it
mediatools flatten   one flat folder of hard links per unit (<unit>/_flat)
mediatools sync      rsync backup that keeps hard links and never deletes
mediatools snapshot  versioned backups with rsnapshot on a removable disk
mediatools config    one local file for paths and names (kept outside the repo)
mediatools rename-db rename shrink databases: rename-db OLD NEW [PATH]
```

---

## Contents

- [Why](#why)
- [Safety model](#safety-model)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Subcommands](#subcommands)
  - [shrink](#shrink) · [progress](#progress) · [triage](#triage) · [flatten](#flatten) · [sync](#sync) · [snapshot](#snapshot)
- [Configuration](#configuration)
- [How the pieces fit together](#how-the-pieces-fit-together)
- [Data directory](#data-directory)
- [Exit codes](#exit-codes)
- [FAQ](#faq)
- [Development](#development)
- [License](#license)

---

## Why

Archives of creator platforms such as Patreon - one folder per campaign,
posts with numbered folders, attachments, image galleries - quickly grow into
trees like this (illustrative example):

```text
collection/
└── projectA/
    ├── posts/members/987654321 2024-05-01 10-00-00/Videos/making_of_part1.mp4
    ├── posts/public/Images/cover_2024.jpg
    └── attachments/...
```

Three things become painful at a few hundred thousand files:

1. **Disk space** - source videos are H.264 at high bit rates.
2. **Browsing** - every image or clip is buried four or five levels deep.
3. **Backups** - hard links (from deduplication or flat views) silently turn
   into full copies when the backup tool does not preserve them.

`mediatools` addresses each point with one subcommand and shares one set of
rules between them (which directories are never entered, how files are
identified, where logs go).

## Safety model

| Rule | Where it is enforced |
|---|---|
| Originals are replaced only by a **validated** encode (exit code, video stream, duration) | `shrink` |
| Copy-back and database entry are never separated by an interruption | `shrink` |
| `_flat` and `newShrinkedFiles` are never entered by any scan | all |
| `flatten` writes **only** inside `<unit>/_flat/`; originals are never touched | `flatten` |
| Nothing is removed without an explicit confirmation; default is the trash | `triage` |
| A player that exits immediately is treated as an error, its file is kept | `triage` |
| `rsync --delete` is **never** used; target-only files are reported, not removed | `sync` |
| A newer file on the receiving side is never overwritten (`rsync -u`) | `sync` |
| Empty placeholders are never copied over a full file (`rsync --min-size=1`) | `sync` |
| Every run writes a time-stamped log; every flatten run a reversible plan | all |
| `snapshot` refuses to adopt or sync a directory that is not a copy of the source (dry-run probe) | `snapshot` |
| `snapshot` aborts if a run would remove more files than `max_deletions` from the newest snapshot | `snapshot` |

## Requirements

| Tool | Needed by | Debian/Ubuntu package |
|---|---|---|
| Python ≥ 3.10 (standard library only) | all | `python3` |
| ffmpeg, ffprobe with libx265 | shrink, triage | `ffmpeg` |
| VLC or mpv | triage | `vlc` / `mpv` |
| gio (trash) | triage | `libglib2.0-bin` |
| rsync ≥ 3.0 | sync, snapshot | `rsync` |
| rsnapshot ≥ 1.4 | snapshot | `rsnapshot` |

```bash
sudo apt install ffmpeg vlc libglib2.0-bin rsync rsnapshot
ffmpeg -hide_banner -encoders | grep libx265      # must print a libx265 line
```

Hard links require a Linux file system such as ext4, btrfs or XFS on both
sides of a `sync`. exFAT, FAT and NTFS cannot store hard links.

## Installation

**Option A - no installation, launcher script (recommended for personal use)**

```bash
git clone https://github.com/peddanet/mediatools.git ~/Projects/mediatools
cd ~/Projects/mediatools
./install.sh                     # creates ~/scripts/mediatools (or BIN_DIR=...)
mediatools --version
```

**Option B - pipx**

```bash
pipx install ~/Projects/mediatools
```

Ubuntu 24.04 and later block `pip install --user` for the system Python
(PEP 668); use option A or pipx.

## Quick start

```bash
mediatools config --init                 # writes ~/scripts/mediatools.d/mediatools.ini
"${EDITOR:-nano}" "$(mediatools config --path)"   # set [collection] root

mediatools progress                      # how much is still H.264?
mediatools shrink                        # shrink, asks once for confirmation
mediatools triage --max-seconds 30       # review clips up to 30 s
mediatools flatten --dry-run             # what would _flat contain?
mediatools flatten                       # create the hard links
mediatools sync                          # back up; asks where the disk is
```

Every command also accepts an explicit path instead of `[collection] root`.

## Subcommands

### shrink

Re-encodes every video below `PATH` with libx265 and **keeps the file
name**, so download tools that skip existing names do not fetch the large
original again.

```bash
mediatools shrink [PATH] [--keep-copies] [--crf 28] [--threads 6] [--ext mp4] [-y] [-v]
```

Per file, in this order:

1. **Database** - the directory's `shrink.db` (name configurable) has a row with the same
   path, size and mtime → skipped. *The database always has precedence.*
2. **Codec** - already HEVC → recorded, not re-encoded.
3. **Encode** into `newShrinkedFiles/`, validate, copy over the original
   *in place* (same inode, so `_flat` links see the new content), record.

| Option | Meaning |
|---|---|
| `--keep-copies` | leave originals untouched, keep shrunk copies for review, record nothing |
| `--crf N` | libx265 constant rate factor, default 28 |
| `--ext LIST` | extensions to process, default `mp4` |
| `-y` | skip the confirmation question |

Ctrl+C is honoured after the current file. A file that ffmpeg cannot
encode is logged and skipped; the run continues and ends with exit code 2.

### progress

```bash
mediatools progress [PATH] [--ext mp4]
```

Read-only. Writes `progress_done_<ts>.txt` and `progress_todo_<ts>.txt` to
the [data directory](#data-directory) and prints the counts.

### triage

```bash
mediatools triage [PATH] [--max-seconds 60] [--min-seconds 0] [--batch 10]
                         [--player vlc] [--audio] [--delete] [--no-placeholder]
                         [--include-kept] [--dry-run] [-y]
```

1. Counts the videos with a duration between the two thresholds and reports
   number, batches, total length and a rough review-time estimate, then asks
   whether to start (`--dry-run`: report and list only; `-y`: no start
   question). Durations are cached, so a second count is fast.
2. Opens a batch, **one player process per video** (muted, looping).
3. **Close the window = remove the video. Leave the window open = keep it.**
   Then press Enter.
4. The script lists *remove / keep / player error* and asks for confirmation
   (always, also with `-y`).
5. Removed: the file **and its `_flat` hard links** (otherwise no space would
   be freed). By default an **empty placeholder (0 bytes)** is left under the
   original name, so a downloader that skips existing names does not fetch
   the video again; `--no-placeholder` removes the name completely. Every
   removal is recorded in `state/triage_discarded.tsv`. Kept files are
   remembered and not shown again.

Placeholders are ignored by `shrink`, `progress`, `flatten` and `triage`, and
`sync` never copies them over the full file on the backup (`--min-size=1`).
Whether a downloader really skips a 0-byte file depends on the downloader -
check it once with one video before a large session.

VLC is started with `--no-one-instance --no-playlist-enqueue`; a player that
exits within three seconds counts as an error and its file is kept. Trashed
files can be restored with `gio trash --list` / `gio trash --restore URI`.

### flatten

```bash
mediatools flatten [PATH] [--dry-run] [--prune] [--no-abbrev] [--ext LIST] [-y]
mediatools flatten --undo PLAN.tsv
```

`PATH` is the master level; each direct sub-directory is a **unit**. For every
media file of a unit a hard link is created in `<unit>/_flat/`. The link name
encodes the original location:

```text
projectA/posts/members/987654321 2024-05-01 10-00-00/Videos/clip.mp4
projectA/_flat/posts__members__987654321 2024-05-01 10-00-00__Videos__clip.mp4
```

The file system's name limit is read at run time (`statvfs`; eCryptfs with
encrypted names allows 143 bytes, ext4 255). Names that do not fit are
shortened step by step:

| Level | Rule | Example |
|---|---|---|
| 0 | full path, `__` separated | `posts__public__Videos__a.mp4` |
| 1 | configured folders abbreviated, id/date compacted | `p__m__987654321-20240501T100000__V__making_of_…_a1b2c3.mp4` |
| 2 | level 1 + front of the file stem cut, tail (id) kept | `p__m__987654321-20240501T100000__V__~project_part_one_…_a1b2c3.mp4` |
| 3 | directory part hashed | `27613887__~making_of_…_a1b2c3.mp4` |

Examples for a 100-byte limit with `abbreviations = posts=p, members=m, Videos=V`.

Every link is listed in `<unit>/_flat/_index.tsv` (link name → original
path), and any link can be traced by inode: `find UNIT -samefile LINK`.
Re-runs only add what is missing; a replaced source is re-linked; `--prune`
removes links whose source is gone. Each run saves a plan; `--undo PLAN`
removes exactly the links of that plan.

### sync

```bash
mediatools sync [SOURCE] [TARGET] [--reverse] [--find-duplicates] [--no-report]
                                [--report-only] [-y]
```

Without `TARGET` you are asked which mounted disk to use; the choice is
remembered. The transfer is

```bash
rsync -aHu --no-inc-recursive --min-size=1 --exclude=newShrinkedFiles/ --exclude='*.part' SOURCE/ TARGET/
```

preceded by a dry run with a summary and a confirmation. Before that the
target is checked: not nested with the source, a file system that can store
hard links, and a real hard-link test.

After the transfer a report lists **files that exist only in the target**:

| Status | Meaning |
|---|---|
| `unique` | the only copy - keep unless you know it is obsolete |
| `duplicate` | identical content exists elsewhere on the target |
| `hardlinked` | just another name of a file that is still present |

`--find-duplicates` additionally lists groups of identical files stored in
different inodes (space a deduplication could reclaim). Nothing is deleted.

Run the same command with `--reverse` (or swapped arguments) to copy back.
Because nothing is deleted, a reverse run also restores files you removed on
purpose on the other side - check the dry-run summary.

### snapshot

Versioned backups with [rsnapshot](https://rsnapshot.org/) on a disk that is
not always attached. Every snapshot is a complete directory tree; unchanged
files are hard links to the previous snapshot, so each snapshot costs only
the changed files. Deletions in the source leave the newest snapshot but stay
in the older ones.

```bash
mediatools snapshot check                      # read-only: config, disk, tools, probe
mediatools snapshot adopt /media/USER/DISK/X   # one time: existing copy becomes snapshot 1
mediatools snapshot init                       # or: start with an empty snapshot root
mediatools snapshot run [--dry-run] [-y]       # probe, sync, rotate due levels
mediatools snapshot status                     # list snapshots and last rotations
mediatools snapshot install-trigger            # run automatically when the disk is mounted
```

- **adopt** renames an existing copy on the disk into rsnapshot's layout -
  no data is copied - after a dry-run probe has confirmed that it really is a
  copy of the source. The first state is additionally kept as `stand0`
  (hard-link copy, never rotated away).
- **run** compares source and newest snapshot first, writes the list of files
  that would leave the newest snapshot to `reports/`, refuses if the disk is
  too small or more than `max_deletions` files would go, then runs
  `rsnapshot sync` and rotates: `alpha` every run, `beta` weekly, `gamma`
  monthly, `delta` yearly - measured from the last rotation, because the
  disk is not always attached.
- **install-trigger** writes a systemd *user* unit that is started by the
  disk's mount unit; it runs with `-y`, which aborts instead of asking.

## Configuration

Paths and names that describe a concrete collection are not part of the code.
They live in `~/scripts/mediatools.d/mediatools.ini` (or
`$MEDIATOOLS_HOME/mediatools.ini`), created by `mediatools config --init`:

```ini
[collection]
root = ~/Pictures/collection

[sync]
source = ~

[shrink]
db_name = shrink.db

[flatten]
abbreviations = Videos=V, Images=I, Audios=U

[snapshot]
source = ~/Pictures/collection
disk = /media/USER/BACKUP
root = rsnapshot
retain_alpha = 7
retain_beta = 4
retain_gamma = 12
retain_delta = 2
exclude = newShrinkedFiles/
max_deletions = 500
```

`mediatools config` prints the effective values. If the database name is
changed, `mediatools rename-db OLDNAME NEWNAME [PATH]` renames existing
databases (and their `.bak_*` backups) in one pass without further questions
and stores the new name as `[shrink] db_name`; `--dry-run` lists them first.

Every command takes an explicit path, which always wins over the file. To
change a default permanently:

```bash
mediatools config --set collection.root=~/Pictures/other   # shrink, triage, flatten, ...
mediatools config --set sync.source=~                      # default source of sync
```

## How the pieces fit together

```mermaid
flowchart LR
  DL[downloader] --> TREE[(media tree)]
  SH[shrink] -->|in place, same inode| TREE
  SH --> DB[(shrink.db per dir)]
  PR[progress] -->|read| DB
  TR[triage] -->|trash file + its _flat links| TREE
  FL[flatten] -->|hard links| FLAT[(unit/_flat)]
  FLAT -. same inode .- TREE
  SY[sync] -->|rsync -aHu| BK[(backup disk)]
  SY --> REP[target-only report]
  SN[snapshot] -->|rsnapshot, link-dest| VS[(versioned snapshots)]
```

## Data directory

All logs and state live in `$MEDIATOOLS_HOME` (default
`~/scripts/mediatools.d`):

```text
mediatools.ini   configuration
logs/      <command>_<YYYY-MM-DD_hh-mm-ss>.log
state/     triage_kept.tsv, triage_discarded.tsv, triage_durations.tsv,
           sync_targets.json, snapshot_levels.json
plans/     flatten_<ts>.tsv  (input for --undo)
reports/   progress_*.txt, sync_target_only_*.tsv, snapshot_removals_*.txt
snapshot/  rsnapshot.conf (generated), rsnapshot.log
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (also: user declined at a confirmation) |
| 1 | error or interrupted |
| 2 | `shrink`: at least one file could not be encoded |
| 130 | `triage` interrupted with Ctrl+C |

## FAQ

**Does `_flat` double the disk usage?** No. A hard link is a second name for
the same data. `du` counts it once; `tree --du` counts it per name.

**Does `sync` copy `_flat` twice?** No, as long as source and `_flat` are in
the same run (`-H` only sees hard links inside the transfer set) and the
target file system supports hard links.

**Why does `sync` not delete files on the backup?** Deliberately. A
single-copy mirror is the last line of defence; deletions are left to a
human, supported by the target-only report. `snapshot` is the alternative
that does propagate deletions, because older snapshots keep the files.

**Is it tied to one platform?** No. Folder abbreviations, the database name
and all paths come from the configuration file; the defaults are generic.

## Development

```bash
pip install -e '.[dev]'                  # pytest, pylint, mypy, pycodestyle, bandit
git config core.hooksPath .githooks      # pre-push hook runs the gates
tools/check.sh                           # format, lint, types, security, tests
                                         # (needs ffmpeg, rsync, rsnapshot installed)
python3 tools/gen_docs.py                # UML class diagrams + API reference
```

Branching: `main` holds releases only (tag `vX.Y.Z`), `develop` integrates,
work happens on `feature/*` branches with pull requests into `develop`.
Details in [CONTRIBUTING.md](CONTRIBUTING.md).

Coding standard: one class per module, Google-style docstrings for every
class, method and function (checked by pylint's `docparams` extension),
maximum line length 100, standard library only, external programs called
without a shell.

```text
src/mediatools/
├── cli.py                  Cli               argument parsing and dispatch
├── core/                   Config, DataDir, RunLog, MediaTypes, MediaProbe, TreeWalker, Prompt
├── shrink/                 ShrinkDatabase, DatabaseRenamer, HevcEncoder, Shrinker, ProgressReport
├── triage/                 CandidateFinder, PlayerSession, FlatLinkResolver,
│                           FileRemover, KeptList, Triage
├── flatten/                FlatNamer, FlatIndex, FlatPlan/PlanEntry, Flattener
├── sync/                   FsCheck, TargetPicker, RsyncRunner, TargetReport, Syncer
└── snapshot/               SnapshotSettings, RsnapshotConf, SyncProbe, LevelSchedule,
                            Snapshotter, SystemdTrigger
```

Documentation:

| Document | Content |
|---|---|
| [`docs/handbook.md`](docs/handbook.md) | user handbook: installation, every subcommand, safety design |
| [`docs/developer.md`](docs/developer.md) | architecture, UML class/sequence/state/activity diagrams, data formats, testing, release |
| [`docs/api.md`](docs/api.md) | API reference generated from the docstrings |

## License

MIT, see [LICENSE](LICENSE).
