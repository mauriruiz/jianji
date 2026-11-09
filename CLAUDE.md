# jianji: notes for Claude sessions

- **Two repos.** Code lives here (jianji, public). The maintainer's personal settings (profiles, style guides,
  glossary, brand, notes) live in a separate private repo linked to `~/.config/jianji`. Code changes go here; a
  change to someone's own preferences goes in their layer, never in the built-in profiles or styles. Every PR says
  which repo it touches and why.
- **Nothing personal in this repo**: no real names, paths, footage, video titles or e-mails. The pre-commit hook
  checks against `~/.config/jianji/denylist.txt` and the generic patterns in `.githooks/leakcheck`.
- Before saying a feature doesn't exist, check open PRs and branches (`gh pr list`, `git branch -a`) and the
  worktrees under `.claude/worktrees/`.
- A video folder belongs to the user: write only inside `<video>/jianji/` (or `vedit/` in folders from before the
  rename). Never touch footage or brief.md.
- Bump `SCHEMA` and add a `MIGRATIONS` note in `jianji/core.py` when the user's layer format changes.
- Commits: one short line, no AI attribution. Branch + PR per piece of work.
