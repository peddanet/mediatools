# Contributing

## Branching model

| Branch           | Purpose                                         | Created from | Merged into               | How |
|------------------|-------------------------------------------------|--------------|---------------------------|-----|
| `main`           | releases only; every release is a tag `vX.Y.Z`  | -            | -                         | only a commit whose required checks passed |
| `develop`        | integration of finished features                | `main`       | `main` (through a release)| only a commit whose required checks passed |
| `feature/<name>` | one feature or fix                              | `develop`    | `develop`                 | `tools/release.sh` |
| `hotfix/<name>`  | urgent fix of the released version              | `main`       | `main` + `develop`        | `tools/release.sh` |
| `release/X.Y.Z`  | short-lived: carries the locally built release commit to GitHub for its checks | `develop` | `main` | made by `tools/release.sh` |
| `merge/vX.Y.Z`   | short-lived: a second `develop` state (hotfix while `develop` collects features) or the fallback merge back | `main` | `develop` | made by `tools/release.sh` |

Rules:

1. `main` and `develop` are protected on GitHub: they take only a commit
   whose required checks (`gates (3.12)`, `gates (3.14)`) passed on a pull
   request, also from the owner. `tools/release.sh` does all of it
   (dev-conventions bundle v21: one CI run per release, then a direct push
   of exactly the checked commit).
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
sudo apt install ffmpeg rsync rsnapshot python3-tk xvfb shellcheck poppler-utils tesseract-ocr  # once: tests, shellcheck, privacy scan of PDFs and images
tools/check.sh                          # all gates
tools/check.sh --fast                   # without tests, while editing
tools/check.sh --yes                    # no question: tests on the free threads
~~~

**Parallel tests.** The tests run on several workers (pytest-xdist,
`-n N --dist loadscope`). In a terminal `tools/check.sh` asks
`N of M threads free (load L) - run the tests on N workers? [Y/n]` (free =
threads minus the rounded load of the last minute); without a terminal
(pre-push hook, `release.sh --yes`) or with `--yes` it takes the free
threads without asking, in CI all threads of the runner (4 on a public
repository). `CHECK_WORKERS=N` fixes the number, `CHECK_WORKERS=1` runs
them one after another. The tests of files copied unchanged from the
dev-conventions bundle are skipped while `sha256sum -c tools/bundle.sha256`
passes (`tools/bundle_tests.py`); `CHECK_BUNDLE_TESTS=1` runs them anyway.
The coverage table appears only when the coverage is below 90 %.

**Each tree is checked in full once.** After a full green run
`tools/check.sh` remembers the tree of the working copy (uncommitted and
untracked files included), the Python version and a hash of the installed
packages in `.git/checked-trees`; for a known key only `privacy` and
`shell` run again and the script says what it skipped. `release.sh` and
the pre-push hook call it several times for the same code; since bundle
v21 a release needs one full run (on the release tree). `CHECK_FULL=1 tools/check.sh` forces
a full run; CI always runs in full.

`tools/check.sh` activates the development venv itself for its own run
(an active venv first, else `.venv` in the project, else the older
`~/.venvs/mediatools`); the terminal stays as it was, no `source` needed.
`sudo` only for `apt`: it installs system packages.

| Gate     | Tool         | Pass criterion                                  |
|----------|--------------|-------------------------------------------------|
| privacy  | privacy_scan | no locally listed term in files (PDFs via `pdftotext`, text in images via `tesseract`), paths, commit messages, author/committer/tagger; every identity address no-reply (or in the allow list); **fails without local terms or without `pdftotext`/`tesseract`** (skipped only in CI); refuses while old lists exist |
| shell    | shellcheck   | no finding in any shell script                  |
| format   | pycodestyle  | no finding, max line length 120                 |
| lint     | pylint       | 10.00/10 incl. Google-style docstring checks    |
| types    | mypy         | no error (`check_untyped_defs`)                 |
| security | bandit       | no finding (B404/B603 justified in pyproject)   |
| tests    | pytest       | all pass, no resource leak, branch coverage >= 90 % |

## Local files of a developer

A fresh clone is enough to develop, test, release and install: every file
the program needs at run time lies in the repository (icons and the
templates of the menu entry and of the systemd unit in
`src/mediatools/data/`, package data). Only private files stay outside;
each has a sample with invented content in `examples/`, kept in step with
the code by `tests/test_examples.py`. The folders are explained in the
handbook, [Data directory](docs/handbook.md#data-directory) (XDG layout:
`~/.config`, `~/.local/state`, `~/.local/share`, `~/.cache`).

