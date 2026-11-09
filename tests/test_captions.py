"""Caption text: glossary spelling, words that must stay together, balanced line breaks. Pure, no media."""
from jianji.render import NBSP, caption_cues, glossary_fix, keep_together, protect, wrap

W = lambda *ts: [(i * 0.3, i * 0.3 + 0.25, t) for i, t in enumerate(ts)]
texts = lambda ws: [w[2] for w in ws]


def test_glossary_fixes_spelling_and_keeps_terms_whole():
    out = glossary_fix(W("están", "en", "plaza", "mayor,", "en", "kubernetis."), ["Plaza Mayor", "Kubernetes"])
    assert texts(out) == ["están", "en", f"Plaza{NBSP}Mayor,", "en", "Kubernetes."]
    assert out[2][:2] == (0.6, 1.15), "the joined term spans both words' time"


def test_glossary_leaves_ordinary_words_alone():
    ws = W("el", "camino", "de", "la", "tarta")
    assert glossary_fix(ws, ["Plaza", "Kubernetes", "Grafana"]) == ws
    ws = W("pasta", "para", "llevar", "en", "Pasta&Co.")  # a word that is a prefix of the term
    assert texts(glossary_fix(ws, ["Pasta&Co"])) == ["pasta", "para", "llevar", "en", "Pasta&Co."]


def test_glossary_keeps_a_sentence_capital():
    assert texts(glossary_fix(W("Carbonara.", "una", "Carbonara"), ["carbonara"])) == ["Carbonara.", "una", "Carbonara"]
    assert protect("Carbonara 8,50 €, una carbonara", ["carbonara"]) == "Carbonara 8,50 €, una carbonara"


def test_names_and_numbers_with_units_stay_together():
    out = keep_together(W("Café", "Central", "cuesta", "13", "euros.", "Madrid.", "Están"), 17)
    assert texts(out) == [f"Café{NBSP}Central", "cuesta", f"13{NBSP}euros.", "Madrid.", "Están"]


def test_wrap_balances_two_lines_without_splitting_groups():
    text = f"Mayor y se llama Café{NBSP}Central."
    top, bottom = wrap(text, 17).split("\n")
    assert f"Café{NBSP}Central." in bottom and abs(len(top) - len(bottom)) <= 4, (top, bottom)
    assert wrap("Corto.", 17) == "Corto."


def test_cue_breaks_never_split_a_glossary_term():
    words = ["Están", "en", "plaza", "mayor", "y", "se", "llama", "Café", "Central."]
    tl = {"fps": "25/1", "segments": [{"src": "A", "rec": 0, "in": 0.0, "n": 100, "kept": list(range(len(words)))}]}
    src = {"A": {"words": [{"i": i, "t": t, "s": i * 0.3, "e": i * 0.3 + 0.25} for i, t in enumerate(words)]}}
    cfg = {"max_chars": 14, "line_chars": 14, "min_dur": 0.5, "max_dur": 3.0, "break_gap": 0.6}
    cues = [c[2] for c in caption_cues(tl, src, cfg, ["Plaza Mayor"])]
    assert any(f"Plaza{NBSP}Mayor" in c for c in cues), cues
    assert not any(c.rstrip().endswith("Plaza") for c in cues), cues


def test_on_screen_text_gets_glossary_spelling():
    assert protect("Café Central · plaza  mayor", ["Plaza Mayor"]) == f"Café Central · Plaza{NBSP}Mayor"
