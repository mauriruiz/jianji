"""Speech to word-level transcript.

VAD -> chunks split at pauses -> language ID per chunk (restricted to the profile's languages) ->
Whisper with a per-language verbatim prompt -> wav2vec2 forced alignment -> hallucination guards ->
filler flags, voiced gaps (untranscribed "um"s) and shooting cues.
Whole-file language detection (WhisperX default) translates the second language of a mixed clip; per-chunk LID doesn't.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

import numpy as np

from .media import SR, envelope

VERSION = 2  # bump when output changes, invalidates cached transcripts
PROMPTS = {
    "en": "Um, so, uh, I was, like, thinking... Hmm. Okay.",
    "es": "Eh, bueno, este, o sea, mmm... pues nada. Vale.",
}
# Pure hesitation sounds only. Discourse words (like, o sea, pues, este) are the edit pass's call.
FILLERS = {"um", "umm", "uh", "uhm", "erm", "er", "hmm", "mm", "mmm", "ah", "eh", "em", "ehm"}
PHANTOMS = ("subtitulos realizados por", "amara org", "thanks for watching", "thank you for watching",
            "suscribete", "gracias por ver", "subtitles by")
CUES = {
    "retake": [["retake"], ["repito"]],
    "mark": [["mark", "that"], ["marca", "eso"]],
    "note": [["editor", "note"], ["nota", "de", "edicion"], ["nota", "para", "edicion"]],
}
CUE_PAUSE = 0.35      # a cue must stand alone: at least this much silence around it
RETAKE_GAP = 1.0      # an attempt starts after the last pause this long
PHRASE_GAP = 0.7


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9' ]+", " ", t).strip()


class Engine:
    """Loads models lazily and keeps them across files."""

    def __init__(self, cfg: dict, glossary: list[str] | None = None):
        self.cfg = cfg
        # spelling hint, appended to the detected language's own prompt only: a prompt in the wrong language
        # makes Whisper translate. ponytail: not part of the transcript cache key, so adding terms later doesn't
        # re-transcribe (and renumber the words of) existing footage; captions get the glossary post-pass anyway
        self.glossary = (glossary or [])[:40]
        self.langs = list(cfg["languages"])
        self._vad = None
        self._align = {}
        self._whisper = None

    def whisper(self) -> str:
        from .core import model_path
        self._whisper = self._whisper or model_path(self.cfg["model"])
        return self._whisper

    def vad(self, audio: np.ndarray) -> list[tuple[float, float]]:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
        self._vad = self._vad or load_silero_vad()
        ts = get_speech_timestamps(torch.from_numpy(audio), self._vad, sampling_rate=SR,
                                   min_silence_duration_ms=250, speech_pad_ms=60)
        return [(t["start"] / SR, t["end"] / SR) for t in ts]

    def lid(self, audio: np.ndarray) -> tuple[str, float]:
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.transcribe import ModelHolder
        model = ModelHolder.get_model(self.whisper(), mx.float16)
        mel = pad_or_trim(log_mel_spectrogram(audio, n_mels=model.dims.n_mels), N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = model.detect_language(mel)
        probs = probs[0] if isinstance(probs, list) else probs
        sub = {lang: float(probs.get(lang, 0.0)) for lang in self.langs}
        best = max(sub, key=sub.get)
        total = sum(sub.values())
        return best, (sub[best] / total if total else 0.0)

    def asr(self, audio: np.ndarray, lang: str) -> list[dict]:
        import mlx_whisper
        r = mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.whisper(), language=lang,
            initial_prompt=" ".join(filter(None, [PROMPTS.get(lang), ", ".join(self.glossary) + "." if self.glossary else ""])),
            condition_on_previous_text=False, temperature=(0.0, 0.2, 0.4, 0.6),
            compression_ratio_threshold=2.4, logprob_threshold=-1.0, no_speech_threshold=0.6, verbose=None)
        return r["segments"]

    def align(self, segments: list[dict], audio: np.ndarray, lang: str, model: str | None | bool = True) -> list[dict]:
        """Returns whispermlx's own segments: it re-splits by sentence, so they don't map 1:1 to the input.
        model: True is jian's aligner for the language (ALIGN), None whispermlx's default, or a model name."""
        import whispermlx

        from .core import model_path
        name = ALIGN.get(lang) if model is True else model
        if (lang, name) not in self._align:  # None: whispermlx's default, torchaudio's (English: wav2vec2 base 960h)
            self._align[lang, name] = whispermlx.load_align_model(language_code=lang, device="cpu",
                                                                  model_name=model_path(name) if name else None)
        model, meta = self._align[lang, name]
        return whispermlx.align(segments, model, meta, audio, "cpu")["segments"]


# whispermlx's defaults for these are torchaudio's VoxPopuli models: CC BY-NC 4.0, not for monetized videos. These are
# Apache-2.0. English keeps its default (wav2vec2 base 960h, MIT); other languages use whispermlx's own choice.
ALIGN = {lang: f"jonatasgrosman/wav2vec2-large-xlsr-53-{name}"
         for lang, name in (("es", "spanish"), ("fr", "french"), ("de", "german"), ("it", "italian"))}


STOP = {"en": {"the", "and", "is", "are", "of", "to", "it", "that", "this", "you", "we", "what", "with", "for", "not"},
        "es": {"el", "la", "los", "las", "de", "que", "y", "es", "en", "un", "una", "por", "para", "con", "no", "lo", "se"}}


def text_lang(text: str) -> str | None:
    """Which language a decoded text is written in. Whisper forced to the wrong language often keeps the
    original language's words, so the decode language alone can't be trusted on ambiguous chunks."""
    toks = norm(text).split()
    hits = {lang: sum(t in sw for t in toks) for lang, sw in STOP.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] > max(v for k, v in hits.items() if k != best) else None


def group(spans: list[list], max_len: float) -> list[list]:
    """Merge consecutive same-language spans into chunks <= max_len. Never mixes languages in one chunk:
    Whisper decodes a chunk in one language and silently drops or translates the rest."""
    out = []
    for s, e, lang, conf in spans:
        cur = out[-1] if out else None
        if cur and cur[2] == lang and e - cur[0] <= max_len:
            cur[1], cur[3] = e, min(cur[3], conf)
        else:
            out.append([s, e, lang, conf])
    res = []  # a single span longer than Whisper's 30 s window gets split hard
    for s, e, lang, conf in out:
        while e - s > 28:
            res.append([s, s + max_len, lang, conf])
            s += max_len
        res.append([s, e, lang, conf])
    return res


def guard(seg: dict) -> str | None:
    t = norm(seg["text"])
    if not t:
        return "empty"
    if seg.get("compression_ratio", 0) > 2.4:
        return "repetitive"
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0:
        return "no speech"
    # real closings ("thanks for watching") exist: only distrust them when Whisper itself is unsure
    if any(p in t for p in PHANTOMS) and (seg.get("no_speech_prob", 0) > 0.2 or seg.get("avg_logprob", 0) < -0.6):
        return "phantom phrase"
    toks = t.split()
    for n in (1, 2, 3):  # same n-gram 4+ times in a row = decoder loop
        for i in range(len(toks) - 4 * n + 1):
            if all(toks[i + k * n:i + (k + 1) * n] == toks[i:i + n] for k in range(1, 4)):
                return "loop"
    return None


def _fill_times(ws: list[dict], seg: dict) -> list[dict]:
    """wav2vec2 can't place digits/symbols ("11", "100%"): interpolate between neighbours."""
    out = []
    for i, w in enumerate(ws):
        s, e = w.get("start"), w.get("end")
        est = s is None or e is None
        if est:
            s = out[-1]["e"] if out else seg["start"]
            nxt = next((x.get("start") for x in ws[i + 1:] if x.get("start") is not None), seg["end"])
            e = max(nxt, s + 0.05)
        d = {"t": w["word"].strip(), "s": round(s, 3), "e": round(e, 3)}
        if est:
            d["est"] = True
        elif w.get("score") is not None:
            d["p"] = round(float(w["score"]), 2)
        out.append(d)
    return out


