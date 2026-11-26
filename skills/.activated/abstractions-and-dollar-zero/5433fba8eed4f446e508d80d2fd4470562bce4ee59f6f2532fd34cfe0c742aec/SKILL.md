---
name: abstractions-and-dollar-zero
description: Abstractions, subpatches, creation arguments and $0-local names in Pd — passing arguments, local send/receive pairs, arrays and value/text names.
---

# Abstractions and `$0` scoping in Pd

Use this when the user asks about abstractions, one-off subpatches, passing
arguments into a patch, local names that must not collide between copies,
`$0`, `$1`, `[pdcontrol]`, `[clone]`, or "why does my second copy share the
first one's value". Search the docs before answering; this file is a procedure.

## The documented model

- **Two kinds of subpatch.** `[pd]` or `[pd name]` creates a *one-off subpatch*:
  its contents are saved inside the parent patch, and copies can be edited
  individually. An *abstraction* is a separate `something.pd` file invoked as
  `[something]`; every instance shares the file, and a saved edit affects
  instances created afterwards (Subpatches, 2.8 and 2.8.1).
- **Inlets and outlets.** `[inlet]`, `[outlet]`, `[inlet~]` and `[outlet~]`
  inside the subpatch create the box's inlets and outlets, in left-to-right
  order, for both kinds of subpatch. Control inlets/outlets carry messages;
  the tilde versions create signal inlets/outlets (an `[inlet~]`'s right outlet
  can still carry control inside the subpatch).
- **When `$` expands.** In an *object box*, `$1`, `$2`, … are substituted at
  **creation time** from the abstraction's arguments (and are undefined in a
  new or opened document). In a *message box*, they are substituted at
  **message time** from the incoming list. This difference is the source of most
  `$1` bugs; `$1-x` concatenates.
- **`$0` is a per-patch counter.** Every patch gets its own value, which is what
  makes `$0-bear` a local send/receive pair, and gives local array names,
  `[value]` names and `[text]` names that do not collide between instances
  (Dollar signs, 2.7.6).
- **IEMguis are special.** Their send/receive names are set by message, so to
  store a literal `$0` you must escape it with a backslash — the docs call this
  out explicitly.
- **Many instances.** `[clone]` creates and manages copies of an abstraction;
  inside each copy `$1` is the instance number (counting from 0 unless changed
  with `-s`, or suppressed with `-x`), and further arguments appear as `$2`…
- **Variable argument count.** `[pdcontrol]` with the `args` message returns the
  patch's arguments as a list, which is the documented way to accept a variable
  number of arguments and to preserve their atom types.
- **Graph on parent** shows a subpatch's controls on the parent box (2.8.2);
  only controls become visible, and you open the subpatch with right-click →
  Open.

## Procedure for writing or fixing one

1. Decide the shape: one-off subpatch (contents in the same file, edited per
   copy) or abstraction (reused, a separate file). Say which and why.
2. Create the interface with `[inlet]`/`[outlet]` (or the `~` versions) in the
   order you want the box's inlets/outlets to read. Order is left-to-right.
3. Take arguments through object boxes, e.g. `[f $1]`, `[symbol $0-$1]`, not
   through message boxes that only see the message. If the number of arguments
   varies, use `[pdcontrol]` → `args`.
4. Give every local name the `$0-` prefix: `[send $0-trigger]`,
   `[receive $0-trigger]`, `[array define $0-buffer]`, `[value $0-level]`,
   `[text define $0-notes]`. Two copies then behave independently, including
   arrays and `[value]` storage.
5. For IEMguis, send the `$0`-bearing name as an escaped message (`\$0-name`),
   or set it in the properties dialog, rather than expecting the box text to
   expand.
6. If you need several substitution stages, nest abstractions: each stage adds
   its own `$` variable and passes the result to the next.
7. Sanity-check the wiring: the box's inlets/outlets match the `[inlet]`/
   `[outlet]` order, arguments are defined, and a second instance does not share
   state with the first.

## Typical bugs

- `$1` in a message box that never receives an argument list, or `$1` used in
  the main patch where creation arguments do not exist.
- Two copies sharing `[send foo]`/`[receive foo]`, an array name, or a `[value]`
  name — the fix is `$0` on both ends.
- Editing a one-off subpatch expecting other copies to change, or editing an
  abstraction expecting already-open instances to update.
- Forgetting that a one-off subpatch lives in the parent file, while an
  abstraction is a separate file that has to be findable on Pd's path.
- Using `$0` in an IEMgui message without escaping it.

## How to answer

Name the mechanism (one-off vs abstraction, creation- vs message-time
substitution, per-patch `$0`) with the excerpt that states it, then give one
concrete object-level example. Keep the example's object names and arguments
exact. If the user shows a patch, point at the specific box that is wrong rather
than rewriting the whole patch; flag anything you add as your suggestion.
