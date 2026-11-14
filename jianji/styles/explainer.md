# Explainer style guide

Talking-head videos that explain one idea: a tutorial, an opinion, a story told to camera. Reusable rules only; feedback about one
video stays in that video's `jianji/feedback.log`. Rules change only after you approve a proposed update.

## Story

- Build a section plan before choosing a single range. Typical shape: **hook** (the problem or the claim, in the first 20-30 s), **context** (why it matters, who hits it), **the explanation** in 2-5 sections, **a concrete example**, **takeaway**, short **outro**. Use the material's own shape when it has a better one.
- The hook is the strongest framing of the problem that exists in the footage, even if it was said late. Moving it to the front is fine; changing what it means is not.
- No "hi, welcome back" opening unless the brief asks for it. If the recording has one, it can follow the hook.
- Every section must earn its place: if removing it doesn't hurt the takeaway, flag it as a candidate cut instead of keeping it silently.
- Keep personality: an aside, a joke or an opinion that lands stays even if it's not strictly needed. Rambling that repeats a point does not.

## Selection

- Shooting cues are instructions: honor `<<RETAKE>>` (use the attempt after it), keep `<<MARK>>` phrases unless they break the story, turn `<<NOTE>>` into markers and act on them when possible.
- Several takes of the same idea: prefer the last complete one; prefer the clearer one when they differ. A take that fades out before its point is never the one used.
- False starts and abandoned sentences go. Self-corrections ("the leader, sorry, the follower") keep only the corrected form, unless the correction itself is the point.
- Never cut mid-thought, never cut the setup of a payoff, never stitch words into a sentence that wasn't said.
- Language: the video's main language is the one most of the footage is in (or the brief's). Passages in the other language stay only if they're clearly intended; otherwise flag them.

## Fillers and pacing

- Hesitation sounds (um, uh, eh, mmm) are removed automatically. Discourse words (so, like, you know, well; o sea, pues, bueno in Spanish) are your call: cut them where they stall a sentence, keep them where they carry rhythm.
- Natural, not breathless. Every cut keeps the silence of what it separates: ~0.15 s inside a sentence (a removed
  filler or false start), ~0.38 s between sentences, ~0.75 s between sections; natural pauses up to 0.8 s stay as
  they are, longer ones shorten to their class. `cut.breathing = tight | natural | relaxed` scales it all. Compile
  takes the silence from the audio (last word decays, next one's onset and inhale kept); plan cuts at sentence ends
  so it can. Don't add cuts just to remove breaths.
- A speaker who runs straight on gets a little room tone at sentence breaks (none with `[cut] max_room = 0`: the
  cut is straight, so cut where the take pauses); a section change that needs more gets
  a B-roll beat (compile warns). `jian cuts <name>` plays every join with 1.5 s each side: audit it.
- Jump cuts inside one take are acceptable in explainers.
- **Speech cuts land on a clause or sentence end, never mid-sentence.** A pause inside a clause is never cut; the end of a clip counts only where the word is
  punctuated and an audible pause follows (`boundary_gap`), since Whisper's period isn't the speaker's. A clip that
  stops mid-sentence runs on to its end, up to `finish_max` (1.5 s): half a second more of natural speech, a trailing
  "uh", "you know?" or "right" included, beats a chopped sentence. Every cut keeps ~0.2-0.3 s of air around it
  (`gap_sentence`). Only exception: the speaker jumps to another topic with no pause; give the clip
  `"abrupt": "<why>"` and the cut still falls between words; review.md lists it.
- **Naturalness wins over length.** Never break a phrase to hit the target: run over `target_length` (up to ~15% on
  shorts) rather than cut a sentence halfway. `abrupt` stays a rare exception, never a way to save time.
- **Nothing on screen under 1 s** (`[shots] min_visible`), the talking head included. Compile stretches B-roll over a
  short talking-head stretch next to it; a short piece between jump cuts is yours to fix: merge it into a neighbour
  (keep the pause, `"keep": ["pauses"]`), extend or drop it.
- Default target: about 60-70% of the raw speaking time, or the brief's length. Report the actual length and why.

## B-roll

- B-roll earns its place where the picture of a talking head adds nothing: abstract explanations, lists, the setting (the café, the street), or to hide a jump cut. Not as wallpaper.
- Match what's being said when possible ("every service owns its data" over a shot of the laptop with the diagram); otherwise use it as breathing room between sections.
- Keep the face on screen for the hook's first line, for MARK highlights, for punchlines and for strong opinions: the delivery is the point there.
- 2-6 s per shot, cut from steady, well-framed stretches. Start it on the word it illustrates. Let it cover a cut rather than end right on one.
- Every shot must register. Label each overlay with its type (`"shot": "place" | "detail" | "action" | "reaction"`);
  `[shots]` holds a place shot (the café, the street) at least 2 s, a detail 1.2 s, an action or reaction until its
  motion settles. Compile extends a short one and warns where it can't; QA `shot_readable` counts the rest.
- Prefer horizontal footage. A vertical phone clip is centre-cropped and heavily zoomed: use it only if the sheet shows the subject in the middle, and flag it.
- B-roll's own sound: a light bed (about -20 dB) when it adds place (café, street); silent when it's just noise.

## Text on screen

- Burned text (titles, lower thirds, the review render's captions) never covers a face or the thing being shown.
  jian measures it against faces over the text's whole time on screen and moves it (another slot,
  then up to 1 s in time; never shorter or smaller); QA fails text over a face more than 10% of its time. Captions
  keep their lane and switch to the alternate lane only where a face enters it.
- Every title and lower third stays up at least max(1.5 s, 0.4 s + characters ÷ 13), at most 4.5 s (`[texts]`);
  shorten the words to fit, never the time. `jian texts <name>` plays each one in context.

## Uncertainty

- Unsure whether something should stay? Keep it and flag it (`conf` < 0.7 plus a marker). A wrong cut is invisible to you; a flagged keep costs you five seconds.
- Things Whisper dropped (`[? ...]` lines) near kept material deserve a look: say so in review.md.

## Review notes

- "Next time, film this": shots or lines that would have made the video better (a missing example, a clearer one-sentence definition, B-roll for an abstract section, a stronger closing line).
