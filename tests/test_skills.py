"""Tests for the runtime skills registry and the ``load_skill`` tool (TASK-5.13).

Four things have to hold for skills to be a real capability rather than a prompt
trick:

* the model sees only names and descriptions until it asks for one,
* adding or editing a skill in the watched directory takes effect on the next
  turn, with no restart,
* a run records which skill *version* it loaded, and
* a skill whose files are broken does not take the others offline.

The loop tests use the scripted ``StubClient`` from ``test_agent_loop``: no API
key and no network, but the real refresh-per-turn path.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent import cache, chat
from agent.events import Done, ToolResult
from agent.llm import ModelRequest
from agent.loop import AgentConfig, AgentLoop
from agent.skills import (
    SkillRegistry,
    default_skills_root,
    parse_front_matter,
)
from agent.store import RunStore
from agent.tools import ToolRegistry
from agent.tools.skills import MAX_INSTRUCTIONS_CHARS, LoadSkillTool
from tests.test_agent_loop import StubClient, StubTool, answer, tool_turn

BODY_SENTINEL = "GRAIN-WINDOW-SENTINEL"
GRAIN_BODY = (
    f"# Granular synthesis\n\n{BODY_SENTINEL}: overlap grains with a {{{{Hann}}}} window.\n"
    "Use [array] + [tabread4~]."
)


def write_skill(
    root: Path,
    name: str = "granular-synthesis",
    *,
    description: str = "Grain clouds in Pd - windowing, overlap, and pitch scatter.",
    body: str = GRAIN_BODY,
    version: str | None = None,
    files: dict[str, str] | None = None,
    front_matter: bool = True,
) -> Path:
    """Create a skill folder on disk and return it."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    header = ""
    if front_matter:
        header = "---\n"
        if name:
            header += f"name: {name}\n"
        if description is not None:
            header += f"description: {description}\n"
        if version is not None:
            header += f"version: {version}\n"
        header += "---\n\n"
    (directory / "SKILL.md").write_text(header + body, encoding="utf-8")
    for relative, content in (files or {}).items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


# --- Discovery ---------------------------------------------------------------


