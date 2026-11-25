# Pd skills

Skills are small, versioned playbooks the agent can load when a question calls
for one. They are **not** part of the answer prompt: the model sees only each
skill's name and one-line description in the `load_skill` tool, and the
instructions arrive only when it asks for them. That keeps every request cheap
while the project can carry many skills with long instructions.

## Format

One folder per skill, with a `SKILL.md` and any files its instructions refer to:

```
skills/
  granular-synthesis/
    SKILL.md                 # front matter + instructions
    scripts/grain_cloud.py   # optional: listed when the skill loads
    examples/grain-cloud.pd
```

`SKILL.md` opens with flat front matter:

```markdown
---
name: granular-synthesis
description: Grain clouds in Pd — windowing, overlap, and pitch scatter.
version: 1
---

# Granular synthesis in Pd

...the instructions the model reads when it loads this skill...
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Must equal the folder name. Lowercase letters, digits, `-`, `_`, `.` |
| `description` | yes | One line, at most 500 characters. This is the *trigger*: the model decides whether to load the skill from it alone |
| `version` | no | A short string. When omitted, the version is a digest of the skill's files, so an edit changes it automatically |

The body is the instruction text. It can reference the bundled files by relative
path (`scripts/grain_cloud.py`); the whole folder is listed in the tool result so
the model knows what is there. A skill that is malformed — no description, no
body, a name that does not match its folder, a duplicate name — is skipped with a
warning in the logs and does not affect the other skills.

## Hot reload

The registry reads the directory; it does not cache a copy across turns.

* The loop calls `ToolRegistry.refresh()` at the start of **every turn**, so a
  skill added while a session is running appears in the next turn's tool
  description. No restart, no redeploy.
* `load_skill` re-reads the skill's folder on every call, so an edited
  `SKILL.md` (or a changed bundled file, which changes the digest) is served the
  next time it is loaded.

The watched directory is `$SKILLS_DIR`, or `skills/` in the repository when the
variable is unset.

## Versioning and the run log

Every load records the skill name and the version the model actually read. The
record travels in the `load_skill` tool result (`data.loaded_skill`), the run's
`done` event (`skills`), the Langfuse trace metadata, and
`RunStore.reconstruct_state()` — so a stored run can be audited for the exact
skill version that shaped its answer. This is the hook the eval gate
(TASK-5.15) uses to decide whether a skill version is good enough to activate.

## Trust

Skill files are repository content, not user input, but the agent treats a
skill's text the same way it treats any other tool output: the prompt-injection
check runs on tool results, including `load_skill`, and the answer prompt still
requires citations to come from `search_docs` excerpts rather than a skill. A
skill guides *how* to answer; it is never a source.
