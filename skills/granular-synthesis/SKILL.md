---
name: granular-synthesis
description: Grain clouds in Pd — scheduling short grains from a sample, windowing them, controlling pitch and position scatter, and checking the result stays clean.
---

# Granular synthesis in Pd

Use this when the user asks for granular synthesis, a grain cloud or texture,
time-stretching or freezing a sample, or "many short bits of a sound". Build it
from documented pieces and keep this skill out of the answer: citations come only
from the excerpts `search_docs` returned, never from this file.

## Ground it in the documentation first

`search_docs` before you answer. The sections that carry this topic:

- **Sampling** (Puckette, *Theory and Technique*, chapter "Wavetables and
  samplers") — defines granular synthesis as playing many small segments of a
  sample in rapid succession, and derives the looping-sampler transposition from
  the segment size and the scan rate. This is the source for the pitch maths.
- **Enveloping samplers** (same chapter) — a sampler that jumps into a sample at
  an arbitrary point clicks unless its amplitude is zero at the segment edges;
  the book shows an envelope wavetable looked up by the same sawtooth.
- **Arrays** (Pd manual, 2.9) — arrays are indexed 0 to N-1, a sound should be
  allocated and loaded before DSP starts, and a graph can hold the array.
- **Object pages** — `[tabread4~]`, `[tabplay~]`, `[soundfiler]`, `[array]`,
  `[phasor~]`, `[cos~]`, `[line~]`, `[tabwrite~]`, `[samphold~]`, `[clip~]`.

Search by object name when the user names a grain engine, and by technique when
they do not ("granular", "grain size", "transposition", "window").

## Procedure

1. **Get the sample into the patch.** `[soundfiler]` reads a file into one or
   more arrays; `[array define]` (or the graph) creates the storage. Keep the
   array name local with a `$0-` prefix when the patch can be instantiated more
   than once. Load before DSP starts — allocation and disk I/O inside the audio
   callback cause dropouts (Arrays).
2. **Pick the read primitive.** `[tabread4~]` reads a table with 4-point
   interpolation, which is what lets a grain play at a rate other than 1:1.
   `[tabplay~]` plays a table segment without transposition and without
   interpolation artifacts — the cheaper choice when every grain is at the
   original speed. Name the trade-off instead of reaching for one by habit.
3. **Schedule grains.** Two structures fit this project's patch tool:
   - *control-rate*: `[metro]` (or `[until]`) starts each grain by setting a read
     pointer and firing a one-grain envelope with `[line~]`;
   - *signal-rate*: one `[phasor~]` per grain stream sweeps the read position and
     a window signal shapes the amplitude — the book's looping-sampler figures
     are exactly this, with a second lookup for the envelope.
   Start deterministic (fixed rate and offset) so the run is reproducible, then
   add scatter: random position, random rate, random start, random amplitude.
4. **Window every grain.** Multiply by an envelope that is zero at both ends, or
   the entry and exit jump click (Enveloping samplers). `[cos~]` turns its input
   into a cosine, so scale a phase ramp into the range that lands on the positive
   half-cycle (the book does exactly that with a multiply and an offset), or use
   `[line~]` per grain for a control-rate ramp. Overlapping windows partly hide
   the amplitude dip at the edges, so window shape and overlap belong together.
5. **Control the pitch deliberately.** For a looping sampler the transposition
   follows from the scan rate and the segment length together, relative to the
   sample's own sample rate; the *Sampling* section gives the relation, so quote
   it from the excerpt rather than from memory. State which factor you are
   setting: grain duration, read rate, or both. Varying the read position over
   time adds the "scratch"/Doppler effect the book describes — either embrace it
   or hold the pointer steady with `[samphold~]` on a phase wrap.
6. **Mix and keep the level sane.** Sum the grains, then scale. Many overlapping
   grains add up quickly; a `[clip~]` at the end tells you when you are over, and
   `[env~]` gives an RMS reading in dB (1 is 100 dB) for checking. Put gain under
   a `[line~]`-smoothed control if it can change while audio runs.
7. **Verify.** With `run_patch` and then `analyze_audio`: audible, peak below
   clipping, no long silent gaps, no runaway loudness. If a run is not available,
   say what the patch should do and what you did not verify.

## Pitfalls that look like bugs

- **Clicks** at grain edges: the window is missing or does not reach zero. Check
  the envelope timing against the grain duration.
- **Wrong pitch**: confusion between grain duration and read rate, or forgetting
  the sample's own rate. Re-derive from the *Sampling* formulas.
- **Reading outside the array**: clamp or wrap the index explicitly; do not rely
  on unspecified behaviour at an out-of-range index.
- **Silence**: empty array (wrong `[soundfiler]` path), DSP off, a `[line~]` left
  at zero by an earlier switch, or a `[threshold~]`/trigger that never fires.
- **CPU spikes**: a signal-rate voice per grain multiplies fast. Use `[clone]`
  with a modest voice count, or a single scheduler.

## How to answer

Answer the question in prose first — what a grain is, what controls it, how the
pitch and the texture relate — then show one patch of the key signal path (this
project's patch tool caps a diagram at 15 objects). Put every chosen number
(grain length, overlap, scatter range) in the "my suggestion, not documented"
category. Do not claim that a particular grain size or window is the documented
standard: the excerpts describe the mechanism, not a preset.