class TestDiscovery:
    def test_skill_is_discovered_with_front_matter_and_body(self, skills_root):
        write_skill(skills_root)
        registry = SkillRegistry(skills_root)

        skills = registry.skills()

        assert [skill.name for skill in skills] == ["granular-synthesis"]
        skill = skills[0]
        assert skill.description.startswith("Grain clouds in Pd")
        assert BODY_SENTINEL in skill.instructions
        assert "description:" not in skill.instructions  # front matter stripped
        assert skill.path == skills_root / "granular-synthesis"
        assert registry.diagnostics() == ()

    def test_folders_without_a_skill_md_are_not_skills(self, skills_root):
        (skills_root / "README.md").write_text("not a skill", encoding="utf-8")
        (skills_root / "notes").mkdir()
        (skills_root / "notes" / "todo.md").write_text("later", encoding="utf-8")

        registry = SkillRegistry(skills_root)

        assert registry.skills() == ()
        assert registry.diagnostics() == ()

    def test_existing_skills_directory_with_only_a_readme_is_empty(self, skills_root):
        (skills_root / "README.md").write_text("# skills", encoding="utf-8")
        assert SkillRegistry(skills_root).names() == []

    def test_a_missing_directory_is_an_empty_registry_not_an_error(self, tmp_path):
        registry = SkillRegistry(tmp_path / "does-not-exist")
        assert registry.skills() == ()
        assert registry.get("anything") is None

    def test_bundled_files_are_listed_and_excluded_from_the_instructions(self, skills_root):
        write_skill(
            skills_root,
            files={
                "scripts/grain_cloud.py": "print('grains')\n",
                "examples/grain-cloud.pd": "#N canvas 0 0 300 300 0;\n",
                ".DS_Store": "junk",
                "__pycache__/grain.cpython-312.pyc": "cache",
            },
        )
        skill = SkillRegistry(skills_root).skills()[0]

        assert skill.files == ("examples/grain-cloud.pd", "scripts/grain_cloud.py")

    def test_version_is_a_content_digest_that_changes_on_edit(self, skills_root):
        write_skill(skills_root)
        registry = SkillRegistry(skills_root)
        before = registry.skills()[0]

        write_skill(skills_root, body=GRAIN_BODY + "\n\nOne more sentence.")
        after = SkillRegistry(skills_root).skills()[0]

        assert before.version == before.digest[:12]
        assert before.version != after.version
        assert before.digest != after.digest

    def test_a_bundled_file_change_changes_the_version(self, skills_root):
        write_skill(skills_root, files={"scripts/grain.py": "v1\n"})
        before = SkillRegistry(skills_root).skills()[0]
        (skills_root / "granular-synthesis" / "scripts" / "grain.py").write_text("v2\n")
        after = SkillRegistry(skills_root).skills()[0]

        assert before.version != after.version

    def test_the_golden_set_is_hashed_but_not_listed_as_a_bundled_file(self, skills_root):
        write_skill(skills_root)
        before = SkillRegistry(skills_root).skills()[0]

        (skills_root / "granular-synthesis" / "golden.json").write_text(
            '{"skill": "granular-synthesis", "questions": []}', encoding="utf-8"
        )
        after = SkillRegistry(skills_root).skills()[0]

        assert after.files == ()
        # The eval slice belongs to the skill version, but it is not a resource
        # the model is pointed at.
        assert after.digest != before.digest

    def test_a_declared_version_is_used_and_the_digest_still_tracks_content(self, skills_root):
        write_skill(skills_root, version="1.2.0")
        skill = SkillRegistry(skills_root).skills()[0]
        assert skill.version == "1.2.0"
        assert skill.declared_version == "1.2.0"

        write_skill(skills_root, version="1.2.0", body=GRAIN_BODY + "\nEdited prose.")
        edited = SkillRegistry(skills_root).skills()[0]
        assert edited.version == "1.2.0"  # the declared version stays authoritative
        assert edited.digest != skill.digest  # but the edit is still visible

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"description": ""}, "description"),
            ({"body": ""}, "no instructions"),
            ({"front_matter": False}, "description"),
        ],
    )
    def test_malformed_skills_are_skipped_with_a_diagnostic(self, skills_root, kwargs, message):
        write_skill(skills_root, name="broken", **kwargs)
        write_skill(skills_root)  # a good skill must still load

        registry = SkillRegistry(skills_root)

        assert registry.names() == ["granular-synthesis"]
        assert len(registry.diagnostics()) == 1
        assert message in registry.diagnostics()[0].message

    def test_front_matter_name_must_match_the_folder(self, skills_root):
        directory = skills_root / "folder-name"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: other-name\ndescription: A skill.\n---\n\nInstructions.\n"
        )

        registry = SkillRegistry(skills_root)

        assert registry.names() == []
        assert "does not match" in registry.diagnostics()[0].message

    def test_invalid_names_are_rejected(self, skills_root):
        directory = skills_root / "Bad Name"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: Bad Name\ndescription: A skill.\n---\n\nInstructions.\n"
        )

        assert SkillRegistry(skills_root).names() == []

    def test_duplicate_names_keep_the_first_and_report_the_second(
        self, skills_root, monkeypatch
    ):
        import agent.skills as skills_module

        write_skill(skills_root, name="one")
        write_skill(skills_root, name="two", body="other body")
        real_reader = skills_module._read_skill

        def both_claim_one(directory):
            skill = real_reader(directory)
            # Names normally equal their folder name, so a collision needs two
            # folders claiming the same name; force it to exercise the guard.
            return replace(skill, name="one") if skill is not None else None

        monkeypatch.setattr(skills_module, "_read_skill", both_claim_one)

        registry = SkillRegistry(skills_root)

        assert registry.names() == ["one"]
        assert len(registry.diagnostics()) == 1
        assert "duplicate" in registry.diagnostics()[0].message.lower()

    def test_parse_front_matter_handles_the_flat_format(self):
        fields, body = parse_front_matter(
            "---\n"
            "name: purge-differences\n"
            'description: "Purr Data: what differs from vanilla Pd"\n'
            "version: 2\n"
            "---\n\n"
            "Instructions here.\n"
        )
        assert fields == {
            "name": "purge-differences",
            "description": "Purr Data: what differs from vanilla Pd",
            "version": "2",
        }
        assert body == "Instructions here."

    @pytest.mark.parametrize(
        "text",
        ["Just markdown.\n", "---\nname: unterminated\n"],
    )
    def test_text_without_complete_front_matter_is_all_body(self, text):
        fields, body = parse_front_matter(text)
        assert fields == {}
        assert body == text

    def test_empty_front_matter_yields_no_fields_and_an_empty_body(self):
        fields, body = parse_front_matter("---\n---\n")
        assert fields == {}
        assert body == ""

    def test_default_root_follows_the_skills_dir_environment_variable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SKILLS_DIR", str(tmp_path / "watched"))
        assert default_skills_root() == tmp_path / "watched"
        monkeypatch.delenv("SKILLS_DIR")
        assert default_skills_root().name == "skills"


