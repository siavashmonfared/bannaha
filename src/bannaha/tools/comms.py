"""Communication tools: how agents coordinate through the shared board."""
from __future__ import annotations

from ..core import events as ev
from ..core.board import PEER_DATA_NOTE
from ..core.tools import JsonDict, Tool, ToolContext, schema


def _round(ctx: ToolContext) -> int:
    return ctx.session.round or 1


def read_board_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        snap = ctx.session.board.snapshot(ctx.agent.id, round=_round(ctx))
        ctx.agent.last_seen_version = snap["board_version"]
        return {"ok": True, **snap}

    return Tool(
        name="read_board",
        description=("Read the shared board: every claim with its endorsements and challenges, recent proposals, "
                     "messages addressed to you or to everyone, and any open vote."),
        parameters=schema({}),
        handler=handler,
    )


def post_claim_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        if not str(args.get("value") or "").strip():
            return {"ok": False, "error": "value is required"}
        if not str(args.get("source") or "").strip():
            return {"ok": False, "error": "source is required: cite where this comes from"}
        claim, created = ctx.session.board.add_claim(
            agent=ctx.agent.id, round=_round(ctx), value=args.get("value"), unit=args.get("unit", ""),
            quote=args.get("quote", ""), source=args.get("source"), confidence=args.get("confidence", 0.5),
            caveats=args.get("caveats", ""),
        )
        if created:
            ctx.session.emit(ev.CLAIM_POSTED, agent=ctx.agent.id, claim_id=claim.id, value=claim.value,
                             unit=claim.unit, source=claim.source)
        return {"ok": True, "claim_id": claim.id, "duplicate": not created,
                "board_version": ctx.session.board.version}

    return Tool(
        name="post_claim",
        description=("Post one fact you established to the shared board, with the evidence for it. "
                     "Post facts, not guesses; one fact per claim."),
        parameters=schema({
            "value": {"type": "string", "description": "The fact itself, as short as possible."},
            "unit": {"type": "string", "description": "Unit or currency, if numeric."},
            "quote": {"type": "string", "description": "Short verbatim quote from the source."},
            "source": {"type": "string", "description": "URL or citation."},
            "confidence": {"type": "number", "description": "0 to 1."},
            "caveats": {"type": "string", "description": "Anything that limits the claim (period, scope)."},
        }, ["value", "source"]),
        handler=handler,
    )


def endorse_claim_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        cid = str(args.get("claim_id") or "")
        err = ctx.session.board.endorse(cid, ctx.agent.id, args.get("note", ""))
        if err:
            return {"ok": False, "error": err}
        ctx.session.emit(ev.CLAIM_ENDORSED, agent=ctx.agent.id, claim_id=cid, note=str(args.get("note", ""))[:120])
        return {"ok": True}

    return Tool(
        name="endorse_claim",
        description="Endorse a peer's claim when your own evidence agrees with it.",
        parameters=schema({
            "claim_id": {"type": "string"},
            "note": {"type": "string", "description": "What evidence of yours agrees."},
        }, ["claim_id"]),
        handler=handler,
    )


def challenge_claim_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        cid = str(args.get("claim_id") or "")
        if not str(args.get("reason") or "").strip():
            return {"ok": False, "error": "reason is required"}
        err = ctx.session.board.challenge(cid, ctx.agent.id, args.get("reason"), args.get("counter_value", ""))
        if err:
            return {"ok": False, "error": err}
        ctx.session.emit(ev.CLAIM_CHALLENGED, agent=ctx.agent.id, claim_id=cid,
                         reason=str(args.get("reason"))[:120], counter_value=str(args.get("counter_value", "")))
        return {"ok": True}

    return Tool(
        name="challenge_claim",
        description="Challenge a peer's claim when your evidence conflicts with it. Say why.",
        parameters=schema({
            "claim_id": {"type": "string"},
            "reason": {"type": "string", "description": "What is wrong and what your evidence says."},
            "counter_value": {"type": "string", "description": "The value you believe is right, if any."},
        }, ["claim_id", "reason"]),
        handler=handler,
    )


def corroborate_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        cid = str(args.get("claim_id") or "")
        err = ctx.session.board.corroborate(cid, ctx.agent.id, args.get("source", ""), args.get("quote", ""))
        if err:
            return {"ok": False, "error": err}
        ctx.session.emit(ev.CLAIM_CORROBORATED, agent=ctx.agent.id, claim_id=cid,
                         source=str(args.get("source", ""))[:160])
        return {"ok": True}

    return Tool(
        name="corroborate",
        description="Confirm a peer's claim from an independent source (a different URL than theirs).",
        parameters=schema({
            "claim_id": {"type": "string"},
            "source": {"type": "string", "description": "The independent source URL."},
            "quote": {"type": "string", "description": "Short verbatim quote that confirms the claim."},
        }, ["claim_id", "source"]),
        handler=handler,
    )


