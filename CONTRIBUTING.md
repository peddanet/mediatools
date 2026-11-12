# Contributing

## Branching model

| Branch        | Purpose                                   | Created from | Merged into | Who merges  |
|---------------|-------------------------------------------|--------------|-------------|-------------|
| `main`        | releases only; every commit is tagged     | -            | -           | integrator  |
| `develop`     | integration of finished features          | `main`       | `main`      | integrator  |
| `feature/<x>` | one feature or fix, short-lived           | `develop`    | `develop`   | via PR      |
| `release/<x>` | version, CHANGELOG, PDFs of a release     | `develop`    | `main` (PR) | integrator  |
| `hotfix/<x>`  | urgent fix of a release                   | `main`       | `main` + `develop` | integrator |

Rules:

1. Nobody commits directly to `main` or `develop`; changes arrive by pull
   request (feature -> develop) or release merge (develop -> main).
2. Every push to a branch runs the quality gates locally (pre-push hook);
   CI runs them for every pull request and for every merge into `develop`
   and `main`. A pull request can only be merged with green CI (branch
   protection).
3. A release is an annotated tag `vX.Y.Z` on `main`; CI verifies that the
   tag is on `main` and equals `__version__`.

Diagram and command reference: `docs/developer.md`, sections
"Branching model" and "Release procedure".

## Quality gates

One script, used locally and in CI:

~~~bash
pip install -e '.[dev]'                 # once, normal user (or in a venv)
git config core.hooksPath .githooks     # once per clone: activate pre-push hook
sudo apt install ffmpeg rsync rsnapshot python3-tk xvfb  # once: integration and GUI tests
tools/check.sh                          # all gates
tools/check.sh --fast                   # without tests, while editing
~~~

| Gate     | Tool        | Pass criterion                                  |
|----------|-------------|-------------------------------------------------|
| privacy  | privacy_scan | no locally listed term in files, paths, commit messages |
| format   | pycodestyle | no finding, max line length 100                 |
| lint     | pylint      | 10.00/10 incl. Google-style docstring checks    |
| types    | mypy        | no error (`check_untyped_defs`)                 |
| security | bandit      | no finding (B404/B603 justified in pyproject)   |
| tests    | pytest      | all pass, no resource leak, branch coverage >= 90 % |

## Coding rules

1. One class per module, Google-style docstring on every module, class,
   method and function.
2. No new runtime dependencies - standard library only; external programs
   (ffmpeg, rsync, gio, players) are called with argument lists, never
   through a shell.
3. Every behaviour change comes with a test in `tests/`; regenerate the
   diagrams and API reference with `python3 tools/gen_docs.py`.
4. Safety rules in the README ("Safety model") must not be weakened.