# --- The tool contract -------------------------------------------------------


class TestLoadSkillTool:
    def test_the_spec_shows_names_and_descriptions_but_not_instructions(self, skills_root):
        write_skill(skills_root)
        write_skill(
            skills_root,
            name="purr-data-differences",
            description="Purr Data / Pd-L2Ork differences from vanilla Pd.",
            body="PURR-BODY-SENTINEL",
        )
        tool = LoadSkillTool(SkillRegistry(skills_root))

        description = tool.spec.description

        assert "granular-synthesis" in description
        assert "Purr Data / Pd-L2Ork differences" in description
        assert "purr-data-differences" in description
        assert BODY_SENTINEL not in description
        assert "PURR-BODY-SENTINEL" not in description
        assert tool.spec.name == "load_skill"
        assert tool.spec.input_schema["required"] == ["name"]

    def test_loading_returns_the_instructions_files_and_version(self, skills_root):
        write_skill(skills_root, files={"scripts/grain.py": "print('grain')\n"})
        registry = SkillRegistry(skills_root)
        expected = registry.skills()[0]
        tool = LoadSkillTool(registry)

        outcome = tool.run({"name": "granular-synthesis"})

        assert outcome.ok is True
        assert BODY_SENTINEL in outcome.content
        assert "scripts/grain.py" in outcome.content
        assert outcome.data["loaded_skill"] == {
            "name": "granular-synthesis",
            "version": expected.version,
            "digest": expected.digest,
        }
        assert "granular-synthesis" in outcome.summary

    def test_an_unknown_name_lists_what_is_installed(self, skills_root):
        write_skill(skills_root)
        tool = LoadSkillTool(SkillRegistry(skills_root))

        outcome = tool.run({"name": "granular-synth"})

        assert outcome.ok is False
        assert "granular-synthesis" in outcome.content

    def test_a_missing_name_is_rejected_with_recovery_instructions(self, skills_root):
        write_skill(skills_root)
        tool = LoadSkillTool(SkillRegistry(skills_root))

        outcome = tool.run({})

        assert outcome.ok is False
        assert "needs the 'name'" in outcome.content
        assert "granular-synthesis" in outcome.content

    def test_an_empty_registry_says_so_and_suggests_not_retrying(self, skills_root):
        tool = LoadSkillTool(SkillRegistry(skills_root))

        assert "none installed" in tool.spec.description
        outcome = tool.run({"name": "anything"})
        assert outcome.ok is False
        assert "No skills are installed" in outcome.content
        assert "do not retry" in outcome.content

    def test_very_long_instructions_are_truncated(self, skills_root):
        write_skill(skills_root, body="x" * (MAX_INSTRUCTIONS_CHARS + 5_000))
        tool = LoadSkillTool(SkillRegistry(skills_root))

        outcome = tool.run({"name": "granular-synthesis"})

        assert outcome.data["truncated"] is True
        assert "Instructions truncated" in outcome.content


# --- Hot reload --------------------------------------------------------------


