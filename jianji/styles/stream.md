# Stream recap style guide

Recaps of long livestreams: 1-3 h of coding, chat and diagrams cut into a video that tells a
story. Horizontal, YouTube. Reusable rules only; feedback about one video stays in that video's `jianji/feedback.log`.
Rules change only after you approve a proposed update. Everything in the explainer guide about cuts, breathing,
fillers and text on screen applies; this guide adds what a stream needs.

## A stream is threads, not one story

- **Map first.** `jian map <name>` writes `jianji/stream.md`: per minute, words spoken, what's on screen (editor,
  terminal, browser, diagram), files and windows, the first words said, cues said aloud, the vision model's note at
  each change of screen, and privacy hits. From it and `transcript.md`, write `jianji/stream_map.md`:
  1. **Spans:** label every stretch of the stream as feature work, debugging, design/diagram, chat Q&A or
     setup/idle, with its time range and a one-line description: what is being done, which files.
  2. **Threads:** group the spans that belong to the same task, even far apart ("auth refactor: 1:25-1:32,
     1:40-1:51"). A thread is told as a mini-arc: **goal → attempt → obstacle → resolution → result**. Name the
     span that carries each step.
  3. **VOD chapters:** one line per span worth a chapter, in stream time. These are the full VOD's YouTube
     chapters if you upload it.
- **Choose 2-4 threads** with the clearest arcs: something that works, a real obstacle, a decision explained.
  If the stream has more good material, don't cram it: propose a second video in `stream_map.md` (which threads,
  why). Two coherent videos beat one jumpy one.
- **Order by thread, not by clock.** Each thread plays as one block. Moving a span earlier or later is allowed
  when it makes the thread clearer, never to change what happened (a fix doesn't come before its bug). jian lists
  every section told out of order in review.md ("Told out of order"); say why in Key decisions.

## Glue: the viewer must always know where they are

- **Context texts** (`txt`): the thread's goal at its start ("Goal: validate the config"), the obstacle when it
  appears ("Problem: the borrow checker says no"), the result ("Result: it fails as it should").
- **Chapter cards** (`"style": "chapter"`) between threads: 2-4 words, on the thread's first shot.
- **Silent work becomes a bridge**: a sped-up stretch of the screen (`"speed": 8`, about 1-3 s on screen) with a
  text "40 minutes later…". jian puts the stream's own room tone under it, never silence.
- **Chat:** when the streamer answers a question from chat, show the question as a card (`"style": "chat"`) when
  the answer starts: "Chat: why not use a library?". Keep only questions whose answer adds something.
- **Pickup voice-over** (recommended): write 4-6 short lines in the streamer's voice (intro, a line between threads,
  outro) into `jianji/pickups.md`, each with where it goes. The streamer records them in one take on the stream mic
  and drops the file in `footage/pickups/`. After `jian prep`, use them as clips from that audio source, with the stream's picture over
  them as B-roll (an overlay). Never generate anyone's voice.

## Code must be readable

- Where code matters (the line that broke, the fix), zoom: `"crop": [x0, y0, x1, y1]` (fractions of the source
  frame) on the clip or the B-roll. Start from `stream.md`'s "Where the activity is". At 1080p, keep the crop at
  least ~40% of a 1440p source's width (≥ 1:1 pixels) so text stays sharp.
- Don't zoom on talk over a static screen: zoom when the code is the point.

## Privacy

- Never publish keys, tokens, `.env` contents, e-mails, personal notifications, or anything on the brief's
  `never_show:` list (customer data, internal URLs). `stream.md` lists what the map saw; QA reads
  the cut every 2 s and fails on anything, with the box to blur. Fix it with `"blur": [[x0, y0, x1, y1]]` (fractions
  of the output frame) on the clip or B-roll, or cut it. Said aloud (never_show words in speech) is cut, not blurred.
- Future plans and ideas the streamer may not want public: keep only if the brief allows, and flag them in review.md.

## Outputs per stream

`jianji/stream_map.md` (spans, threads, VOD chapters, a second video if there is one), the recap
(`renders/final.vNNN.mp4`), `jianji/pickups.md` (the pickup script) and `jianji/shorts.md` (Short candidates for
later: moment, time range, why it works alone).
