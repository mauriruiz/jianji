# Finishing in DaVinci Resolve

Optional. jian's `final.vNNN.mp4` is ready to upload; use Resolve when you want to change something by hand. The free
edition is enough (tested on 21.2). Every `jian cut` writes `<video>/jianji/resolve/IMPORT.md` with the exact paths
for that version; these are the same steps.

## In

1. File > New Project, named after the video. Open it.
2. File > Import > Timeline… and pick `jianji/resolve/<name> vNNN.fcpxml`. In the dialog turn **on** "Automatically
   set project settings", "Automatically import source clips into media pool" and "Use sizing information" (text
   positions). Nothing else.
3. File > Import > Subtitle… and pick `jianji/renders/captions.vNNN.srt`; drag it from the Media Pool onto the
   timeline at its first frame. The captions land on an editable subtitle track.

What you get:
- Your original footage, linked by path: drag any edge to recover material.
- Tracks: V1 A-roll, V2 B-roll, V3 callout boxes, V4 texts (editable titles), A1 A-roll sound, A2 B-roll sound,
  A3 room tone.
- Markers: blue sections, yellow "check this", red tight cuts, purple QA issues.

A short with two panels (camera over screen) is the same file twice: V1 is the bottom panel and V2 the top one, each
trimmed (Inspector > Cropping) and placed (Transform). Compare it with `preview.mp4` before you render.

One-time, optional: Preferences > System > Media Storage, put a working folder of your choice first, so Resolve's
cache and gallery don't fill your home folder.

## Back (experimental)

Tested on jian's own FCPXML, not yet on a real export from Resolve: tell us how it goes.

1. File > Export > Timeline… > "FCPXML 1.10", save into `jianji/resolve/from-resolve/`.
2. If you edited captions: right-click the subtitle track > Export Subtitle… > SRT, same folder, same name.
3. `jian pull <video>` writes the next edit version at exactly your frames; `jian diff <video>` lists what you changed.
   Or tell Claude in `/jian-revise`.

Your changes come back `"locked"`: later revisions keep them unless your feedback names them, and QA lists them
instead of failing them.

## What doesn't come back

Two-panel shorts don't come back yet. jian renders what it can represent. The pull lists the rest as "unsupported": split edits (J/L cuts), level
keyframes, speed changes, transforms on footage, transitions, compound and multicam clips, footage from outside the
video folder, grading and effects. If you use them, render the final from Resolve.
