---
name: working-from-a-pd-file
description: Use before answering any message that contains Pd patch file text (lines such as #N canvas, #X obj, #X msg, #X connect) or that is about the user's own .pd file or existing patch — explaining what it does, reading one record, or changing it. Covers reading the canvas text, rebuilding the key path with the patch tool, and revising it without losing the user's structure.
---

# Working from a `.pd` file the user supplies

Use this when the user pastes Pd canvas text (it starts with `#N canvas`), says
they attached or uploaded a `.pd` file, describes an existing patch they want
changed, or asks you to read a patch's structure. This assistant has no tool that
opens an uploaded file: if the patch text is not in the conversation, ask for the
relevant fragment (or the object and connection lines) before guessing.

## Reading the canvas text

A saved patch is a list of records, one per line, each ending in `;`:

| Record | Meaning |
|---|---|
| `#N canvas x y w h font;` | the patch header |
| `#X obj x y text;` | an object box (`osc~ 440`, `tabread4~ $0-buf`, …) |
| `#X msg x y text;` | a message box (`bang`, `0 4 2000`, `$1`) |
| `#X text x y text;` | a comment — not an object |
| `#X floatatom x y …;` | a number box; its value is not part of the file |
| `#X connect src outlet dst inlet;` | a wire; the numbers are **box order** (0-based), not ids |
| `#X restore x y pd name;` | a saved subpatch (its boxes appear before this line) |
| `#X array …` / `#A …;` | array declaration and its saved data |

In box text, `,`, `;` and `\` are escaped, so a message containing a comma
appears as `\,` — do not read the escape as part of the message. Subpatch
contents are interleaved in the same file; an `#X connect` inside a subpatch does
not connect to the parent.

## Procedure

1. **Restate what you read.** List the objects (with their creation arguments),
   the signal path, any subpatches, and any arrays. If a line is truncated or an
   object name is unfamiliar, say which one rather than inventing it.
2. **Ask only for what the change needs.** The file's Pd version, an external
   library, or a subpatch's contents may matter; the rest of the patch usually
   does not.
3. **Rebuild the key path with `write_patch`.** One object per canvas box, with
   ids chosen by you and connections by `srcId`/`dstId`/outlet/inlet. Keep the
   user's creation arguments exactly (`osc~ 440`, `pack 0 100`). The diagram tool
   does not model subpatches: flatten the path the question is about, and list
   what you left out instead of silently dropping it.
4. **Preserve the user's structure.** If the patch came from an earlier turn in
   this conversation, use `list_patches`/`read_patch` and edit it by id so there
   is one patch, not a near-duplicate. If it is the user's own file, write a new
   patch named after it and say that you cannot save to their disk — they still
   have their original.
5. **Verify what you made.** With `run_patch` and `analyze_audio` when available:
   check the console for errors, the patch for silence/clipping, and that the
   behaviour the user asked for actually happens. If you cannot run it, say so.
6. **Report the diff.** What you kept, what you added, what you omitted
   (subpatches, GUI controls, saved array data), and what the user should check
   in Pd.

## Pitfalls

- Treating an `#X text` comment line as an object, or a `#X connect` index as an
  id.
- Dropping an external object because it is unfamiliar. Name it as an external
  and say the documentation you have may not cover it.
- Rebuilding a 60-object patch in full when the question is about four boxes —
  the diagram limit is 15 objects, so show the path and describe the rest.
- Inventing inlet or outlet counts for a user's object. Copy what the file says,
  and if the file does not say, do not assert a count.
- Claiming the original file was edited. Only the patch you write exists here.

## How to answer

Open with a one-paragraph reading of their patch (what it does, how the signal
flows) and then the change you made. Put the rebuilt patch after the prose. Any
assumption about an ambiguity in their file should be visible as an assumption,
not folded into the patch.
