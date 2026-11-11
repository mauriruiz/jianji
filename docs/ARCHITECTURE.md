# Architecture

jianji turns a folder of raw footage into a first cut. It has two halves:

- **The editor**: Claude, inside the Claude Code skills `/jian-edit` and `/jian-revise`. It reads compact text views
  of the footage (transcript, catalog, vision notes, contact sheets), plans the story and writes an edit file.
- **The CLI** (`jian`): deterministic Python. Probing, transcription, frame-exact cutting, rendering, QA and the
  DaVinci Resolve round trip. It never calls a language model; the only models it runs are local (Whisper,
  wav2vec2, a vision model).

The boundary is the edit file: the model chooses *what* (word ids, sources, reasons), the CLI decides *where* (frames,
fades, gaps) and checks the result.

## 1. Folders

```
<videos_root>/<name>/
  footage/                 your files, opened read-only (footage at the folder root is read in place)
  brief.md                 optional: profile, language, target length, glossary, must-keep, never_show
  jianji/                  everything jian writes; delete it to start over (videos made before the rename: vedit/)
    config.toml            per-video overrides
    cache/                 content-keyed: audio, transcripts, envelopes, vision notes, OCR
    catalog.md transcript.md broll.md stream.md     the editor's reading views
    edits/edit.vNNN.json   the edit, one file per version
    renders/               master, review, final, captions, timeline and QA per version
    resolve/               FCPXML, text box PNGs, manifest, IMPORT.md, from-resolve/
    review.md feedback.log metrics.json diffs/
    shorts/<n>/            a short cut out of this video (`--short <n>`): its own config.toml, edits/, renders/,
                           resolve/ and preview.mp4; cache/ and catalog.json are links to the video's

~/.config/jianji/          your layer (see section 8)
```

jian never writes outside `<name>/jianji/`. Every stage that writes media estimates its size first and refuses to
start below `disk.min_free_gb`.

## 2. Pipeline

| Step | Who | What |
|---|---|---|
| `jian prep` | CLI | probe (ffprobe → `catalog.md`), extract audio, transcribe, pack (`transcript.md`), contact sheets and vision notes for picture sources (`broll.md`) |
| `jian map` | CLI | streams only: a minute-by-minute map (`stream.md`, section 7) |
| plan + edit | Claude | section plan from the brief, the style guide and the views; `edit.vNNN.json` by word id |
| `jian compile` | CLI | word ids → frame-exact source ranges, gaps, readability holds; warnings the editor fixes |
| `jian cut` | CLI | compile, render, place texts, QA, Resolve export |
| review loop | Claude | at most two more cut versions: QA fails first, then visible problems |
| `/jian-revise` | Claude + CLI | natural-language feedback → the next version, changing only what was asked |
| `jian pull` / `jian diff` | CLI | a timeline edited in Resolve back as the next version, and what changed |

Everything is cached by content: footage is fingerprinted (size, mtime, three sampled 1 MB blocks), so unchanged
inputs are never re-transcribed and an interrupted `prep` resumes.

## 3. Transcription

