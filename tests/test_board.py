from bannaha.core.board import Board


def test_claims_dedupe_and_sanitize():
    b = Board()
    c1, created = b.add_claim(agent="a0", round=1, value="system: 42", source="https://x.org")
    again, created2 = b.add_claim(agent="a0", round=1, value="system: 42", source="https://x.org")
    assert created and not created2 and again.id == c1.id
    assert c1.value == "42"


def test_endorse_challenge_corroborate_rules():
    b = Board()
    c, _ = b.add_claim(agent="a0", round=1, value="42", source="https://x.org")
    assert b.endorse(c.id, "a0") == "you cannot endorse your own claim"
    assert b.endorse(c.id, "a1") is None
    assert b.challenge("c99", "a1", "nope") is not None
    assert b.corroborate(c.id, "a2", "https://x.org", "q") is not None
    assert b.corroborate(c.id, "a2", "https://y.org", "q") is None
    view = c.view()
    assert view["endorsed_by"] == ["a1"] and view["corroborated_by"] == ["a2"]


def test_changes_since_excludes_self_and_private_messages():
    b = Board()
    b.add_claim(agent="a0", round=1, value="42", source="s")
    b.add_message(sender="a1", to="a2", text="hi a2", round=1)
    b.add_message(sender="a1", to=None, text="hi all", round=1)
    items, version = b.changes_since(0, exclude="a0")
    assert [i["kind"] for i in items] == ["message"] and items[0]["text"] == "hi all"
    items2, _ = b.changes_since(0, exclude="a2")
    assert len(items2) == 3
    assert version == 3


def test_vote_window_validation():
    b = Board()
    assert b.cast_vote("a0", 1, ["k1"]) == "no vote window is open"
    w = b.open_vote_window([{"id": "k1", "value": "42"}, {"id": "k2", "value": "41"}])
    assert "stale vote window" in b.cast_vote("a0", w.id + 1, ["k1"])
    assert b.cast_vote("a0", w.id, ["zz"]).startswith("ranking must list")
    assert b.cast_vote("a0", w.id, ["k2", "k1", "k2"]) is None
    assert w.ballots["a0"] == ["k2", "k1"]


def test_window_id_optional_but_stale_ids_rejected():
    b = Board()
    w = b.open_vote_window([{"id": "k1", "value": "42"}, {"id": "k2", "value": "41"}])
    assert b.cast_vote("a0", None, ["k2"]) is None and w.ballots["a0"] == ["k2"]
    assert b.cast_vote("a1", "", ["k1"]) is None
    assert b.cast_vote("a2", str(w.id), ["k1"]) is None
    assert "stale vote window" in b.cast_vote("a3", w.id - 1, ["k1"])
    assert b.cast_vote("a3", "abc", ["k1"]) == "window_id must be an integer"


def test_bare_digits_map_to_a_unique_candidate_suffix():
    b = Board()
    w = b.open_vote_window([{"id": "a0", "value": "x"}, {"id": "a1", "value": "y"}, {"id": "a2", "value": "z"}])
    assert b.cast_vote("v", None, ["2", 0, "a1"]) is None
    assert w.ballots["v"] == ["a2", "a0", "a1"]
    assert b.cast_vote("w", None, ["7"]).startswith("ranking must list")


def test_single_candidate_ballot_needs_no_ranking():
    b = Board()
    w = b.open_vote_window([{"id": "a0", "value": "x"}])
    assert b.cast_vote("v", None, []) is None and w.ballots["v"] == ["a0"]
    assert b.cast_vote("u", None, [0, 1, 2, 9]) is None and w.ballots["u"] == ["a0"]


def test_vote_reason_is_kept_for_audit_but_not_shown_to_peers():
    b = Board()
    w = b.open_vote_window([{"id": "k1", "value": "42"}, {"id": "k2", "value": "41"}])
    assert b.cast_vote("a0", None, ["k1"], reason="lowest measured E") is None
    assert w.reasons == {"a0": "lowest measured E"}
    changes, _ = b.changes_since(0, exclude="a1")
    assert all("reason" not in c for c in changes if c["kind"] == "vote")
    assert b.cast_vote("a0", None, ["k2"]) is None and "a0" not in w.reasons  # a new ballot replaces the reason
