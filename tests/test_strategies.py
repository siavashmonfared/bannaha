from bannaha.core import events as ev
from bannaha.features.dropout import Dropout
from bannaha.features.peer_updates import PeerUpdates


def kinds(session, kind):
    return [e for e in session.events if e.kind == kind]


def test_single(make_session):
    s = make_session("single", "mock@local:42")
    o = s.run()
    assert (o.status, o.answer) == ("committed", "42")


def test_vote_plurality_and_agreement(make_session):
    s = make_session("vote", "mock@local:42*3,mock@local:41*2")
    o = s.run()
    assert o.answer == "42" and o.detail["agreement"] == 0.6
    assert not kinds(s, ev.CLAIM_POSTED)


def test_blackboard_commits_in_round_one_with_quorum(make_session):
    o = make_session("blackboard", "mock@local:42*3,mock@local:41*2").run()
    assert (o.answer, o.detail["decided_by"], o.detail["round"]) == ("42", "quorum", 1)


def test_blackboard_revises_then_commits(make_session):
    o = make_session("blackboard", "mock@local:42*2,mock@local:41*2,mock@local:40").run()
    assert (o.answer, o.detail["round"]) == ("42", 2)
    assert [r["decision"] for r in o.detail["rounds"]] == ["revise", "commit"]


def test_blackboard_ballot_then_fallback_or_fail(make_session):
    fleet = "mock@local:42!*2,mock@local:41!*2,mock@local:40!"
    o = make_session("blackboard", fleet, rounds=2).run()
    assert o.detail["decided_by"] == "ballot_borda_fallback"
    assert o.detail["rounds"][-1]["phase"] == "ballot"
    o2 = make_session("blackboard", fleet, rounds=2, fallback="fail").run()
    assert o2.status == "failed"


def test_blackboard_survives_half_the_fleet_dropping(make_session):
    s = make_session("blackboard", "mock@local:42*3,mock@local:41*3", [Dropout(0.5, after_calls=1, seed=2)])
    o = s.run()
    assert o.status == "committed"
    assert sum(not a.alive for a in s.agents) == 3


def test_peer_updates_attach_to_tool_results(make_session):
    from bannaha.core.tools import ToolContext, TurnState

    s = make_session("blackboard", "mock@local:42*2", [PeerUpdates()])
    s.round = 1
    a0, a1 = s.agents
    read, post = s.toolbox.get(["read_board", "post_claim"])
    ctx0 = ToolContext(s, a0, TurnState())
    ctx1 = ToolContext(s, a1, TurnState())
    s.invoke_tool(ctx0, read, {})
    s.invoke_tool(ctx1, post, {"value": "41", "source": "https://y.org"})
    out = s.invoke_tool(ctx0, post, {"value": "42", "source": "https://x.org"})
    items = out["peer_updates"]["items"]
    assert [(i["kind"], i["agent"], i["value"]) for i in items] == [("claim", "a1", "41")]
    again = s.invoke_tool(ctx0, read, {})
    assert "peer_updates" not in again


def test_stale_read_rejected_once(make_session):
    from bannaha.core.tools import ToolContext, TurnState

    s = make_session("blackboard", "mock@local:42*2")
    s.round = 1
    a0, a1 = s.agents
    propose = s.toolbox.get(["propose_answer"])[0]
    s.board.add_claim(agent=a1.id, round=1, value="41", source="https://y.org")
    ctx = ToolContext(s, a0, TurnState(terminals=("propose_answer",)))
    first = s.invoke_tool(ctx, propose, {"value": "42", "rationale": "r", "seen_version": 0})
    assert first["error"] == "stale_read"
    second = s.invoke_tool(ctx, propose, {"value": "42", "rationale": "r", "seen_version": 0})
    assert second["ok"] and ctx.turn.done


def test_turn_limit_and_budget_note(make_session):
    from bannaha.core.errors import TurnLimitReached
    from bannaha.core.tools import ToolContext, TurnState

    s = make_session("blackboard", "mock@local:42*2")
    read = s.toolbox.get(["read_board"])[0]
    ctx = ToolContext(s, s.agents[0], TurnState(terminals=("propose_answer",), max_tool_calls=3))
    s.invoke_tool(ctx, read, {})
    note = s.invoke_tool(ctx, read, {})
    assert "budget_note" in note
    s.invoke_tool(ctx, read, {})
    try:
        s.invoke_tool(ctx, read, {})
    except TurnLimitReached:
        pass
    else:
        raise AssertionError("expected TurnLimitReached")


def test_cancel_stops_the_session(make_session):
    s = make_session("blackboard", "mock@local:42*2")
    s.cancel("test")
    assert s.run().status == "stopped"


def test_agent_cost_limit_ends_that_agents_work(make_session):
    from bannaha.core.limits import Limits

    s = make_session("blackboard", "mock@local:42!,mock@local:41!,mock@local:40!", rounds=3)
    s.limits = Limits(max_agent_cost_usd=0.0001)  # mock: one tool call is $0.00092
    o = s.run()
    hits = kinds(s, ev.BUDGET_REACHED)
    assert {e.agent for e in hits} == {"a0", "a1", "a2"} and all(e.data["scope"] == "agent" for e in hits)
    statuses = [e.data["status"] for e in kinds(s, ev.TURN_FINISHED)]
    assert "budget" in statuses
    assert all(a.over_budget for a in s.agents)
    assert o.status in ("committed", "no_answer")