class TestHotReload:
    def test_a_skill_added_after_construction_appears_after_refresh(self, skills_root):
        tool = LoadSkillTool(SkillRegistry(skills_root))
        assert "granular-synthesis" not in tool.spec.description

        write_skill(skills_root)
        tool.refresh()

        assert "granular-synthesis" in tool.spec.description
        assert tool.run({"name": "granular-synthesis"}).ok is True

    def test_an_edited_skill_is_served_without_a_new_registry(self, skills_root):
        write_skill(skills_root)
        tool = LoadSkillTool(SkillRegistry(skills_root))
        first = tool.run({"name": "granular-synthesis"})

        write_skill(skills_root, body=GRAIN_BODY + "\n\nNow with pitch scatter.")
        second = tool.run({"name": "granular-synthesis"})

        assert "Now with pitch scatter" in second.content
        assert second.data["loaded_skill"]["version"] != first.data["loaded_skill"]["version"]

    def test_a_deleted_skill_stops_loading(self, skills_root):
        directory = write_skill(skills_root)
        tool = LoadSkillTool(SkillRegistry(skills_root))
        assert tool.run({"name": "granular-synthesis"}).ok is True

        for path in sorted(directory.rglob("*"), reverse=True):
            path.unlink()
        directory.rmdir()

        outcome = tool.run({"name": "granular-synthesis"})
        assert outcome.ok is False
        assert "No skills are installed" in outcome.content

    def test_a_failing_refresh_does_not_break_the_registry(self, skills_root):
        class BrokenTool:
            spec = StubTool.spec

            def run(self, arguments):  # pragma: no cover - never called
                raise AssertionError

            def refresh(self):
                raise RuntimeError("watch failed")

        write_skill(skills_root)
        registry = ToolRegistry([BrokenTool(), LoadSkillTool(SkillRegistry(skills_root))])

        registry.refresh()  # must not raise

        assert any(spec["name"] == "load_skill" for spec in registry.specs())


def _load_skill_registry(skills_root: Path, *tools: Any) -> ToolRegistry:
    return ToolRegistry([*tools, LoadSkillTool(SkillRegistry(skills_root))])


class TestLoopRefresh:
    """The loop must re-read the registry at the start of every turn."""

    def test_a_skill_added_mid_run_is_advertised_and_loadable_next_turn(self, skills_root):
        def first_turn(request: ModelRequest) -> dict[str, Any]:
            assert "granular-synthesis" not in _load_spec(request)["description"]
            write_skill(skills_root)  # appears while the first turn is streaming
            return tool_turn(name="search_docs", args={"query": "osc~"})

        def second_turn(request: ModelRequest) -> dict[str, Any]:
            spec = _load_spec(request)
            assert "granular-synthesis" in spec["description"]
            assert BODY_SENTINEL not in spec["description"]
            return tool_turn(name="load_skill", args={"name": "granular-synthesis"})

        client = StubClient([first_turn, second_turn, answer("Loaded the skill.")])
        loop = AgentLoop(
            client,
            _load_skill_registry(skills_root, StubTool()),
            AgentConfig(max_turns=4),
        )

        events = asyncio.run(_drain(loop))

        results = [event for event in events if isinstance(event, ToolResult)]
        assert [result.name for result in results] == ["search_docs", "load_skill"]
        assert results[1].ok is True
        assert "granular-synthesis" in loop.state.loaded_skills
        assert loop.state.loaded_skills["granular-synthesis"] == results[1].data[
            "loaded_skill"
        ]["version"]

    def test_an_edited_skill_is_reloaded_with_its_new_version_in_the_same_run(self, skills_root):
        write_skill(skills_root)

        def second_turn(request: ModelRequest) -> dict[str, Any]:
            write_skill(skills_root, body=GRAIN_BODY + "\n\nEdited between turns.")
            return tool_turn(name="load_skill", args={"name": "granular-synthesis"})

        client = StubClient([
            tool_turn(name="load_skill", args={"name": "granular-synthesis"}, call_id="c1"),
            second_turn,
            answer("Done."),
        ])
        loop = AgentLoop(
            client,
            _load_skill_registry(skills_root),
            AgentConfig(max_turns=4),
        )

        events = asyncio.run(_drain(loop))

        results = [event for event in events if isinstance(event, ToolResult)]
        first_version = results[0].data["loaded_skill"]["version"]
        assert "Edited between turns" in results[1].content
        assert results[1].data["loaded_skill"]["version"] != first_version
        assert loop.state.loaded_skills["granular-synthesis"] == results[1].data[
            "loaded_skill"
        ]["version"]


def _load_spec(request: ModelRequest) -> dict[str, Any]:
    return next(tool for tool in request.tools if tool["name"] == "load_skill")


