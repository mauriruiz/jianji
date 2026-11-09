# Contributing

Thanks for helping. A few things keep jianji maintainable.

## Setup

```sh
uv sync
git config core.hooksPath .githooks     # leak guard before each commit, full suite before each push
uv run pytest                           # fast suite (about a minute, no models)
JIANJI_SLOW=1 uv run pytest             # with the local models (Whisper, aligners, vision): run before a PR
```

## Principles

- **The CLI never calls a language model.** Editorial judgment lives in the skills (`.claude/skills/`) and the style
  guides (`jianji/styles/`); everything the CLI does is deterministic and testable.
- **QA fails only on confirmed problems.** A check that cries wolf gets ignored. New checks start as warnings until
  real footage shows they're right.
- **Never write outside `<video>/jianji/`.** Footage and brief.md belong to the user.
- **Your preferences go in your layer** (`~/.config/jianji/`), not in the built-in profiles or style guides. Built-in
  defaults should suit anyone.
- **Fixtures stay license-clean**: synthetic or public domain, with the source in `NOTICE` and
  `tests/fixtures/make_golden.sh`. Never commit real footage.
- **Bug fixes come with a regression test**, as small as the bug allows.

## Changing the format of the user's layer

If a change renames or removes a profile key, or changes what a file in `~/.config/jianji/` means: bump `SCHEMA` in
`jianji/core.py` and add a line to `MIGRATIONS` saying what to edit. `jian doctor` shows it to everyone whose layer
is older.

## Pull requests

One topic per PR, a short description of what and why, tests passing with `JIANJI_SLOW=1`. Commit messages: one
short line (`fix(resolve): escape quotes in FCPXML attributes`).
