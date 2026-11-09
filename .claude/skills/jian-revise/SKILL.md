---
name: jian-revise
description: Apply natural-language feedback to an existing jian cut ("S4 drags", "cut 12:30-13:10", "move the Kafka example earlier"). Use when the user runs /jian-revise <name> "<feedback>".
argument-hint: <video name> "<what to change>"
---

# /jian-revise <name> "<feedback>"

Change only what the feedback asks for. Everything else in the cut stays exactly as it is.

**Running jian**: `jian <command> <name>` (or `~/.local/bin/jian` when that isn't on PATH); `<name>` is a folder
under `videos_root` or a full path. `jian status <name>` prints the work folder (`<name>/jianji/`, or `jianji/` for
videos started before the rename; written `jianji/` below) and the style guide.

1. `jian status <name>`; read the latest `jianji/edits/edit.vNNN.json`, `jianji/review.md` and `jianji/feedback.log`.
2. Map the feedback to the edit. Timecodes refer to the review render: `jian at <name> 12:30-13:10` lists the pieces, clips, sections and word ids there. Section ids (S4) and clip ids (c31) are burned into the review render too.
3. Write the next version (`edit.v{N+1}.json`), a copy of the latest with only the requested changes. Use `jian words` when trimming inside a phrase.
   After a `jian pull` (the user edited in Resolve), clips, B-roll and texts carry explicit frames:
   `"locked"` marks the user's hand edit; never change it unless the feedback names it. `"pinned": true` is jian's
   own cut frozen at the frames they saw; change it freely when the feedback needs to (its `words` range says which
   words it holds; replace it with a `from`/`to` clip to re-cut). `jian diff <name>` shows what they changed in
   Resolve: read it before revising, their edits are the best hint of what they want.
4. `jian compile <name>`, then `jian cut <name>`. Only changed pieces re-encode; nothing is re-transcribed. A
   caption- or text-only change reuses the master and its sound checks.
5. Fix QA fails the same way as /jian-edit: at most 2 extra cut versions, fails first, then visible problems, then
   cosmetics; caption/text-only fixes don't count and go last.
6. Append to `jianji/feedback.log`: date, the feedback verbatim, what you changed (clip ids, before/after runtime).
   Then count the round for the scoreboard: `jian log <name> feedback "<the feedback, one line>"`.
7. Update `jianji/review.md`: bump the version, add a "Changes in vN" section under the QA line (jian keeps that line
   current), refresh timecodes that moved.
8. Never write outside the work folder: footage and brief.md are the user's.
9. Report: what changed, new runtime, QA status line, anything you interpreted rather than followed literally.

Feedback that is about style in general ("you always cut my jokes") goes in the feedback log **and** gets proposed as a
style-guide change in the report. Don't edit a style guide without the user's OK (theirs live in `~/.config/jianji/styles/`).