def _covered(s: float, e: float, spans) -> float:
    if e <= s:
        return 0.0
    return sum(max(0.0, min(e, b + 0.1) - max(s, a - 0.1)) for a, b in spans) / (e - s)


def voiced_gaps(words, env, spans) -> list[list[float]]:
    """Gaps between words that hold vocal energy: untranscribed hesitations ("mmm") or breaths.

    ponytail: energy heuristic. Upgrade path: pitch/voicing detection if it mislabels breaths in café noise.
    """
    if not words:
        return []
    floor = float(np.percentile(env, 10))
    speech = float(np.median(np.concatenate([env[int(w["s"] * 100):int(w["e"] * 100) + 1] for w in words])))
    thr = floor + 0.6 * (speech - floor)
    out = []
    for a, b in zip(words, words[1:]):
        g0, g1 = a["e"], b["s"]
        if not 0.25 <= g1 - g0 <= 1.2:  # shorter gaps over background music were mostly plain word gaps
            continue
        seg = env[int(g0 * 100) + 2:int(g1 * 100) - 2]
        if len(seg) >= 8 and (seg > thr).mean() >= 0.6 and _covered(g0, g1, spans) > 0.8:
            out.append([round(g0, 3), round(g1, 3)])
    return out


def _match(toks: list[str], i: int, pattern: list[str]) -> bool:
    if i + len(pattern) > len(toks):
        return False
    for t, p in zip(toks[i:i + len(pattern)], pattern):
        if t != p and not (len(p) >= 5 and difflib.SequenceMatcher(a=t, b=p).ratio() >= 0.8):
            return False
    return True