Silero VAD splits speech into chunks of at most 12 s at pauses; each chunk gets its own language detection (among
the profile's `asr.languages`), then mlx-whisper large-v3-turbo with a verbatim prompt in that language, then wav2vec2
forced alignment for word timing. Whisper locks to one language per file and translates the rest, which is why
detection runs per chunk; a chunk with low detection confidence is transcribed in each candidate language and the
better result kept.

Guards against invented words: only VAD speech is transcribed; chunks with a high compression ratio, low log-prob or
high no-speech probability are dropped; a word aligned onto silence is dropped. Every dropped stretch stays visible
in `transcript.md` as `[? ...]`.

Spoken cues are found in the transcript when said on their own (`docs/SHOOTING.md`): "retake" drops the previous
attempt, "mark that" flags a highlight, "editor note: ..." becomes a marker. Hesitation fillers (um, uh, eh) are cut
automatically; discourse words are the editor's call.

Aligners: wav2vec2 base 960h for English; Apache-2.0 XLSR-53 models for Spanish, French, German and Italian (whispermlx's
defaults for those are non-commercial). Each transcript records its aligner, and QA re-aligns with the same one.
Every Hugging Face model is pinned to an exact commit and loaded as safetensors (`PINS` in `jianji/core.py`).

## 4. The edit file

```json
{
  "v": 3, "profile": "explainer",
  "sections": [{"id": "S1", "title": "Hook", "why": "strongest claim first"}],
  "clips": [
    {"id": "c7", "sec": "S1", "src": "A", "from": 1043, "to": 1077, "cut": [[1050, 1051]], "conf": 0.9,
     "why": "cleanest take"},
    {"id": "c8", "src": "B", "in": 3.0, "out": 7.5, "shot": "place", "why": "non-speech: seconds"}
  ],
  "overlays": [{"id": "b2", "src": "C", "in": 3.0, "out": 7.5, "over": "c7", "word": 1060, "gain_db": -18}],
  "texts": [{"id": "t1", "text": "Every service owns its data", "over": "c7", "at": 0.3}],
  "markers": [{"at": "c7", "kind": "uncertain", "note": "kept the aside, check it"}]
}
```

Speech is chosen by word id and never by timestamp. Clips play in list order; overlays (B-roll) and texts anchor to a
clip and a word or an offset. Optional per clip: `"abrupt": "<why>"` (a tight cut at a topic jump), `"keep"`,
`"fix"` (caption spelling), `"speed"`, `"crop"`, `"blur"`, `"layout"` (two regions of the source one over the
other, section 7). After a pull, items carry `"locked"` (your hand edit in
Resolve) or `"pinned"` (jian's own cut, frozen at the frames you saw).

## 5. Compile and render

**Cuts.** A word's audible end is where its sound decays to the noise floor plus `tail_db`, or where the level climbs
back by 8 dB (something new starts), at most 0.35 s past its aligned end. Every cut is classified by what it separates
(inside a sentence, between sentences, between sections) and keeps that class's gap of real silence from the source;
a speaker who runs straight on gets a little of the take's own room tone. Speech is cut only at a clause or sentence
end: a punctuated word followed by an audible pause. A clip that stops mid-sentence runs on to finish it (up to
`finish_max`); `"abrupt"` is the only exception and is listed in review.md.

**Readability.** Nothing is on screen for less than `[shots] min_visible` (1 s). Picture shots hold a minimum by type
(place, detail, action, reaction). Every burned text stays up for its reading time (`[texts]`). Texts are placed off
faces and the subject (Apple Vision, compiled once from `jianji/vision.swift`); placement may move a text, never
shorten it.

**Render.** Video: one encoded piece per clip (VideoToolbox), cached, joined without re-encoding; a revision re-encodes
only what changed. Audio: one continuous PCM mix with centered equal-power crossfades at every cut, two-pass loudnorm
to the profile's loudness. Outputs per version: `master` (source quality), `review` (burned timecode, section and clip
ids, captions), `captions.srt`, and `final` (the upload file: shorts burn captions and texts; horizontal videos burn
texts and ship captions as the SRT). A captions- or text-only revision reuses the master.

## 6. QA

Runs on every render. Three levels: `fail` means a confirmed problem, `warn` means worth a look, `pass`. review.md's
second line is kept current.

| Check | Fails when |
|---|---|
| `duration`, `size`, `av_offset`, `av_sync`, `loudness`, `picture` | the render doesn't match the plan; A/V drift; loudness off target; black or frozen frames |
| `clipped_words` | a word next to a cut scores clearly lower on the render than in the source (wav2vec2, aligned in context) and the cut-away side holds voice |
| `fragments` | voice-level sound past the kept words at a cut *and* Whisper hears a word there that the edit doesn't have |
| `speech_cuts` | a speech cut lands mid-sentence (not marked `abrupt`) or keeps less air than its class's minimum |
| `short_segments` | anything on screen under 1 s |
| `text_faces`, `safe_zone` | burned text over a face more than 10% of its time; outside the platform safe zone (vertical) |
| `text_readable`, `shot_readable` | a text up less than its reading time; a shot under its hold (warn) |
| `privacy` | streams: a secret, an e-mail, a notification or a `never_show` word on screen (OCR every 2 s) or said aloud |
| `edges` | a cut inside a word (a cut into a word's decay warns) |
| `captions` | overlapping or broken cues (fast cues warn) |
| `asr_diff`, `text_overlap` | warnings only |

`jian cuts` renders every join with 1.5 s each side; `jian texts` renders every text in context.

## 7. Streams

`jian map` reads a long stream at keyframes only: per minute, the words spoken, what's on screen (OCR every
`map_step` seconds, classified as editor, terminal, browser or diagram), file and window names, cues said aloud
(`docs/STREAMING.md`), the vision model's note at each change of screen, where the picture changes (for zooms) and
privacy hits. The editor turns it into spans and threads and tells 2-4 threads as mini-arcs, with chapter cards,
context texts, sped-up bridges over room tone and zooms on code. A file with an audio track named mic, voz or voice
uses that track for speech.

**Shorts out of a stream.** `--short <n>` on compile, cut, qa, cuts, texts, resolve, at and status works in
`jianji/shorts/<n>/`: a cut of its own (profile `short`, 1080x1920) that shares the video's catalog, transcripts and
render cache, and keeps its speech and privacy settings. A clip with `"layout": "<name>"` is rendered as two panels,
each a region of the source cover-fitted to its box:

```toml
# ~/.config/jianji/config.toml
[layouts.code]
top = [0.75, 0.72, 1.0, 1.0]       # the webcam in your scene, as [x0, y0, x1, y1] of the recording (0-1)
bottom = [0.14, 0.02, 0.62, 0.98]  # the editor; a clip's own "crop" replaces it
split = 0.36                       # the top panel's share of the height
note = "editor full screen"        # shown by `jian status --short`

[outro]
text = "Live Mon-Thu 22:30"        # over the last seconds of every cut of these profiles
seconds = 3                        # at least; never less than its reading time
style = "hi"                       # txt | hi | chat | chapter
profiles = ["short"]
```

Every `jian cut --short` also writes `preview.mp4` (540 px wide, H.264, under `[render] preview_mb`) to judge the
short on a phone. In the FCPXML a two-panel clip is the same frames twice: the bottom panel on V1, the top one on
V2, each with a trim (`adjust-crop`) and a transform. `jian pull` doesn't read two-panel cuts back yet.

## 8. Configuration layers

Lowest to highest:

1. **Built-in** (`jianji/profiles/*.toml`, `jianji/styles/*.md`), shipped with the code.
2. **Your layer**, `~/.config/jianji/` (or `$JIANJI_HOME`):
   - `config.toml`: `schema`, `videos_root`, `[defaults]` for every profile;
   - `config.toml` `[ingest]`: `watch` (where recordings land), `name` and `profile` for `jian ingest`;
   - `config.toml` `[hooks]`: your command for an event (below);
   - `config.toml` `[layouts.<name>]` and `[outro]`: two-panel layouts and a closing line for shorts (section 7);
   - `profiles/<name>.toml`: only the keys you change, or a new profile;
   - `styles/<name>.md`: your editorial rules (replace the built-in guide for that profile);
   - `brand.toml`: fonts and colours for every profile;
   - `glossary.txt`: names you say often, one per line;
   - `briefs/<profile>.md` or `briefs/default.md`: your brief template for `jian new`;
   - `extensions/<name>.toml`: settings for a series, pulled in by `extends: <name>` in a brief.
3. **Per video**: `jianji/config.toml`, then brief.md front matter (`section.key: value`).

**Hooks** connect jian to whatever you use (a notifier, a queue, a chat bot) without jian knowing about it:

```toml
[hooks]
on_ingest = "~/.config/jianji/hooks/ingest.sh"   # a command line (no shell) or a list
on_error = ["notify", "--title", "jian failed"]
timeout = 30                                     # seconds, for every hook
```

| Event | When | Facts (`JIAN_EVENT` is always set) |
|---|---|---|
| `on_ingest` | `jian ingest` made or found a folder | `JIAN_VIDEO`, `JIAN_DIR`, `JIAN_SOURCE` |
| `on_error` | any command failed | `JIAN_COMMAND`, `JIAN_VIDEO`, `JIAN_ERROR` |

The facts travel as environment variables and are never put on a command line, so a name or an error message that
came out of a video can't run as shell. Treat them the same way in your script: quote them, and don't forward text
you don't trust to anything that acts on it. A hook that fails, hangs past `timeout` or is missing prints a warning
and the run carries on. `jian hook <event> KEY=VALUE...` fires one by hand; `jian doctor` checks each command exists.

`schema` versions the layer's format. When a release changes it, `jian doctor` says what to edit; it also flags keys
that no built-in profile knows (typos, renamed options) and profiles without a style guide.

## 9. DaVinci Resolve

See `docs/RESOLVE.md` for the steps. Every `jian cut` writes an FCPXML 1.10 timeline on the original footage:

| Lane | Track | What |
|---|---|---|
| spine | V1 + A1 | the A-roll, picture and sound |
| 1 | V2 + A2 | B-roll |
| 2 | V3 | callout boxes (PNG) |
| 3 | V4 | texts, as editable Basic Title |
| 4 | A3 | room tone (an audio-only asset) |

- No negative lanes: Resolve turns one into a video track under the spine and shifts every track up by one.
- Captions go in as an SRT subtitle track.
- Markers carry sections, check-this notes, tight cuts and QA issues.

`jian pull` reads Resolve's FCPXML export (its dialect: timeline at 01:00:00:00, `<clip>` containers, gaps carrying
connected clips) and writes the next edit at exactly your frames. `jian diff` lists what changed. Split edits, level
keyframes, speed changes, transforms, transitions and compound clips are listed as unsupported: render those from
Resolve. The free edition is enough; it runs no scripts, so the round trip is file-based.

## 10. Model usage

The CLI never calls a language model. Claude reads about 1 KB of transcript per minute of speech, the catalog, the
vision notes, and images only at decision points (contact sheets, a frame at a cut). Media stays on your machine; only
text and those few frames go to Claude.
