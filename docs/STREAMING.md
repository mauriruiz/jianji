# Streaming protocol

Small habits on stream that make the recap much better. jian hears them (`jian map` marks the cues).

## Say it aloud

- **At the start:** the session's goal, in one sentence. "Today I want the app to validate its config." It becomes the
  recap's first context line.
- **When you switch:** "new topic: the scheduler" ("nuevo tema" in Spanish). It marks where a thread starts.
- **When something works:** "mark that" ("marca eso"). It marks a result worth keeping.
  Or press a button: a text file next to the recording and named after it (`2026-03-01 22-30-05.marks` for
  `2026-03-01 22-30-05.mkv`), one line per mark with the time into the recording (`3761.5` or `1:02:41`, then an
  optional note), counts the same. OBS gives that time over obs-websocket (`GetRecordStatus`), so a hotkey script can
  append it.
- **Chat:** read the question out loud before answering ("X asks why I'm not using a library"). It becomes a card.
- **At the end:** a quick spoken recap: what got done, what's next. It's the recap's outro.

## OBS

- **Record the mic on its own audio track**, separate from desktop audio and music. In Settings > Output >
  Recording, enable a second audio track; in Advanced Audio Properties, put the mic on that track only. Name it
  "Mic" (Settings > Output > Audio > Track 2 name). jian uses the track named mic/voz/voice for transcription and
  dialogue. That keeps music out of the recap (no Content ID claims) and drops chat alert sounds.
- Optional: record the screen and the webcam as separate sources (Source Record plugin), for cleaner zooms on code.
- Keep secrets off screen: close `.env` files, use a separate browser profile, turn on Do Not Disturb (notifications).
  QA still checks, but it can only blur what was recorded.