| Path on the machine | Written by | Sample in the repository | Needed for |
|---|---|---|---|
| `~/.config/mediatools/mediatools.ini` | first start of `mediatools`, `mediatools config --set` | `examples/config.ini.sample` (= built-in template) | settings; `[collection] root` also gives the privacy scan its folder names |
| `~/.config/mediatools/privacy/denylist.txt` | you (mode 600) | `examples/privacy/denylist.sample.txt` | privacy gate: terms that must not appear; **without terms the gate fails** |
| `~/.config/mediatools/privacy/allowlist.txt` | you (mode 600) | `examples/privacy/allowlist.sample.txt` | privacy gate: allowed phrases, and addresses accepted in git identities |
| `.venv/` in the clone | `python3 -m venv .venv` (in `.gitignore`) | - | development tools; `tools/check.sh` activates it |
| `.git/checked-trees` | `tools/check.sh` after a full green run (last 50) | - | check cache: a tree is checked in full once |
| `.git/config` (`core.hooksPath`, `user.name`, `user.email`) | `git config` | - | pre-push hook; identity = your GitHub login and its no-reply address |
| `~/.local/state/mediatools/`, `~/.local/share/mediatools/`, `~/.cache/mediatools/` | mediatools while it runs | - (tests use temporary folders) | logs, state, kept data, caches |

Create the privacy lists once from the samples, then replace the invented
terms by your own (normal user; `-n` never overwrites an existing list,
`chmod 600` because the lists themselves are private):

~~~bash
mkdir -p ~/.config/mediatools/privacy && \
    cp -n examples/privacy/denylist.sample.txt ~/.config/mediatools/privacy/denylist.txt && \
    cp -n examples/privacy/allowlist.sample.txt ~/.config/mediatools/privacy/allowlist.txt && \
    chmod 600 ~/.config/mediatools/privacy/denylist.txt ~/.config/mediatools/privacy/allowlist.txt
~~~

Set the no-reply identity once per clone (normal user; writes only
`.git/config`). `<login>` is your GitHub login, `<id>+<login>@users.noreply.github.com`
the address GitHub shows under Settings > Emails ("Keep my email address
private"):

~~~bash
git config user.name '<login>' && \
    git config user.email '<id>+<login>@users.noreply.github.com'
~~~

## Integrating and releasing (`tools/release.sh`)

All steps run as a normal user; `git` and `gh` (logged in once with
`gh auth login`) act on the clone and on GitHub with your own account.

| Current branch   | What the script does                                                   |
|------------------|------------------------------------------------------------------------|
| `feature/<name>` | rebase onto `develop`, then "Build the release now? [y/N]": `y` merges and releases, `n` asks whether to merge into `develop` only |
| `develop`        | release of the collected features: asks the part (default minor) and the release message |
| `hotfix/<name>`  | patch release into `main` and back into `develop`                       |
| `main`           | refused                                                                |

Everything is built locally, as on Forgejo: the feature is merged into
`develop` (`--no-ff`), version and CHANGELOG heading are set on
`release/X.Y.Z`, the gates run once, the release commit is merged into
`main` with the message `mediatools X.Y.Z: <release message>`, tagged
`vX.Y.Z`, and `develop` moves to the same commit. Then `release/X.Y.Z` is
pushed, its pull request runs the required checks once on exactly that
commit, and `git push --atomic` publishes `main`, `develop` and the tag
(all or nothing). If GitHub refuses the direct push (a rule wants pull
requests), the script merges the pull request instead and brings
`develop` back through `merge/vX.Y.Z`, as up to bundle v20. If somebody
pushed to `main` or `develop` meanwhile, nothing is published and local
`main`/`develop` go back to the state of origin. At the end
`tools/same-code.sh` checks that `main`, `develop` and the tag are
bit-identical, and `./install.sh vX.Y.Z` is offered (then
`tools/after-install.sh`: reload of a running shrink). Every question
rings the terminal bell and raises a desktop notice (`attention` of
`tools/output.sh`; `NO_NOTIFY=1` switches the notice off).

Review of a delivered branch before its release (four-eyes, Beyond
Compare, both sides as real copies with named sides):

~~~bash
tools/review.sh develop feature/<name>
~~~

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
