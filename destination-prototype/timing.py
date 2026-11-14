#!/usr/bin/env python3
"""Narration timing: per-word times from Kokoro chunk boundaries, and scene cuts that follow the script.

Kokoro yields one audio chunk per sentence-ish unit together with its text, so chunk boundaries are
real timestamps. Words inside a chunk are spread by character length. Scenes may declare
`starts_at: "<phrase from the narration>"`; the cut then lands exactly where that phrase is spoken.
"""
from __future__ import annotations
import re
import numpy as np

_WORD = re.compile(r"[\w]+(?:[’'\-][\w]+)*", re.UNICODE)


def norm_words(text):
    """Lower-cased word list with apostrophe variants unified."""
    return [w.replace("’", "'").lower() for w in _WORD.findall(str(text))]


def word_times(script_words, chunks, sample_rate, total_seconds=None):
    """script_words: display words (script.split()). chunks: [(text, n_samples)] in speaking order.
    Returns [(start, end)] per script word. If chunk text and script disagree in word count, falls back
    to interpolating over chunk boundaries by character offset."""
    chunk_secs = [n / sample_rate for _, n in chunks]
    starts = np.concatenate([[0.0], np.cumsum(chunk_secs)])
    total = float(starts[-1]) if total_seconds is None else float(total_seconds)
    counts = [len(str(t).split()) for t, _ in chunks]
    weights = np.array([max(1, len(re.sub(r"\W", "", w))) for w in script_words], dtype=float)
    if sum(counts) == len(script_words) and all(counts):
        out, i = [], 0
        for (t, _), n, s0, s1 in zip(chunks, counts, starts[:-1], starts[1:]):
            w = weights[i:i + n]
            edges = s0 + (s1 - s0) * np.concatenate([[0.0], np.cumsum(w) / w.sum()])
            out.extend((float(edges[j]), float(edges[j + 1])) for j in range(n))
            i += n
        return out
    # fallback: character-offset interpolation across chunk boundaries
    char_edges = np.concatenate([[0.0], np.cumsum([max(1, len(str(t))) for t, _ in chunks])])
    char_edges = char_edges / char_edges[-1]
    cum = np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum()
    times = np.interp(cum, char_edges, starts / (starts[-1] or 1.0)) * total
    return [(float(times[i]), float(times[i + 1])) for i in range(len(script_words))]


def find_phrase(script_words, phrase):
    """Index of the first word of `phrase` in the script (normalised), or -1."""
    s, p = norm_words(" ".join(script_words)), norm_words(phrase)
    if not p:
        return -1
    for i in range(len(s) - len(p) + 1):
        if s[i:i + len(p)] == p:
            return i
    return -1


def scene_ends(scenes, script_words, times, duration, fallback_weights=None):
    """Return the end time of every scene (last == duration).

    If any scene has `starts_at`, cuts follow the narration: the first scene starts at 0, scenes without
    `starts_at` are spaced evenly between their neighbours. A phrase that is missing or out of order raises
    ValueError (a config error should fail loudly, not silently shift the visuals)."""
    n = len(scenes)
    if not any(s.get("starts_at") for s in scenes):
        w = np.array(fallback_weights if fallback_weights is not None else [1] * n, dtype=float)
        return list(np.cumsum(duration * w / w.sum()))
    idx = [None] * n
    for i, s in enumerate(scenes):
        if i == 0:
            idx[0] = 0
        elif s.get("starts_at"):
            j = find_phrase(script_words, s["starts_at"])
            if j < 0:
                raise ValueError(f"Scene '{s.get('title')}': starts_at phrase not found in narration: {s['starts_at']!r}")
            idx[i] = j
    known = [(i, v) for i, v in enumerate(idx) if v is not None]
    if any(b[1] <= a[1] for a, b in zip(known, known[1:])):
        raise ValueError("Scene starts_at phrases must appear in narration order (and not repeat a position)")
    for (a, va), (b, vb) in zip(known, known[1:] + [(n, len(script_words))]):
        gap = b - a
        for k in range(1, gap):
            idx[a + k] = round(va + (vb - va) * k / gap)
    starts = [0.0 if i == 0 else times[idx[i]][0] for i in range(n)]
    return starts[1:] + [float(duration)]