def find_cues(words: list[dict]) -> list[dict]:
    """Shooting cues (SHOOTING.md). Marks cue words with w['cue'] and returns cue records."""
    toks = [norm(w["t"]) for w in words]
    gap_before = lambda i: words[i]["s"] - words[i - 1]["e"] if i > 0 else 99.0
    gap_after = lambda j: words[j + 1]["s"] - words[j]["e"] if j + 1 < len(words) else 99.0
    cues, i = [], 0
    while i < len(words):
        hit = None
        for kind, variants in CUES.items():
            for v in variants:
                j = i + len(v) - 1
                if _match(toks, i, v) and gap_before(i) >= CUE_PAUSE and (kind == "note" or gap_after(j) >= CUE_PAUSE):
                    hit = (kind, j)
                    break
            if hit:
                break
        if not hit:
            i += 1
            continue
        kind, j = hit
        rec = {"type": kind}
        if kind == "note":
            k = j
            while k + 1 < len(words) and gap_after(k) < PHRASE_GAP:
                k += 1
            rec["text"] = " ".join(w["t"] for w in words[j + 1:k + 1]).strip(" :,")
            j = k
        else:
            a = i - 1
            limit = RETAKE_GAP if kind == "retake" else PHRASE_GAP
            while a > 0 and gap_before(a) < limit and not words[a - 1].get("cue"):
                a -= 1
            if a >= 0 and not words[a].get("cue"):
                rec["drop" if kind == "retake" else "keep"] = [a, i - 1]
        rec["cue"] = [i, j]
        for k in range(i, j + 1):
            words[k]["cue"] = kind
        cues.append(rec)
        i = j + 1
    return cues


