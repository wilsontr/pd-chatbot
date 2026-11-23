# Making pd-chatbot Agentic

A research roadmap for adding agentic capability to pd-chatbot so that it reads
as real agentic work in a portfolio, not "agent" as a buzzword. The work is
tracked in Backlog as TASK-5 and its subtasks.

## Where pd-chatbot stands today

`rag.py` is a **fixed pipeline**: classify, retrieve, generate, then validate
the patch or faithfulness and retry. The validation retry loop is already a
small agent loop, but the model never chooses an action and no tool result
changes what happens next.

**The principle:** give the model tools whose results change the outcome, then
use the existing eval harness to show that the agent does better than the
pipeline. Agentic features that can't be measured are a portfolio smell.

**The unusual advantage:** Pd runs headless. Pd can open a patch with no GUI
and render audio offline. So the agent can check its own patches by *running
them*, not just by validating JSON. Build everything else around that.

Priority order below. **Item 1 is the headline.**

---

## 1. Sandboxed code execution: write a patch, run it, listen, fix it

**Tools to expose:**

- `write_patch(pd_patch)`: turns `PdPatch` JSON into a real `.pd` file.
- `run_patch(path, seconds)`: runs Pd with no GUI, renders a WAV file and
  returns Pd's error output. Pd prints messages like "couldn't create" for
  objects it doesn't recognise, which makes a cheap, reliable check.
- `analyze_audio(wav)`: a numpy/scipy script that reports RMS level, peak
  frequency, a summary of the spectrum, and whether there is clipping or
  silence.
- `run_python(code)`: a general scratchpad for DSP maths, for example checking
  a filter's cutoff.

**Example loop:** a user asks for "FM bell with decaying index". The agent
builds the patch and runs it. It sees "couldn't create `fm~`" and searches the
docs for the right objects. It rebuilds, runs again and confirms that the
spectrum decays over time. The answer includes the patch diagram and a
playable audio clip.

**Sandbox options:**

- Docker with gVisor on your own host.
- A hosted service: E2B, Modal sandboxes, or Fly Machines.
- Anthropic's server-side code execution or Managed Agents sandbox. The catch
  is that you need an image with Pd installed, so a self-hosted container is
  probably the simplest choice.

**Show the hardening explicitly**, because reviewers look for it:

- no network access
- CPU, memory and wall-clock limits
- a temporary filesystem
- a read-only base image
- limits on output size

**Measurable result:** add an eval metric for "patch executes cleanly and
meets its spec". Compare pipeline vs. agent, and agent with vs. without the
sandbox.

**Prerequisite:** `PdObject.type` in `pd_schema.py` only allows `obj`, `msg`,
`floatatom` and `comment`, but the frontend renderer supports more box types
(`table`/`array`, `bng`, `tgl`, sliders). Widen the schema before turning it
into `.pd` files for execution.

**Concepts to learn:** tool-use loops, sandbox isolation (gVisor,
Firecracker), verifier-driven self-correction.

---

## 2. Durable agent state

Today, history comes from the client and gets compressed, and the response
cache lives in memory (`TTLCache`). Three layers to add:

- **Run log (event-sourced):** every model turn, tool call and tool result goes
  to Postgres, keyed by `run_id`. This lets you replay runs, debug them and
  resume them after a crash. It connects to the existing tracing hooks
  (`_start_obs`).
- **Durable execution:** wrap the agent loop in a workflow engine such as
  Temporal, DBOS or Inngest. Long sandbox runs then survive a redeploy, and
  you can add human-approval steps. DBOS is the lightest: it's Postgres-only
  and fits Railway.
- **Workspace and memory:** keep the patches each session creates, so a
  follow-up like "make the filter resonant" edits an existing patch.
  Optionally, add per-user memory (Pd version, vanilla vs. Purr Data,
  preferred externals).

**For the demo:** a "run timeline" page that replays a stored run step by step.

