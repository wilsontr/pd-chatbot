---
name: wavetables
description: Wavetable oscillators in Pd — filling a table with a waveform (a sine, partials, or a computed shape) when the patch opens, playing it at a pitch with tabosc4~ or phasor~ into tabread4~, and checking the table really holds the wave.
---

# Wavetable oscillators in Pd

Use this when the user asks for a wavetable oscillator, to fill a table or array
with a waveform (one cycle of a sine, a sum of partials, a drawn or computed
shape), or to play a table back at a pitch. Cite only the excerpts `search_docs`
returned; this file is a procedure, not a source.

## Ground it in the documentation first

`search_docs` with `object_names` set to the objects you will use. The sections
that carry this topic:

- **`[tabosc4~]` page** — the wavetable oscillator: frequency in Hz in the left
  inlet, a table of a power of two points **plus three guard points**, and the
  `sinesum` / `cosinesum` array methods that write the guard points for you. It
  also says not to send new `sinesum` messages while the table is playing.
- **`[tabread4~]` page** — 4-point interpolated lookup with a signal index.
- **`[table]` / `[array]` pages and Arrays (manual 2.9)** — named storage,
  indexed 0 to N-1, allocated and filled before DSP needs it.
- **Wavetables and samplers** (Puckette, *Theory and Technique*) — wavetable
  lookup in general and why the index is scaled to the table size.

## Pick the route — the documented one first

1. **A sine or a sum of partials: `sinesum` + `[tabosc4~]`.** This is the
   shortest correct patch and every part of it is on the `[tabosc4~]` page.

   ```
   [loadbang]
   |
   [; wave sinesum 512 1(        [table wave 515]

   [sig~ 220]
   |
   [tabosc4~ wave]               <- the argument is the table name, not a pitch
   |
   [*~ 0.2]
   |
   [dac~]
   ```

   The message box starts with `;`, so it is sent to the receiver named `wave`
   (the table) rather than out of its outlet — it needs no wire into the table.
   `sinesum 512 1` resizes the table to 512 + 3 points and writes one partial at
   amplitude 1; more amplitudes (`sinesum 512 1 0.5 0.33`) add partials. The
   frequency goes into the left inlet, as a float or a signal such as
   `[sig~ 220]`; the creation argument is the table name. Flag the message
   syntax as your suggestion if no excerpt shows it.

2. **`[phasor~]` into `[tabread4~]`** when the user asks for that structure.
   `[phasor~ 220]` ramps 0 to 1, so scale it to the table length with `[*~ N]`
   before `[tabread4~]`. With a `sinesum` table (N + 3 points) the cycle sits at
   indices 1 to N, so add 1 (`[+~ 1]`) after the scaling; with a table you fill
   yourself at indices 0 to N-1, scale by N and leave the offset out.

3. **A shape `sinesum` cannot make** (a computed curve, a ramp, a window): fill
   the table with a counter loop, and get these four things right — each one,
   wrong, gives a patch that runs cleanly and is silent:

   ```
   [loadbang]
   |
   [512(                       <- the count goes to [until]'s LEFT inlet
   |
   [until]                     <- a float N means "bang N times"; 0 means none
   |
   [f]x[+ 1]                   <- [until]'s outlet must drive [f]
   |
   [t f f]                     <- right outlet fires first: index, then value
   |     \
   |      [tabwrite wave] right inlet (index)
   [expr sin($f1 * 2 * 3.14159265 / 512)]
   |
   [tabwrite wave] left inlet (value — writes at the index just set)
   ```

   - Send the **count** (`512`) to `[until]`. A `0` or a reset value sent to
     `[until]` runs the loop zero times. A bare `bang` loops until something
     sends `[until]` a bang in its **right** inlet, so it needs a stop condition.
   - Wire **`[until]` → `[f]`**. Without that wire the counter never steps.
   - `[f]` starts at 0, so a fill that runs once on load needs no reset. If it
     can run again, reset `[f]` through its right inlet with its own `[0(`,
     sent before the count (`[t b b]`), never with the message that starts
     `[until]`.
   - Use **`[t f f]`** so the index reaches `[tabwrite]`'s right inlet before the
     value reaches its left inlet. Fanning one outlet out to both inlets leaves
     the order to the order the wires were made, and the table comes out shifted
     by one sample.

   Flag the loop as your own construction if the excerpts do not show it.

## Check that the table holds the wave

When you can run patches, a clean run that is **silent** usually means the
table is still all zeros, not that the read side is wrong. Check the fill on its
own before changing the playback chain: add `[loadbang]` → `[delay 10]` →
`[128(` → `[tabread wave]` → `[print]` (128 is a quarter cycle of 512, so a
filled sine prints about 1; index 129 for a `sinesum` table). A `0` means the
fill never ran — fix the loop, then take the check out again and re-run with
`analyze_audio` to confirm the pitch.

## How to answer

Lead with the route you used and why, then walk the patch: what fills the table,
when, and what plays it at the asked pitch. Keep the level modest (`[*~ 0.2]`)
and say so. Mention the guard points whenever `[tabosc4~]` is in the patch.
