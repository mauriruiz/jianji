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

## Shorts from the stream

`/jian-shorts <name>` (after `jian prep` and `jian map`) picks up to three moments that stand alone and cuts each as
a vertical short in `jianji/shorts/<n>/`, with `preview.mp4` to watch on a phone and its own Resolve timeline. Say
"mark that" on stream when something deserves one.

Each short opens on its strongest line, and QA's `hook` check holds it to that: no run-up word first, no halting
start. The words you tend to start with ("so", "okay", your own) go in your layer, and they replace the built-in list:

```toml
# ~/.config/jianji/profiles/short.toml
[hook]
preamble = ["so", "okay", "well", "you know"]
```

Say how long you want them, and the editor builds each idea to that length (the setup, the point, a closing line)
instead of stopping at the first sentence that works. Outside what you aim for is a warning (a short one can still
be the best of the night); past your limits QA fails it, and the night repairs it or cuts another:

```toml
# ~/.config/jianji/config.toml
[shorts]
length = [35, 60]    # seconds you aim for
min_length = 25      # never shorter
max_length = 60      # never longer
```

Tell jian once where the camera and the code are in your scene, as regions of the recording, and the shorts stack
them (camera over code); the editor zooms the bottom panel on the lines you're talking about:

```toml
# ~/.config/jianji/config.toml
[layouts.code]
top = [0.75, 0.72, 1.0, 1.0]       # the webcam: [x0, y0, x1, y1] of the frame, 0-1
bottom = [0.14, 0.02, 0.62, 0.98]  # the editor
split = 0.36

[outro]
text = "Live Mon-Thu 22:30"
```

`jian queue` lists the shorts waiting for you; `jian approve <video>:<n>` puts one in your upload folder
(`[queue] upload = "~/Movies/To upload"`) as a Resolve project on the original recording, ready to finish and
render by hand; `jian reject <video>:<n>` drops it; `jian requeue <video>:<n>` takes a yes or a no back (and the
upload folder with it, unless you put or changed something there). Keep the recording while a short of it waits there:
`jian queue --protected` lists the files still needed.

A webcam that is small in the recording is enlarged to fill the top panel and looks soft: under about 1080 px wide it
shows. Record it as a separate source (above) or switch to a full-screen camera scene for the moments you'd clip.

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

Then `jian prep`, `jian map` and `/jian-edit` as usual, or all of it overnight: `scripts/night.sh <folder>` runs
prep and map, has Claude Code cut the shorts with its permissions cut down to jian and the work folder (README,
Security), and ends with `jian queue --announce`, so your `on_queue_ready` hook can tell you in the morning. The log
is `jianji/night.log`. The recording stays where OBS wrote it, under its own name:
keep it there while you work on the folder. Running `jian ingest` again on the same file finds the same folder.

The night aims at a full queue (`night.sh <folder> 3`: three shorts, the default). A short that fails QA is repaired
or another moment is cut in its place, and if fewer than asked for got through, the editor gets one more pass for
just that. `jian status <folder>` shows where each short stands (`1 pending, 2 failed, 3 approved`). Run again on
the same folder, the night cuts only what is missing: `NIGHT_RECAP=1 scripts/night.sh <folder>` the day after
adds the recap and leaves the shorts alone.