**Concepts to learn:** event sourcing, durable execution and checkpointing,
idempotent tool calls.

---

## 3. Loading new skills while the agent runs

- **Use the Agent Skills format:** folders containing a `SKILL.md` plus
  scripts. The agent first sees only each skill's name and one-line
  description. It calls `load_skill(name)` to pull in the full instructions
  when it needs them.
- **Skills that would suit Pd:**
  - granular synthesis
  - Purr Data / Pd-L2Ork differences
  - debugging DSP problems (denormals, DC offset, `block~`)
  - abstractions and `$0` scoping
  - building a patch from a `.pd` file the user uploads
- **Hot loading:** watch a directory or S3 prefix and version each skill. The
  registry is re-read at the start of each turn, so a newly added skill
  appears mid-session without a redeploy.
- **The distinctive twist: gate each skill with evals.** Every skill comes with
  a small golden set. The registry only activates a skill version if its eval
  slice passes in CI. A skill becomes a deployable unit with a quality gate,
  which builds directly on the existing eval work.
- **MCP:** expose the same tools as an MCP server, so Claude Code or Claude
  Desktop can use the Pd sandbox and docs search.

**Concepts to learn:** progressive disclosure of context, MCP, versioned
capability registries.

---

## 4. Interfaces: Slack, CLI, MCP

**The key refactor:** pull the agent loop out of `rag.py` and `main.py` into a
core package, for example `agent/` with `loop.py`, `tools/`, `skills/` and
`store.py`. The loop emits a typed event stream (`text_delta`, `tool_call`,
`tool_result`, `artifact`). Each interface becomes a thin adapter over that
stream:

| Interface | Implementation notes |
|---|---|
| Web (existing) | The current SSE stream sends the events. The UI shows tool calls as collapsible steps and plays rendered audio. |
| CLI | Built with Typer and `rich`: `pdbot ask "…"`, `pdbot run patch.pd`, `pdbot replay <run_id>`. It writes real `.pd` files to disk. |
| Slack | Bolt in Socket Mode. One thread = one session = one durable run. Stream by editing the message, and upload the SVG diagram and WAV clip as files. Add approval buttons for expensive runs. |
| MCP | As described in section 3. |

Several interfaces on one core is itself the portfolio point. It shows the
agent logic is separated from transport.

---

## 5. What makes it presentable

- **Agent evals, not just RAG evals:** task success rate, number of tool
  calls, cost and latency per task, and recovery rate (how often the agent
  fixes its own errors). Publish them in [EVAL.md](EVAL.md) alongside the current tables.
- **Guardrails:** a maximum turn count, a token/dollar budget per run, and tool
  allowlists for each interface (for example, no sandbox from Slack without
  approval). Run the injection check on tool outputs, not just on user input.
- **Artifacts:**
  - an architecture diagram
  - a 60-second video of the self-correction loop: broken patch → error →
    fix → audio plays
  - a write-up of observed failure modes

**Resume payoff:** turns *"built an evaluated RAG system"* into *"built a
tool-using agent that runs and checks its own Pd patches in a hardened
sandbox, with durable resumable runs, eval-gated skills, and web, CLI, Slack
and MCP interfaces on one core."*

---

## Suggested order

1. Refactor to a tool-use loop where retrieval is a tool, and compare it
   against the pipeline on the current evals.
2. Widen `PdPatch`, add the Pd sandbox and the "executes cleanly" metric. This
   is the headline.
3. Add the Postgres run log and replay. Durable execution can come later.
4. Add the CLI first (cheap), then Slack.
5. Add eval-gated skills and an MCP server.

## Skip / deprioritize

- **Multi-agent orchestration** (planner/worker swarms): one agent with good
  tools is easier to evaluate and more convincing at this scale.
- **Agent frameworks as the centerpiece** (LangGraph, CrewAI): fine as an
  implementation detail, but a hand-written loop over the Claude API shows
  more understanding.