def test_run_cost_limit_stops_the_run(make_session):
    from bannaha.core.limits import Limits

    s = make_session("blackboard", "mock@local:42!,mock@local:41!,mock@local:40!", rounds=5)
    s.limits = Limits(max_cost_usd=0.003)
    o = s.run()
    hit = [e for e in kinds(s, ev.BUDGET_REACHED) if e.data["scope"] == "run"]
    assert len(hit) == 1 and "cost limit" in s.stop_reason
    # no agent starts work after the limit is hit (blackboard then falls back to its best answer so far)
    after = [e for e in s.events[s.events.index(hit[0]):] if e.kind == ev.TURN_STARTED]
    assert not after and o.status in ("committed", "stopped")
    finished = kinds(s, ev.SESSION_FINISHED)[0].data
    assert finished["total_cost_usd"] >= 0.003 and finished["agents"][0]["cost_usd"] > 0


def test_usage_events_report_running_cost(make_session):
    s = make_session("single", "mock@local:42")
    s.run()
    usage = kinds(s, ev.USAGE)
    assert usage and abs(usage[-1].data["cost_usd"] - 0.00092) < 1e-12 and usage[-1].data["input_tokens"] == 600


def test_deliberative_vote_commits_on_a_first_choice_quorum(make_session):
    s = make_session("deliberative_vote", "mock@local:42*3,mock@local:41*2")
    o = s.run()
    assert (o.status, o.answer, o.detail["decided_by"]) == ("committed", "42", "ballot_quorum")
    votes = kinds(s, ev.VOTE_CAST)
    assert len(votes) == 5 and all(v.data["reason"] for v in votes)
    assert o.detail["rounds"][0]["reasons"]


def test_deliberative_vote_carries_the_decision_into_the_next_round(make_session):
    s = make_session("deliberative_vote", "mock@local:42*2,mock@local:41*2,mock@local:40")
    o = s.run()
    assert [r["decision"] for r in o.detail["rounds"]] == ["continue", "commit"]
    revise = [e for e in kinds(s, ev.PROMPT) if e.data["part"] == "prompt" and "propose again" in e.data["text"]]
    assert revise and "Last round the group chose" in revise[0].data["text"]


def test_deliberative_vote_last_round_fallback(make_session):
    fleet = "mock@local:42!*2,mock@local:41!*2,mock@local:40!"
    o = make_session("deliberative_vote", fleet, rounds=2).run()
    assert o.status == "committed" and o.detail["decided_by"] == "ballot_borda_last_round"
    assert make_session("deliberative_vote", fleet, rounds=2, fallback="fail").run().status == "failed"


def test_deliberative_vote_commits_without_a_ballot_when_all_agree(make_session):
    s = make_session("deliberative_vote", "mock@local:42*3")
    o = s.run()
    assert o.detail["decided_by"] == "proposals_agree" and not kinds(s, ev.VOTE_CAST)


def test_prompts_are_logged_once_and_referenced_by_hash(make_session):
    s = make_session("blackboard", "mock@local:42*3")
    s.run()
    starts = kinds(s, ev.TURN_STARTED)
    prompts = {e.data["sha"]: e.data for e in kinds(s, ev.PROMPT)}
    assert starts and all({"system_sha", "prompt_sha", "tools_sha"} <= set(e.data) for e in starts)
    for e in starts:
        assert prompts[e.data["prompt_sha"]]["part"] == "prompt"
        assert "What is the answer?" in prompts[e.data["prompt_sha"]]["text"]
    assert len(prompts) == len(set(prompts))  # each distinct text once


def test_usage_events_are_marked_cumulative(make_session):
    s = make_session("single", "mock@local:42")
    s.run()
    assert all(e.data["cumulative"] is True for e in kinds(s, ev.USAGE))


def test_turn_that_stops_after_a_failed_tool_call_is_tool_errors():
    from bannaha.core.fleet import parse_fleet
    from bannaha.core.registry import register_harness, strategies
    from bannaha.core.session import Session
    from bannaha.harnesses.base import Harness, HarnessReply
    from bannaha.tools.toolbox import ToolOptions

    @register_harness
    class BadVoter(Harness):
        name = "bad_voter_test"
        backends = {"local": []}

        def run_turn(self, req):
            tools = {t.name: t for t in req.tools}
            req.call_tool(tools["final_answer"], {"answer": 42, "confidence": "high?"})
            return HarnessReply()

    s = Session(question="q", fleet=parse_fleet("bad_voter_test@local:x"), strategy=strategies()["single"](),
                tool_options=ToolOptions(web=False, python=False))
    s.toolbox._tools["final_answer"] = type(s.toolbox._tools["final_answer"])(
        "final_answer", "d", s.toolbox._tools["final_answer"].parameters,
        lambda ctx, a: {"ok": False, "error": "bad args"}, terminal=True)
    events = []
    s.bus.subscribe(events.append)
    s.run()
    done = [e for e in events if e.kind == ev.TURN_FINISHED]
    assert done[0].data["status"] == "tool_errors" and done[0].data["tool_errors"] == 1
