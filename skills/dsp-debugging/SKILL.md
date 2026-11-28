---
name: dsp-debugging
description: Use when a Pd audio patch misbehaves — no sound, clicks or crackles (including from a slider or [line~]), distortion or clipping, a DC offset, CPU spikes on quiet tails, extra latency from [throw~]/[catch~] or feedback, a subpatch that goes silent after [block~] or [switch~], or an FFT patch that breaks — and to inspect signal values with [print~].
---

# Debugging DSP problems in Pd

Use this when the user says a patch is silent, clicks, crackles, distorts, has a
DC offset, uses too much CPU, or behaves differently after adding `[block~]`,
`[switch~]` or a subpatch. Work through the causes in order and show the user
what each check rules out. Cite the excerpts from `search_docs`; this file is a
procedure, not a source.

## Triage, in this order

1. **Is it running at all?** DSP on, `[dac~]` connected, audio settings sane, and
   the run's console output (when `run_patch` is available) free of errors. A
   patch that loads silently is usually not a DSP bug yet.
2. **Signal or control?** `[inlet]`/`[outlet]` carry control messages and
   `[inlet~]`/`[outlet~]` carry signals; a signal outlet only takes signals
   (Subpatches). Check whether the value that never arrives is a float where a
   signal is expected, or the reverse. `[inlet~]` is the documented exception:
   inside the subpatch it also has a right outlet that carries control messages.
3. **Reblocking.** `[block~]` and `[switch~]` set block size, overlap and
   up/downsampling for a window and its subwindows; there may be only one per
   window (Switching and blocking; the `[block~]` page). `[inlet~]`/`[outlet~]`
   reblock between parent and child, but only by power-of-two factors. The FFT
   objects' channel count is the block size, so a wrong block size breaks
   spectral patches quietly. Block size 1 is the documented way to get
   sample-by-sample feedback; smaller blocks also reduce block delay.
4. **Switching.** A switched-off subpatch outputs zeros, so a "missing" signal
   can be a `[switch~]` that is off — including a parent window's switch. Give a
   `[line~]` time to reach zero before switching off, or it is stuck at a
   non-zero level for the next on (Switching and blocking).
5. **Order of execution and feedback.** `[throw~]` has to be sorted before its
   `[catch~]`, or you never get less than a block of delay (the `[throw~]`
   page). Delay-line patches have documented lower limits and ordering rules
   (the book's "Order of execution" examples). A loop that looks one block short
   is usually block delay, not a wrong object.
6. **Numbers.** Summed signals can exceed the range, so check for clipping
   (`[clip~]` between limits, `[env~]` for RMS). A persistent offset is DC; a
   one-pole high-pass such as `[hip~]` at a few Hz is the usual way to remove it
   — that is general technique, the docs only describe `[hip~]` as a high-pass.
   Very quiet tails getting expensive or producing denormals is **not covered by
   the Pd docs**: say so, describe it as general floating-point DSP practice, and
   offer measures as suggestions. Write that part with **no citation at all** —
   no excerpt supports it, so any link attached to it is wrong.

## Documented anchors worth searching for

These are **search targets, not citations**. `search_docs` for the ones the
symptom needs (by `object_names` for object pages), and cite only what comes
back: copy the `Source:` URL printed under the excerpt. Never type a URL, a
section number or a page from memory, and never cite a section you did not
retrieve in this answer — an uncited true sentence is better than a citation
the excerpts do not contain.

- **Switching and blocking** (2.5.4): block size, FFT channel count, 1-sample
  feedback at block size 1, clicks when switching, zeros from switched windows,
  `[throw~]`/`[catch~]` as a cheaper way to collect switched output.
- **`[block~]` / `[switch~]` pages**: arguments (size, overlap, up/downsampling),
  one per window, default block size, power-of-two reblocking.
- **Subpatches** (2.8): `[inlet]`/`[outlet]` create the box's inlets and outlets
  in left-to-right order; `[inlet~]`/`[outlet~]` are the signal versions.
- **Order of execution and lower limits on delay times** (book examples).
- **`[line~]`**: its right inlet does not remember the previous ramp time, so a
  bare float jumps instead of ramping — a classic source of clicks.
- **`[env~]`**: RMS in dB, with a full-scale signal at 100 dB.
- **`[print~]` / `[bang~]`**: inspect raw block values; `[bang~]` fires per DSP
  block and needs a block of at least 64.
- **Arrays** (2.9): allocate and load before DSP starts; a sound loaded during
  playback can drop out.

## Habits that make the answer useful

- Ask for the smallest missing fact only when it changes the diagnosis: Pd
  version/flavor, block size, whether the patch was tested with a mouse.
- Name the check and what it would show: "if you see zeros from the subpatch,
  the switch is off", not just "check switch~".
- When the cause is not in the documentation (denormals, CPU tuning), label the
  advice as your own and do not invent a Pd object or a default value.
- If you can run patches, reproduce the fault, fix it, and report what the last
  run showed; don't claim a fix you did not run.

## How to answer

Start with the most likely cause given the symptom, then list the remaining
checks in the order above. Prefer one or two concrete object-level suggestions
over a survey. Keep patch diagrams to the failing path, and flag any threshold,
cutoff or gain you choose as a starting point.
