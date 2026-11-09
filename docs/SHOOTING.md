# Shooting protocol

Small habits on set that save editing time later. Everything is optional: the pipeline works without them, it just has to guess more.

## Spoken cues

Say a cue **on its own**: stop, pause about 1 s, say it in a normal voice, pause again, carry on. The pause is what separates a cue from the same words used in a sentence. English and Spanish cues work in either language.

| Cue | English | Español | What happens |
|---|---|---|---|
| Retake | "retake" | "repito" | The previous attempt is dropped. After the cue, restart the **whole sentence or thought** from its beginning, not mid-sentence. Several retakes in a row: only the last attempt survives |
| Highlight | "mark that" | "marca eso" | The phrase you just said is flagged as a highlight: kept unless it clearly breaks the story, and first in line for Shorts |
| Editor note | "editor note: ..." | "nota de edición: ..." | Everything after the cue, up to the next pause, becomes a marker in review.md (and later the Resolve timeline). Use it for "B-roll of the whiteboard here", "this joke might be too long", "put the diagram from the blog post here" |

Cue words never appear in the output. If a cue is misheard, the transcript shows it as normal speech and the edit pass treats it as a slip to cut. That's why the pauses matter.

Examples:

> "...and that's why the saga pattern... (pause) **retake** (pause) The saga pattern exists because distributed transactions don't scale."

> "Idempotency is not optional, it's the contract. (pause) **mark that** (pause)"

> (pause) "**nota de edición**: aquí va el diagrama del outbox" (pause)

## Start of every recording

1. Hit record, then **2 seconds of silence** before speaking. This gives a clean noise profile for café cleanup and a clean first cut point.
2. **Separate audio** (wireless mic or recorder): after the silence, **one clap** in front of the camera, hands visible. Sync is found from the waveform; the clap makes it certain. For takes over 20 minutes, clap again at the end so clock drift can be measured.
3. Don't talk over the clap.

## Camera

| Setting | Value | Why |
|---|---|---|
| Resolution / frame rate | 4K at 25 fps where mains power is 50 Hz (Europe), 30 fps where it's 60 Hz | Matches the lights; 4K leaves room for punch-ins and vertical crops |
| Anti-flicker | Your mains frequency (50 or 60 Hz) | Indoor lights flicker at twice that |
| Shutter | Twice the frame rate (1/50 at 25 fps) if you can lock it, otherwise auto | Natural motion blur |
| Color profile | Normal | No grading step; D-Log M would need a LUT |
| White balance | Fixed preset closest to the room, not auto | Auto WB shifts colour between takes, which makes jump cuts obvious |
| Exposure | Lock it after framing | Same reason |
| Framing | Eyes on the upper third, head roughly centred, some headroom | The centre 9:16 strip must contain your face for Shorts |
| Tracking | Face tracking on | Keeps framing stable when you lean |

## Audio

- Wireless mic: wear it over your clothes, about a hand's width under your chin. Rubbing fabric on the mic can't be fixed later.
- Turn **off** in-device noise cancellation if the mic records a backup; cleanup in post is gentler and adjustable. Keep the internal backup on (32-bit float if available).
- In cafés, sit with your back to the loudest source (the espresso machine), so the mic faces away from it.

## B-roll (phone)

- Same frame rate as the main camera, or double it for slow motion.
- Lock exposure and focus. Hold each shot **at least 5 seconds**, plus 1 s still before and after any camera move.
- Shoot horizontal unless the shot is only for Shorts.
- Don't delete anything. Bad takes cost nothing; the pipeline picks.

## Language

Switch between English and Spanish freely **between sentences**: each short chunk gets its own language detection. English technical terms inside a Spanish sentence ("el event sourcing") are fine. Whole-phrase switches mid-sentence work, but that's where transcription errors cluster.