def send_message_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        text = str(args.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text is required"}
        to = str(args.get("to") or "").strip() or None
        everyone = [a.id for a in ctx.session.agents]
        if to and to not in everyone:
            return {"ok": False, "error": f"unknown agent {to!r}"}
        msg = ctx.session.board.add_message(sender=ctx.agent.id, to=to, text=text, round=_round(ctx))
        ctx.session.emit(ev.MESSAGE_SENT, agent=ctx.agent.id, to=msg.to or "all", text=msg.text[:160])
        return {"ok": True, "message_id": msg.id}

    return Tool(
        name="send_message",
        description=("Send a short message to one agent (by id) or to everyone you are allowed to reach. Use it to "
                     "coordinate who checks what."),
        parameters=schema({
            "text": {"type": "string"},
            "to": {"type": "string", "description": "Agent id, or omit for everyone."},
        }, ["text"]),
        handler=handler,
    )


def read_messages_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        board = ctx.session.board
        msgs = [m for m in board.messages if m.sender != ctx.agent.id and m.to in (None, ctx.agent.id)]
        return {"ok": True, "note": PEER_DATA_NOTE,
                "messages": [{"from": m.sender, "to": m.to or "all", "round": m.round, "text": m.text}
                             for m in msgs[-30:]]}

    return Tool(
        name="read_messages",
        description="Read the messages other agents sent to you or to everyone.",
        parameters=schema({}),
        handler=handler,
    )


def propose_answer_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        if not str(args.get("value") or "").strip():
            return {"ok": False, "error": "value is required"}
        board = ctx.session.board
        seen = args.get("seen_version")
        if isinstance(seen, (int, float)) and ctx.turn.stale_rejections == 0 \
                and board.others_changed_since(int(seen), ctx.agent.id):
            ctx.turn.stale_rejections += 1
            return {"ok": False, "error": "stale_read", "board_version": board.version,
                    "hint": "Peers posted new claims or proposals since you last read the board. "
                            "Call read_board, then propose again."}
        claim_ids = args.get("claim_ids") or []
        if isinstance(claim_ids, str):
            claim_ids = [claim_ids]
        prop = board.add_proposal(agent=ctx.agent.id, round=_round(ctx), value=args.get("value"),
                                  unit=args.get("unit", ""), rationale=args.get("rationale", ""),
                                  claim_ids=[str(c) for c in claim_ids])
        ctx.session.emit(ev.PROPOSAL_POSTED, agent=ctx.agent.id, proposal_id=prop.id, value=prop.value,
                         unit=prop.unit, claim_ids=prop.claim_ids)
        return {"ok": True, "proposal_id": prop.id}

    return Tool(
        name="propose_answer",
        description="Propose your answer to the question for this round. This ends your turn.",
        parameters=schema({
            "value": {"type": "string", "description": "The answer, as short as possible."},
            "unit": {"type": "string", "description": "Unit or currency, if numeric."},
            "rationale": {"type": "string", "description": "One or two sentences on the evidence."},
            "claim_ids": {"type": "array", "items": {"type": "string"},
                          "description": "Board claims that support this answer."},
            "seen_version": {"type": "integer",
                             "description": "board_version from your last read_board, to detect stale reads."},
        }, ["value", "rationale"]),
        handler=handler,
        terminal=True,
    )


def cast_vote_tool() -> Tool:
    def handler(ctx: ToolContext, args: JsonDict) -> JsonDict:
        ranking = args.get("ranking") or []
        if isinstance(ranking, str):
            ranking = [r.strip() for r in ranking.split(",")]
        err = ctx.session.board.cast_vote(ctx.agent.id, args.get("window_id"), list(ranking))
        if err:
            return {"ok": False, "error": err}
        w = ctx.session.board.vote_window
        ctx.session.emit(ev.VOTE_CAST, agent=ctx.agent.id, window_id=w.id if w else None,
                         ranking=w.ballots.get(ctx.agent.id) if w else ranking)
        return {"ok": True}

    return Tool(
        name="cast_vote",
        description="Rank the candidate answers in the open vote, best first. This ends your turn.",
        parameters=schema({
            "window_id": {"type": "integer"},
            "ranking": {"type": "array", "items": {"type": "string"}, "description": "Candidate ids, best first."},
        }, ["window_id", "ranking"]),
        handler=handler,
        terminal=True,
    )
