# Pd skills

Skills are small, versioned playbooks the agent can load when a question calls
for one. They are **not** part of the answer prompt: the model sees only each
skill's name and one-line description in the `load_skill` tool, and the
instructions arrive only when it asks for them. That keeps every request cheap
while the project can carry many skills with long instructions.

## Format

One folder per skill, with a `SKILL.md` and any files its instructions refer to (this
tree is illustrative; the shipped skills are listed below):

```
skills/
  granular-synthesis/
    SKILL.md                 # front matter + instructions
    golden.json              # the questions this skill should improve
    scripts/grain_cloud.py   # optional: listed when the skill loads
    examples/grain-cloud.pd
```

Six skills ship today: `granular-synthesis`, `dsp-debugging`,
`abstractions-and-dollar-zero`, `working-from-a-pd-file`, `pd-flavors` and
`wavetables`.

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
| `version` | no | A short string. When omitted, the version is a digest of the skill's files, so an edit changes it automatically. The shipped skills omit it: every content edit is a new version, and TASK-5.15 judges versions |

The body is the instruction text. It can reference the bundled files by relative
path (`scripts/grain_cloud.py`); the folder is listed in the tool result so the
model knows what is there (`golden.json` is the exception — it is eval data, not
a resource, so it is not advertised). A skill that is malformed — no description,
no body, a name that does not match its folder, a duplicate name — is skipped
with a warning in the logs and does not affect the other skills.

## Golden sets

Every skill shipped here carries the small question list it is supposed to improve in
`golden.json`:

```json
{
  "skill": "granular-synthesis",
  "questions": [
    {
      "id": "gs-001",
      "question": "How do I build a granular sampler in Pd that plays short grains from a recorded sample?",
      "expects": ["grain"],
      "notes": "What a good answer does, for the judge or a reviewer."
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `skill` | yes | Must equal the folder name, so a copied file cannot silently measure the wrong skill |
| `questions` | yes | 3–12 entries. Small on purpose: a smoke slice, not the generation golden set |
| `questions[].id` | yes | Unique within the skill; stable across edits so a version's results stay comparable |
| `questions[].question` | yes | The question, as the user would ask it |
| `questions[].expects` | no | Terms a good answer should contain (case-insensitive). A cheap signal, not a wording test |
| `questions[].notes` | no | What a good answer does — what a judge or reviewer checks |

`agent/skills/golden.py` validates the file on read and raises `SkillError` on a
name mismatch, a duplicate id, an empty question or an out-of-range count, so a
schema slip cannot quietly produce an eval slice that measures nothing. Because
`golden.json` lives in the skill folder it is part of the version digest: changing
the questions produces a new skill version, which has to pass the gate below.

## Activation: a skill version is a deployable unit

A skill folder is **not** live just because it changed. `skills/activation.json`
pins, per skill, the content digest that may serve:

```json
{
  "version": 1,
  "skills": {
    "granular-synthesis": {
      "version": "41e503ae5451",
      "digest": "41e503ae5451…",
      "activated_at": "2026-11-26T00:15:28Z",
      "activated_by": "github-actions[bot]",
      "metrics": {"faithfulness_mean": 4.7, "relevance_mean": 4.9, "load_rate": 1.0}
    }
  }
}
```

The snapshot of each activated digest lives in `skills/.activated/<name>/<digest>/`.
Serving resolves to that snapshot whenever the working folder's digest does not
match the manifest, so an ungated edit is ignored and the **previously activated
version keeps serving**. If the snapshot is missing, the skill is not served at
all — a manifest that cannot be honoured fails closed.

A directory with **no** `activation.json` is the development view: every folder
serves. That is how the test suite and a scratch `$SKILLS_DIR` behave.
`SKILLS_ACTIVATION=off` gives you the same permissive view in a repository that
does have a manifest, which is the quickest way to iterate on a skill locally.

| Command | What it does |
|---|---|
| `python -m evaluation.skills --changed-from origin/main --gate --activate` | CI: run the changed skills' slices, gate them, pin the winners |
| `python -m evaluation.skills --skills granular-synthesis --gate` | Run one slice and check the gate without activating |
| `python -m evaluation.skills --all --gate` | Run every slice (a release check) |
| `python -m evaluation.skills --render-report docs/SKILLS.md` | Rewrite the per-skill report from the manifest, no API calls |
| `python -m evaluation.skills --baseline` | Bootstrap: pin current versions unmeasured. CI never calls it |

The gate floors are the generation eval's judge floors plus a load-rate floor
(4.0 faithfulness, 4.5 relevance, 4.0 citation, at least half the slice must
actually call `load_skill` — a skill nobody selects is not helping). A slice is
answered by the real agent with only the skill under test available, so the
measurement is attributable to that skill. Results are in
[`docs/SKILLS.md`](../docs/SKILLS.md), and the generated
[`docs/EVAL.md`](../docs/EVAL.md) carries a per-skill summary from the manifest.

`.github/workflows/skills.yml` runs the gate for the skills a pull request
changes, commits the activation and the report back to the PR branch, and fails
the job on a missed threshold. A failed slice changes nothing, which is exactly
how the previous version stays live. A direct push to `main` that skips the
workflow leaves the changed skill on its previously activated version (and a new
skill is not served at all); run the workflow from the Actions tab, or
`python -m evaluation.skills --changed-from HEAD~1 --gate --activate` locally, to
publish it.

## Hot reload

The registry reads the directory; it does not cache a copy across turns.

* The loop calls `ToolRegistry.refresh()` at the start of **every turn**, so a
  newly activated skill (or a new snapshot) is served from the next turn on, with
  no restart.
* `load_skill` re-reads the activated version on every call, so activating a new
  version takes effect on the next load.

The watched directory is `$SKILLS_DIR`, or `skills/` in the repository when the
variable is unset.

## Versioning and the run log

Every load records the skill name and the version the model actually read. The
record travels in the `load_skill` tool result (`data.loaded_skill`), the run's
`done` event (`skills`), the Langfuse trace metadata, and
`RunStore.reconstruct_state()` — so a stored run can be audited for the exact
skill version that shaped its answer. The gate's per-skill report
([`docs/SKILLS.md`](../docs/SKILLS.md)) stores the measured evidence next to the
activated digest, so a version's numbers travel with the version that earned them.

## Trust

Skill files are repository content, not user input, but the agent treats a
skill's text the same way it treats any other tool output: the prompt-injection
check runs on tool results, including `load_skill`, and the answer prompt still
requires citations to come from `search_docs` excerpts rather than a skill. A
skill guides *how* to answer; it is never a source.