async def _drain(loop: AgentLoop) -> list[Any]:
    return [event async for event in loop.stream("How do I make grain clouds?")]


# --- Run recording -----------------------------------------------------------


IN_SCOPE = {
    "rewritten_query": "how do I make grain clouds",
    "query_type": "conceptual",
    "object_names": [],
    "scope": "in_scope",
    "off_topic_part": "",
    "pd_query": "how do I make grain clouds",
}


class TestRunRecordsSkillVersions:
    def test_done_event_and_run_log_record_the_loaded_skill_version(self, skills_root, tmp_path):
        write_skill(skills_root)
        expected = SkillRegistry(skills_root).skills()[0]
        store = RunStore(f"sqlite:///{tmp_path / 'runs.db'}")
        client = StubClient([
            tool_turn(name="load_skill", args={"name": "granular-synthesis"}),
            answer("Grain clouds use windowed grains."),
        ])
        tools = _load_skill_registry(skills_root)

        async def go():
            from unittest.mock import patch

            with patch.object(chat, "classify_query", lambda q, h: IN_SCOPE):
                return [
                    event
                    async for event in chat.stream_chat(
                        "How do I make grain clouds?",
                        client=client,
                        tools=tools,
                        run_store=store,
                        run_id="run-1",
                        session_id="session-1",
                    )
                ]

        events = asyncio.run(go())

        done = next(event for event in events if isinstance(event, Done))
        assert done.skills == ({"name": "granular-synthesis", "version": expected.version},)
        assert done.to_dict()["skills"] == [
            {"name": "granular-synthesis", "version": expected.version}
        ]

        # Persisted twice over: on the tool result and on the terminal event.
        stored = store.reconstruct_state("run-1")
        assert stored["skills"] == [
            {"name": "granular-synthesis", "version": expected.version}
        ]
        run = store.get_run("run-1")
        done_payload = next(
            event["payload"] for event in run["events"] if event["event_type"] == "done"
        )
        assert done_payload["skills"] == [
            {"name": "granular-synthesis", "version": expected.version}
        ]

    def test_a_run_that_loaded_no_skill_records_none(self, skills_root):
        write_skill(skills_root)
        client = StubClient([answer("No skill needed.")])
        loop = AgentLoop(
            client,
            _load_skill_registry(skills_root, StubTool()),
            AgentConfig(max_turns=3),
        )
        asyncio.run(_drain(loop))
        assert loop.state.loaded_skills == {}

    def test_a_durable_run_records_the_loaded_skill_version(self, skills_root, tmp_path):
        from agent.durable import (
            DurableRuntime,
            destroy_dbos,
            launch_dbos,
            reset_runtime,
            set_runtime,
            start_durable_run,
        )

        write_skill(skills_root)
        expected = SkillRegistry(skills_root).skills()[0]
        destroy_dbos()
        reset_runtime()
        run_store = RunStore(f"sqlite:///{tmp_path / 'runs-durable.db'}")
        launch_dbos(f"sqlite:///{tmp_path / 'dbos.db'}", app_name="pd-skills-test")
        client = StubClient([
            tool_turn(name="load_skill", args={"name": "granular-synthesis"}),
            answer("Grain clouds use windowed grains."),
        ])
        set_runtime(
            DurableRuntime(
                client_factory=lambda: client,
                # Rebuilt for every turn, so this is also the durable hot-reload path.
                tools_factory=lambda ws, iface, pol, approved: _load_skill_registry(
                    skills_root
                ),
                config_factory=lambda iface, pol, approved: AgentConfig(max_turns=4),
                run_store=run_store,
                classify_fn=lambda q, h: IN_SCOPE,
            )
        )

        async def go():
            _rid, _sid, handle = await start_durable_run(
                "How do I make grain clouds?",
                run_id="run-skill-1",
                session_id="sess-skill-1",
                interface="web",
                pause_for_approval=False,
            )
            return await handle.get_result()

        try:
            result = asyncio.run(go())
        finally:
            destroy_dbos()
            reset_runtime()

        record = [{"name": "granular-synthesis", "version": expected.version}]
        assert result["skills"] == record
        stored = run_store.get_run("run-skill-1")
        done_payload = next(
            event["payload"] for event in stored["events"] if event["event_type"] == "done"
        )
        assert done_payload["skills"] == record
