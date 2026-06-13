# Numpy Audio Synthesis — Floating-Point Rounding Pitfalls

**Context:** GRO-1270 — generating 10 cinematic SFX samples via numpy waveform synthesis + ffmpeg.
4 of 10 generators hit the same `ValueError: operands could not be broadcast together` bug
before the root cause was identified.

## Root Cause

`int(duration * SAMPLE_RATE)` is NOT associative across multiple float operations because
each `int()` call truncates independently. When computing segment boundaries for multi-note
audio:

```python
note_dur = total_dur / num_notes  # e.g., 0.6 / 4 = 0.15

for i in range(num_notes):
    start = int(i * note_dur * SAMPLE_RATE)       # truncates i*note_dur*44100
    end   = int((i + 1) * note_dur * SAMPLE_RATE)  # truncates (i+1)*note_dur*44100
    seg_len = end - start                           # may be ±1 from wave_len

    wave = triangle_wave(freq, note_dur)  # creates int(note_dur * SAMPLE_RATE) samples
    # wave_len ≠ seg_len → broadcasting fails
    samples[start:end] = wave[:seg_len] * envelope[:seg_len]
```

**Concrete example from GRO-1270 (SAMPLE_RATE=44100, note_dur=0.15):**

| Note i | start | end | seg_len | int(0.15*44100) |
|--------|-------|-----|---------|-----------------|
| 0 | 0 | 6615 | 6615 | 6615 ✓ |
| 1 | 6615 | 13230 | 6615 | 6615 ✓ |
| 2 | 13230 | 19844 | **6614** | 6615 ✗ |
| 3 | 19844 | 26460 | **6616** | 6615 ✗ |

Note 2 is 1 sample SHORT (seg_len=6614, wave=6615) — slicing `wave[:6614]` fits fine.
Note 3 is 1 sample LONG (seg_len=6616, wave=6615) — slicing `wave[:6616]` gives 6615,
which mismatches `envelope[:6616]` (6616 elements) → broadcasting failure.

## Fix Pattern

Use `min()` when slicing to gracefully handle the ±1 difference:

```python
# Pre-generate full waveforms for each note (include +1 margin)
wave = triangle_wave(freq, note_dur)[:int(note_dur * SAMPLE_RATE) + 1]

for i in range(num_notes):
    start = int(i * note_dur * SAMPLE_RATE)
    end   = int((i + 1) * note_dur * SAMPLE_RATE)
    seg_len = end - start
    seg_t = t[start:end] - t[start]

    n = min(seg_len, len(wave))  # <-- THE FIX

    env = envelope(seg_t[:n], note_dur, attack=..., decay=..., ...)
    samples[start:start+n] = wave[:n] * env[:n]
```

## Detection Rules

- **When one generator in a batch hits this:** ALL generators using segment-based
  waveform construction are susceptible. Fix them all in one pass.
- **Symptom:** `ValueError: operands could not be broadcast together with shapes (N,) (N±1,)`
  — and retrying the exact same code fails in the same spot every time (deterministic,
  not a runtime fluke).
- **The bug is silent in ~50% of segments** (when seg_len ≤ wave_len, slicing hides it).
  Only manifests when seg_len > wave_len.

## GRO-1270 Session Log

4 generators failed before root cause was identified:
1. `gen_powerup_pickup` — shapes (6615,) (6616,) — first failure
2. Applied `[:seg_len]` slicing fix — still failed on note 3 (6616 > 6615)
3. Applied `seg_t = t[start:end] - t[start]` — still failed (envelope creates its own array)
4. Applied `n = min(seg_len, len(wave))` + pre-generation — FIXED
5. `gen_ui_click` — shapes (5292,) (2646,) — same class, different generator
6. `gen_victory_jingle` — shapes (35281,) (35280,) — ditto

Pattern: each fix was applied to one generator at a time, discover-next-failure, fix-next.
If the root cause (floating-point truncation non-associativity) had been understood after
the first failure, all 3 subsequent generators would have been fixed in one pass.