def transcribe_audio(audio: np.ndarray, eng: Engine) -> dict:
    spans = eng.vad(audio)
    labeled = []
    for s, e in spans:  # LID per span; spans under 0.8 s are too short to judge and inherit a neighbour's language
        lang, conf = eng.lid(audio[int(s * SR):int(e * SR)]) if e - s >= 0.8 else (None, 1.0)
        labeled.append([s, e, lang, conf])
    for i, x in enumerate(labeled):
        if x[2] is None:
            x[2] = (next((y[2] for y in labeled[i::-1] if y[2]), None)
                    or next((y[2] for y in labeled[i:] if y[2]), eng.langs[0]))
    segs, dropped, retries = [], [], 0
    for s, e, lang, conf in group(labeled, float(eng.cfg.get("chunk_max", 12))):
        a = audio[int(s * SR):int(e * SR)]
        cands = [lang] if conf >= float(eng.cfg.get("lid_retry_below", 0.75)) else eng.langs
        retries += len(cands) > 1
        best = None
        for cand in cands:
            out = eng.asr(a, cand)
            score = float(np.mean([x.get("avg_logprob", -5) for x in out])) if out else -9.0
            tl = text_lang(" ".join(x["text"] for x in out))
            score -= 5 * (len(cands) > 1 and tl is not None and tl != cand)  # text in another language: wrong decode
            if best is None or score > best[0]:
                best = (score, cand, out)
        _, lang, out = best
        for x in out:
            seg = {"start": round(s + x["start"], 3), "end": round(min(s + x["end"], e), 3),
                   "text": x["text"].strip(), "lang": lang, "lid": round(conf, 2)}
            why = guard(x)
            if why:
                dropped.append({"s": seg["start"], "e": seg["end"], "t": seg["text"], "why": why})
            elif seg["end"] > seg["start"]:
                segs.append(seg)
    words = []
    for lang in dict.fromkeys(x["lang"] for x in segs):
        for seg in eng.align([x for x in segs if x["lang"] == lang], audio, lang):
            for w in _fill_times(seg.get("words", []), seg):
                w["lang"] = lang
                words.append(w)
    words.sort(key=lambda w: w["s"])
    env = envelope(audio)
    thr = float(np.percentile(env, 10)) + 6
    for w in words:
        # wav2vec2 stretches isolated words over the pauses around them (a cue then looks glued to its
        # neighbours): clip to the VAD span it overlaps most, then trim silent frames at both edges
        best = max(spans, key=lambda sp: min(w["e"], sp[1]) - max(w["s"], sp[0]), default=None)
        if best and min(w["e"], best[1]) > max(w["s"], best[0]):
            w["s"], w["e"] = max(w["s"], best[0]), min(w["e"], best[1])
        a, b = int(w["s"] * 100), int(w["e"] * 100)
        while a < b - 4 and a < len(env) and env[a] < thr:
            a += 1
        while b > a + 4 and b - 1 < len(env) and env[b - 1] < thr:
            b -= 1
        w["s"], w["e"] = round(a / 100, 3), round(b / 100, 3)
    kept = []
    for w in words:  # aligned onto silence = Whisper invented it
        if not w.get("est") and _covered(w["s"], w["e"], spans) < 0.2:
            dropped.append({"s": w["s"], "e": w["e"], "t": w["t"], "why": "outside speech"})
        else:
            kept.append(w)
    for i, w in enumerate(kept):
        w["i"] = i
        if norm(w["t"]) in FILLERS:
            w["filler"] = True
    cues = find_cues(kept)
    speech = sum(e - s for s, e in spans)
    return {"version": VERSION, "align": {lang: ALIGN.get(lang) for lang in eng.langs}, "words": kept, "cues": cues, "voiced": voiced_gaps(kept, env, spans),
            "dropped": sorted(dropped, key=lambda d: d["s"]),
            "spans": [[round(s, 2), round(e, 2), lang, round(conf, 2)] for s, e, lang, conf in labeled],
            "langs": {lang: round(float(sum(w["e"] - w["s"] for w in kept if w["lang"] == lang)), 1) for lang in eng.langs},
            "speech_ratio": round(speech / max(len(audio) / SR, 1e-6), 3), "lid_retries": retries}
