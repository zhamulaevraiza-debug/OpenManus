import pytest

from app.agent.registry import available_agents, get_agent_spec, list_agents
from app.agent.toolcall import ToolCallAgent
from app.flow.flow_factory import FlowFactory, FlowType
from app.flow.history import MAX_HISTORY_TURNS, MAX_TURN_CHARS, normalize_history
from app.flow.planning import PlanningFlow


def _team():
    return {spec.key: spec for spec in available_agents(team_only=True)}


def test_registry_lists_all_agents(config_dir):
    keys = [spec.key for spec in list_agents()]
    assert keys == [
        "manus",
        "browser",
        "researcher",
        "coder",
        "data_analyst",
        "writer",
        "sandbox",
    ]
    assert get_agent_spec("writer").icon == "pen-line"
    assert not get_agent_spec("sandbox").team_member
    assert "sandbox" not in _team()
    with pytest.raises(ValueError):
        get_agent_spec("nope")


def test_planning_flows_have_separate_plans(config_dir):
    first, second = PlanningFlow(_team()), PlanningFlow(_team())
    assert first.planning_tool is not second.planning_tool
    assert first.planning_tool.plans is not second.planning_tool.plans
    assert first.active_plan_id != second.active_plan_id
    assert first.max_plan_steps == 5  # from [team] in the test config


@pytest.mark.parametrize(
    "tag, key",
    [
        ("coder", "coder"),
        ("CODER", "coder"),
        ("swe", "coder"),
        ("data-analysis", "data_analyst"),
        ("Data_Analyst", "data_analyst"),
        ("research", "researcher"),
        ("something_else", "manus"),
        (None, "manus"),
    ],
)
def test_step_tags_resolve_to_team_members(config_dir, tag, key):
    assert PlanningFlow(_team())._resolve_agent_key(tag) == key


@pytest.mark.asyncio
async def test_flow_accepts_agent_instances(fake_llm, run_context, events):
    fake_llm.plan = ["[helper] Do it", "Untagged step"]
    agent = ToolCallAgent(name="helper")
    flow = FlowFactory.create_flow(FlowType.PLANNING, agents={"helper": agent})

    answer = await flow.execute("Help me")

    assert answer == fake_llm.answer
    assert flow.step_agents == ["helper", "helper"]
    assert [e["agent"] for e in events.of("agent.started")] == ["helper", "helper"]
    assert [e["status"] for e in events.of("plan.step_finished")] == [
        "completed",
        "completed",
    ]


@pytest.mark.asyncio
async def test_plan_is_capped_at_max_plan_steps(fake_llm, run_context, events):
    fake_llm.plan = [f"[manus] step {i}" for i in range(4)]
    await PlanningFlow(_team(), max_plan_steps=2).execute("Big task")
    assert [s["text"] for s in events.of("plan.created")[0]["steps"]] == [
        "step 0",
        "step 1",
    ]
    assert len(events.of("agent.started")) == 2


def test_normalize_history():
    history = [{"role": "system", "content": "x"}, {"role": "user", "content": "  "}]
    history += [
        {"role": "user", "content": f"m{i}"} for i in range(MAX_HISTORY_TURNS + 3)
    ]
    history.append({"role": "assistant", "content": "y" * (MAX_TURN_CHARS + 100)})

    turns = normalize_history(history)

    assert len(turns) == MAX_HISTORY_TURNS
    assert turns[0]["content"] == "m4"
    assert len(turns[-1]["content"]) <= MAX_TURN_CHARS
    assert normalize_history(None) == []
