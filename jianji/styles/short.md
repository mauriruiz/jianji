# Short style guide

Vertical 9:16 clips for TikTok, Reels and YouTube Shorts, 30-60 s: a place, a product, a moment. Reusable rules only; feedback about one
video stays in that video's `jianji/feedback.log`. Rules change only after you approve a proposed update.

## Spine

- **Picture-led.** The spine is whatever carries the story: a voice-over if there is one; otherwise the creator's own
  on-camera lines where they say something useful (place, price, reaction, opinion), and on-screen texts plus the
  footage's natural sound everywhere else. Never invent narration.
- Every phrase gets the shot that illustrates it. Cover talking stretches with B-roll, chained back to back
  (`over` + `at`, frame-exact) so the talking head never flashes for a frame between them.
- The creator's face stays on screen for the reaction, the price and the verdict: that's the delivery.

## Pace

- Fast, but every shot must register: the viewer has to understand what they're looking at (a counter that
  flashes by is never seen). Least hold by shot type, from `[shots]`: **place** (establishing: facade, counter, sign,
  street) 2 s; **detail** (the product, a close-up) 1.2 s; **action** (mixing, pouring, a cheese pull) until the action
  completes; **reaction** the whole reaction. Label every picture clip and B-roll overlay with its type
  (`"shot": "place"`) from the vision notes and the sheet; compile holds each one at least that long (later into its
  source, then earlier; an action or reaction until its motion settles, up to 1.5 s more) and warns where it can't.
  Plan for it: pick stretches long enough, and fewer, longer shots over many flashes.
- A change on every phrase or half-phrase where the shots allow it. Nothing over 2.5 s except the key moment and its
  reaction.
- No dead air, no "hi guys, today we're at...". Jump cuts in a selfie take are fine.
- Fast, never airless: cuts landing right on the voice sound rushed. Every cut keeps the silence
  of what it separates: ~0.12 s inside a sentence, ~0.30 s between sentences, ~0.55 s between sections
  (`[cut] gap_*`, scaled by `cut.breathing = tight | natural | relaxed`). Compile places it from the audio; your job is
  to cut where it can: end a clip on a sentence's last word, not mid-phrase into the next one.
- Where the speaker runs straight on, compile adds a little room tone (with `[cut] max_room = 0` it adds nothing
  and the cut is straight: cut where the take pauses, or keep both sentences in one clip); a section change that needs more gets a
  B-roll beat from you (compile warns). After breathing room shifts timing, re-chain back-to-back B-roll (compile
  warns about a talking-head flash between shots).
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
- Audit every join in the cut reel (`jian cuts <name>`) before calling the pacing done.
- Hook (0-3 s): the most spectacular shot in the material, in motion from the first frame, no fade-in. A cut that
  opens on speech opens on its strongest line, said in one go: no run-up before it ("so", "well"), no pause
  inside it, the sentence over inside those seconds. Name it in the edit (`"hook": "<the line>"`); compile warns
  and QA (`hook`, tuned in `[hook]`) fails an opening that isn't one.

## Sound

- No music: the creator adds a trending sound in the app.
- Process shots (mixing, sizzling, the vendor singing) play their own sound at full level: they're the spectacle.
- B-roll under speech: -14 to -20 dB bed. Back-to-back sounding overlays crossfade in a couple of frames.

## Text on screen

- Captions for the creator's speech, burned in, bottom-centre inside the safe zone (clear of the description at
  the bottom and the buttons on the right). Fix misheard words with `fix`, never leave a wrong word on screen.
- **Every text must be read.** Time on screen ≥ max(1.5 s, 0.4 s + characters ÷ 13), at most 4.5 s (`[texts]`):
  "Café Central · Lavapiés" needs 2.2 s. Leave `dur` out and compile gives each text its reading time; a shorter `dur`
  is raised to it, the next text of the same style waits, and a text runs on across a shot change rather than being
  cut at it. To fit the time, shorten the words, not the duration.
- Story texts (`txt`, top): short, in the creator's voice. They carry the story where nobody speaks; don't repeat
  what the creator says in the same breath. They keep one position through the video; where a face forces a move,
  the whole run of story lines switches to one alternate slot together.
- Callouts (`hi`, yellow box): the place name and the price, at the moment they're mentioned.
- **Never over a face, never over the subject.** Plan texts for shots where there's room: a callout belongs on the sign
  or the counter shot, not on the close-up, and that shot (or what follows it) must have room for its whole reading
  time. jian checks every text against faces (and the subject, in shots without a face) for its whole time on screen
  and moves it to another slot or up to 1 s in time; it never shortens or shrinks a text to dodge a face. Where
  nothing is free the text stays readable over the face, review.md ("Text placement") says why and QA fails it:
  re-anchor it. Check every text in the text reel (`jian texts <name>`): time on screen and chars/s.
- No emoji in burned text (libass draws them as boxes); suggest them for the in-app text instead.

## Don't invent

- Prices, facts and opinions only if they're said or clearly visible. If the brief proposes a text the footage
  doesn't support (a detail you can't see), adjust it and say so in review.md; if something important is
  missing (the price), leave it out and flag it.

## Review notes

- Say which shot is the hook and why.
- "Next time, film this": the shots that were missing (a close-up of the key detail, the sign or menu, the
  creator's face at the key moment in a steady frame).
