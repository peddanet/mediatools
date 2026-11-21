# Contributing

## Branching model

| Branch           | Purpose                                         | Created from | Merged into               | How |
|------------------|-------------------------------------------------|--------------|---------------------------|-----|
| `main`           | releases only; every release is a tag `vX.Y.Z`  | -            | -                         | pull request only |
| `develop`        | integration of finished features                | `main`       | `main` (through a release)| pull request only |
| `feature/<name>` | one feature or fix                              | `develop`    | `develop`                 | `tools/release.sh` |
| `hotfix/<name>`  | urgent fix of the released version              | `main`       | `main` + `develop`        | `tools/release.sh` |
| `release/X.Y.Z`  | short-lived: version and CHANGELOG heading      | `develop`    | `main`                    | made by `tools/release.sh` |
| `merge/vX.Y.Z`   | short-lived: the release back into `develop`    | `main`       | `develop`                 | made by `tools/release.sh` |

Rules:

1. `main` and `develop` are protected on GitHub: changes only through a
   pull request whose required checks (`gates (3.12)`, `gates (3.14)`)
   are green, also for the owner. `tools/release.sh` does all of it.
2. Every push of a branch runs the quality gates locally (pre-push hook,
   never bypassed with `--no-verify`); CI runs them for every pull request.
3. The version lives only in `src/mediatools/__init__.py` and is set only by
   `tools/release.sh`; every change adds lines under `## [Unreleased]` in
   `CHANGELOG.md`.
4. A release is an annotated tag `vX.Y.Z` on `main` with the release
   message; CI verifies that the tag is on `main` and equals `__version__`.

Diagrams: `docs/developer.md` ("Branching model", "Release procedure") and
`docs/ci-guide.md` (lane diagrams).

## Quality gates

One script, used locally, by the pre-push hook and in CI:

~~~bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # once: development venv
git config core.hooksPath .githooks     # once per clone: activate pre-push hook
sudo apt install ffmpeg rsync rsnapshot python3-tk xvfb shellcheck  # once: tests, shellcheck
tools/check.sh                          # all gates
tools/check.sh --fast                   # without tests, while editing
~~~

`tools/check.sh` activates the development venv itself for its own run
(an active venv first, else `.venv` in the project, else the older
`~/.venvs/mediatools`); the terminal stays as it was, no `source` needed.
`sudo` only for `apt`: it installs system packages.

| Gate     | Tool         | Pass criterion                                  |
|----------|--------------|-------------------------------------------------|
| privacy  | privacy_scan | no locally listed term in files, paths, commit messages; refuses while old lists exist |
| shell    | shellcheck   | no finding in any shell script                  |
| format   | pycodestyle  | no finding, max line length 100                 |
| lint     | pylint       | 10.00/10 incl. Google-style docstring checks    |
| types    | mypy         | no error (`check_untyped_defs`)                 |
| security | bandit       | no finding (B404/B603 justified in pyproject)   |
| tests    | pytest       | all pass, no resource leak, branch coverage >= 90 % |

## Integrating and releasing (`tools/release.sh`)

All steps run as a normal user; `git` and `gh` (logged in once with
`gh auth login`) act on the clone and on GitHub with your own account.

| Current branch   | What the script does                                                   |
|------------------|------------------------------------------------------------------------|
| `feature/<name>` | rebase onto `develop`, gates, then "Release jetzt bauen?": `j` merges and releases, `n` asks whether to merge into `develop` only |
| `develop`        | release of the collected features: asks the part (default minor) and the release message |
| `hotfix/<name>`  | patch release into `main` and back into `develop`                       |
| `main`           | refused                                                                |

Merging means: push the branch, pull request, wait for the required
checks, merge on GitHub. A release sets version and CHANGELOG heading on
`release/X.Y.Z`, runs the gates, merges into `main` with the message
`mediatools X.Y.Z: <release message>`, tags `vX.Y.Z`, waits for the
release check, brings `develop` back up through `merge/vX.Y.Z`, checks
with `tools/same-code.sh` that `main`, `develop` and the tag are
bit-identical and offers `./install.sh vX.Y.Z` (then
`tools/after-install.sh`: reload of a running shrink).

Which part: `patch` corrects wrong behaviour (also tests, scripts,
documentation only); `minor` adds something usable while everything else
keeps working; `major` makes something that worked before stop working or
behave differently. The effect decides, not the place of the change.

~~~bash
git switch develop && git pull --ff-only && git switch -c feature/<short-name>
# ... change code and tests, add a CHANGELOG line under [Unreleased] ...
git add -A && git commit -m "<what and why>" && tools/release.sh
~~~

Hotfix: `git switch main && git pull --ff-only && git switch -c hotfix/<name>`,
fix, then `tools/release.sh -m "<release message>"`. Without questions:
`tools/release.sh minor -m "<message>" --yes`; merge only:
`tools/release.sh --merge-only --yes`. `tools/release.sh --help` lists
everything.

Patches delivered by Claude come as `mediatools-feature-<name>.tar.gz`
(or `-hotfix-`) with `patch.info` (branch, base and result hashes, part,
message), never with a version: `mediatools update` checks the hashes,
applies them and runs `tools/release.sh` with that part and message.

## Coding rules

1. One class per module, Google-style docstring on every module, class,
   method and function.
2. No new runtime dependencies - standard library only; external programs
   (ffmpeg, rsync, gio, players) are called with argument lists, never
   through a shell.
3. Every behaviour change comes with a test in `tests/`; regenerate the
   diagrams and API reference with `python3 tools/gen_docs.py`.
4. Safety rules in the README ("Safety model") must not be weakened.
