---
name: pd-flavors
description: Pd Vanilla vs Purr Data, Pd-L2Ork, PlugData and Pd-extended — answering version- and flavor-specific questions without guessing at another distribution's behaviour.
---

# Pd flavors: Vanilla, Purr Data, Pd-L2Ork, PlugData, Pd-extended

Use this when the user names a Pd flavor or distribution, asks whether an object
or feature works "in Pd", follows a tutorial that assumes a library, or has a
patch that works on one machine and not another. The excerpts cover **Pd
Vanilla**; everything about another flavor has to be framed that way.

## The documented starting point

The manual states that it covers the official Pure Data distribution by Miller
Puckette — "Pd Vanilla" — and that:

- forks ("flavors") mainly change the GUI and pre-install externals; PlugData
  additionally runs Pd as a plugin in a DAW;
- **Pd-extended** was a historically important flavor with a large external
  collection, discontinued in 2014; many tutorials are based on it, and they can
  still be followed if the needed libraries are installed, but they are outdated;
- the currently active main flavors are listed on the front page of
  puredata.info; some forks introduce incompatibilities and lag behind Vanilla;
- for forks that are fully compatible, the manual mostly applies.

Purr Data and Pd-L2Ork are forks of Pd. If you name them, present that as
context: the excerpts do not describe their internals, their menus, or which
objects they bundle.

## Procedure

1. **Identify the flavor when it changes the answer.** Ask how they installed Pd,
   or what the title bar / About box says — once, not as a questionnaire. If the
   question is flavor-independent (e.g. what a tilde object is), answer it and
   skip the identification.
2. **If the flavor is unknown**, give the Vanilla answer and add a short caveat
   that another flavor may differ; do not turn the answer into a survey.
3. **Check whether an object is documented as Vanilla.** Search the object name
   with `search_docs`. A page in the IEM object reference is evidence it is part
   of the documented Vanilla set. If there is no page, say that the reference
   does not cover it — it may be an external that has to be installed and loaded
   (the manual's Externals chapter covers libraries) — rather than asserting it
   is or is not built in. Do not assume a library object behaves like a
   similarly named Vanilla one.
4. **For fork-specific GUI, menus, preferences or bundled externals**, say the
   documentation you have covers Vanilla and you cannot verify that flavor's
   behaviour; point to its own documentation and community. Never invent an
   object, menu item or default for a fork.
5. **For tutorials that predate Vanilla updates**, name the library the tutorial
   relies on and say what has to be installed. If you translate to Vanilla
   objects, flag the translation as your suggestion — "equivalent" is a claim
   about behaviour, and library objects are not guaranteed to match.

## Typical bugs

- Answering a Purr Data question with features from the Vanilla manual as if they
  were verified there (or the reverse).
- Assuming an object is built in because it appeared in a tutorial; the object
  reference is the check.
- Calling Pd-extended current, or implying its objects ship with Vanilla.
- Claiming a GUI feature exists in "Pd" when the manual describes Vanilla's
  interface only.

## How to answer

State the flavor assumption in one line, give the answer that the documentation
supports, and mark the fork-specific part as unverified. When the excerpts do
not cover a flavor, say so plainly instead of filling the gap — an honest "the
Pd docs I have cover Vanilla; check Purr Data's own docs for that menu" is a
better answer than a plausible guess.
