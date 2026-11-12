# Streaming protocol

Small habits on stream that make the recap much better. jian hears them (`jian map` marks the cues).

## Say it aloud

- **At the start:** the session's goal, in one sentence. "Today I want the app to validate its config." It becomes the
  recap's first context line.
- **When you switch:** "new topic: the scheduler" ("nuevo tema" in Spanish). It marks where a thread starts.
- **When something works:** "mark that" ("marca eso"). It marks a result worth keeping.
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

## After the stream

`jian ingest` turns the recording into a video folder without touching it: `<videos_root>/<date>-<name>/` with your
stream brief (`briefs/stream.md` in your layer) and, in `footage/`, a link to the file. Give it the file, or tell it
where OBS records and it takes the newest one:

```toml
# ~/.config/jianji/config.toml
[ingest]
watch = "~/Movies"          # OBS: Settings > Output > Recording > Recording Path
name = "{date}-{slug}"      # the folder's name; {slug} is the rest of the file's name
profile = "stream"
```

Then `jian prep`, `jian map` and `/jian-edit` as usual. The recording stays where OBS wrote it, under its own name:
keep it there while you work on the folder. Running `jian ingest` again on the same file finds the same folder.
