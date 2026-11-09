---
name: jian-edit
description: Turn a video folder (<videos_root>/<name>/footage) into a first cut with jianji. Use when the user runs /jian-edit <name> or asks to edit a video folder.
argument-hint: <video name or folder path>
---

# /jian-edit <name>

You are the editor. The `jian` CLI does every mechanical step (probe, transcription, rendering, QA); you make the
editorial decisions from compact text views. Never decode or watch media yourself; look at frames only where QA or a
decision needs it.

**Running jian**: installed with `uv tool install`; call it as `jian`, or `~/.local/bin/jian` when that isn't on
PATH. `<name>` is a folder under `videos_root` (`~/.config/jianji/config.toml`) or a full path. `jian status <name>`
prints the work folder and the style guide to follow (the user's own, from `~/.config/jianji/styles/`, or the
built-in one).

**The work folder** is `<name>/jianji/` (videos started before the rename: `<name>/vedit/`; `jian status` prints
it). Paths below are written `jianji/...`.

**The video folder is the user's**: never move, rename or write anything outside the work folder (not the footage,
not brief.md). Footage dumped at the folder root is read where it is. Per-video settings you need (a glossary they
didn't write, a profile override) go in `jianji/config.toml`; tell the user so they can move them into the brief.

## 1. Prepare

1. `jian status <name>`. Read `brief.md` if it exists (new folders: `jian new <name>` writes a commented template). No brief: infer profile, language, audience and target length, and list every assumption in review.md. A brief jian can't parse stops `prep` with the reason; report it, don't edit the brief.
2. `jian prep <name>`. Long footage takes minutes: run it in the background and wait for it. It caches by content and resumes, so rerunning is free.
3. Read `jianji/catalog.md`, `jianji/transcript.md`, `jianji/broll.md` and the style guide `jian status` names.

## 1b. Streams (profile `stream`)

A long livestream is several threads interleaved, not one story. Follow the stream style guide:
1. `jian map <name>` (background: OCR, activity and the vision model over the whole stream) writes
   `jianji/stream.md`, one row per minute. Read it with `transcript.md`.
2. Write `jianji/stream_map.md`: spans (label, range, what and which files), threads (the spans of one task, as
   goal → attempt → obstacle → resolution → result), VOD chapters, and a second video if the material has one.
3. Pick 2-4 threads and plan the recap by thread: chapter cards between threads, context texts for goal, obstacle
   and result, sped-up bridges (`"speed": 8` on a non-speech clip) with "X minutes later…", chat questions as
   `"style": "chat"` cards, `"crop"` zooms where code matters, `"blur"` boxes for anything private.
4. Write `jianji/pickups.md` (4-6 lines in the streamer's voice for them to record into `footage/pickups/`) and
   `jianji/shorts.md` (Short candidates). Never generate anyone's voice.
5. In review.md: the threads and why, every reorder (jian lists them in "Told out of order") and the privacy check.

## 2. Plan the story

Write the section plan first: ordered sections, each with a title, the word ranges it draws from, and one line on why
it's there. Check it against the style guide (hook first, every section earns its place).

If `jian status` shows `outline_checkpoint=True`: write the plan to `jianji/outline.md` (sections, rough timings,
what you'd cut and why), tell the user, and **stop** until they approve or change it.

## 3. Write the edit

Create `jianji/edits/edit.vNNN.json` (`jian status` names the next file):

```json
{
  "v": 1, "profile": "explainer",
  "sections": [{"id": "S1", "title": "Hook: why sagas fail", "why": "strongest claim, said at 12:40"}],
  "clips": [
    {"id": "c1", "sec": "S1", "src": "A", "from": 1043, "to": 1077, "cut": [[1050, 1051]],
     "why": "cleanest take", "conf": 0.9},
    {"id": "c2", "sec": "S1", "src": "B", "in": 3.0, "out": 7.5, "why": "non-speech: seconds, not word ids"}
  ],
  "overlays": [{"id": "b1", "src": "C", "in": 4.0, "out": 8.5, "over": "c1", "word": 1060, "gain_db": -20,
                "why": "the whiteboard while I explain the outbox"}],
  "markers": [{"at": "c1", "kind": "uncertain", "note": "kept the aside about Kafka, check it"}]
}
```

- Clips play in list order. `from`/`to` are inclusive word ids from transcript.md; `cut` removes word ranges inside a clip (false starts, discourse fillers). Use `jian words <name> <src> <a> <b>` to see every id in a range before cutting inside a phrase.
- Hesitation fillers, untranscribed vocal sounds and cue words are removed automatically; `"keep": ["filler"]` or `["voiced"]` on a clip opts out.
- Never write timestamps for speech; compile places every edge.
- End every clip (and every `cut` range) on a clause or sentence end, never mid-sentence: compile runs a clip that
  stops mid-sentence on to its end (up to 1.5 s) and warns where it can't. A long hesitation inside a clause stays.
  Only a jump to another topic with no pause may cut tighter: `"abrupt": "<why>"` on the clip (review.md lists it).
- Nothing may be on screen under 1 s, talking head included. A piece between two jump cuts that short: merge it
  (`"keep": ["pauses"]` on that part, splitting the clip if needed), cover it with B-roll or drop it.
- `conf` < 0.7 means "flag this in review.md".

**B-roll** (`overlays`): picture from another source while the narration keeps playing. One layer at a time.
- Start from `broll.md`: the vision model's notes are a first pass. Shortlist, then open the sheet image of each shortlisted source (Read the jpg) before using it. `jian sheet <name> <src> <a> <b>` makes a denser sheet of a range.
- `in`/`out` are seconds in the B-roll source: take a steady stretch (the sheet's BEST range), usually 2-6 s.
- `"shot"`: label every overlay and every picture clip (`in`/`out`) `place` (facade, counter, sign, street),
  `detail` (a product, a dish, a close-up), `action` (hands at work, pouring, a pan flip) or `reaction`, from the vision notes and the
  sheet. The profile's `[shots]` holds each type a minimum (place 2 s, detail 1.2 s, action and reaction until the
  motion settles); compile extends a short one in its source and warns where it can't. Plan stretches that long.
- Anchor with `over` (a clip id) + `word` (a kept word id in that clip, the word the picture illustrates), or `at` seconds from the clip's start. An overlay may run across later cuts; that's a good way to hide a jump cut.
- `gain_db` mixes the B-roll's own sound under the voice (-20 is a light bed); omit it to keep the B-roll silent.
- Follow the B-roll rules in the style guide.

**Picture-led cuts** (profile `short`, vertical): the profile's `[timeline]` sets 9:16 whatever the sources are.
- Non-speech clips (`in`/`out`) carry their own natural sound; `"gain_db"` on a clip lifts or lowers it.
- `texts`: `{"id": "t1", "text": "Las hacen al momento…", "over": "p1", "at": 0.1}`, anchored like overlays
  (`word` or `at`). `"style": "hi"` is a callout (place, price). One text per style at a time. Leave `dur` out:
  each text gets its reading time, max(1.5 s, 0.4 s + characters ÷ 13), at most 4.5 s, and runs on across shot
  changes; a text that needs longer is too long, so shorten its words. `final.vNNN.mp4` burns them in with the
  captions, clean, ready to upload.
- `"fix": {"27": "Kubernetes", "28": ""}` on a speech clip corrects caption text for misheard words ("" drops one);
  cuts don't change, and QA checks the render against the fixed text. Glossary terms (brief `glossary:`) are
  corrected automatically in captions and texts; `fix` is for everything else.
- Captions and texts keep glossary terms, names and "13 euros" on one line, and wrap to two balanced lines.
  QA measures every burned text box against the profile's `[safe_zone]`; the review render shows the zone as a faint frame.
- Chain B-roll back to back frame-exactly (read the piece frames from `timeline.vNNN.json`), or the talking head
  flashes for a frame between shots.

## 4. Compile, render, check

1. `jian compile <name>`: fix every rejection, read every warning. A RETAKE warning means you kept a dropped take;
   "A-roll flashes between b3 and b4" or "b_rig ends early" mean breathing room moved chained B-roll: re-chain it;
   "section ... give it a B-roll beat" means the speaker runs on into a new section: give it one. "shot held" and
   "on screen ... its reading time" are compile keeping a shot or text readable: check the chain still works.
   "a place shot wants 2.0s" means it couldn't: give it room (a longer stretch, unchain the next shot) or accept it.
   "runs on to ..." is a sentence finished for you; "lands mid-sentence" and "on screen 0.7s ... (min 1.0s)" are QA
   fails waiting to happen: fix them in the edit.
2. `jian cut <name>`: renders master, review render and captions, places burned text off faces and the subject, then
   runs QA and prints one status line. The placement decisions land in review.md ("Text placement").
3. Reading QA: `fail` means a confirmed problem; fix it. `warn` means look if it matters: `asr_diff` is Whisper
   re-hearing the render and is only worth a look where `clipped_words` also flags; fast captions come from fast speech.
   `speech_cuts` fails a speech cut that ends mid-sentence or keeps less air than its minimum (measured on the
   render) unless the clip is marked `abrupt`; `fragments` fails a cut through speech nobody kept (voice-level sound
   past the kept words where Whisper hears a word the edit doesn't have: a false start, a word it never wrote);
   `short_segments` fails anything on screen under 1 s. A sentence is never cut halfway to save time: run over the
   target length (up to ~15% on shorts) instead. `text_faces` fails text over a face: move or
   re-anchor the text to a shot with room. `text_readable` fails a text up less than its reading time and warns one
   faster than 13 chars/s (too many words: shorten them). `shot_readable` counts picture shots under their type's hold.
4. Listen to every join: `jian cuts <name>` makes `renders/cuts.vNNN.mp4` (each cut with 1.5 s each side, labelled
   with its class and gap). With on-screen texts, `jian texts <name>` makes `renders/texts.vNNN.mp4` (each text in
   context, labelled with its time and chars/s). Mention both in your report.
5. Review loop, **at most 2 extra cut versions**, fixing in this order: QA fails, then visible problems (look at frames
   only where QA or a decision needs it: a jump at a cut, a B-roll choice), then cosmetics. Anything still failing goes
   into review.md as a known issue.
6. Caption-only and text-only fixes (spellings, `fix`, on-screen wording) **don't count** against those 2: batch them
   after the last cut iteration as one more version. `jian cut` sees the picture and sound didn't change, reuses the
   master and its sound checks, and only re-renders captions, review and final.

## 5. Write `jianji/review.md`

```
# <name>: first cut v00N
QA: pass (2 warnings: captions, asr_diff) · v00N      <- written and kept current by jian; leave it as the 2nd line
Runtime, raw speech used, pieces, profile. Files: review render, master, captions (paths).
## Assumptions        (missing brief, language, audience, target length)
## Structure          (S1..Sn: review-render timecode, title, why)
## Key decisions      (what was cut, reordered or kept, and why)
## B-roll             (each overlay: review timecode, source, what it shows, why there; vertical or soft shots flagged)
## Check these        (uncertain edits with review timecodes, conf < 0.7, QA warnings worth a look)
## Unused material worth a look   (good lines, takes or B-roll left out, source + time)
## QA                 (one line per check that isn't pass, with what you concluded)
## Next time, film this
```

Timecodes in review.md always refer to the **review render** (burned in), so the user's feedback can point at them.

## 6. Report

Tell the user: runtime, where the review render is, the 3-5 decisions most worth checking, QA status, known issues.
The upload file is `renders/final.vNNN.mp4` (shorts: captions burned in; horizontal: texts burned in, captions as
the SRT). To change it by hand in DaVinci Resolve: `jianji/resolve/IMPORT.md` (every cut writes it); back with
`jian pull <name>` and `jian diff <name>`.
Ask them to log their review time later with `jian log <name> review_minutes <n>`.
