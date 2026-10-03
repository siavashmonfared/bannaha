from bannaha.consensus.ballots import borda, first_choices
from bannaha.consensus.clustering import cluster_answers, parse_answer
from bannaha.consensus.quorum import Quorum


def test_numeric_parsing_scales_and_currency():
    assert parse_answer("$2.9bn").numeric == 2.9e9
    assert parse_answer("2,900", "USD millions").numeric == 2.9e9
    assert parse_answer("2,900", "USD millions").unit == "usd"
    assert parse_answer("12.5%").unit == "%"
    assert parse_answer("Saudi Arabia").numeric is None


def test_numeric_answers_merge_within_tolerance_and_same_unit():
    clusters = cluster_answers([("a", "$2.9bn", ""), ("b", "2,900", "USD millions"), ("c", "2.901 billion", "USD"),
                                ("d", "3.4 billion", "USD"), ("e", "2.9", "%")])
    assert clusters[0].members == ["a", "b", "c"]
    assert {tuple(c.members) for c in clusters[1:]} == {("d",), ("e",)}


def test_text_answers_merge_on_normalization():
    clusters = cluster_answers([("a", "The Saudi Arabia", ""), ("b", "saudi arabia.", ""), ("c", "Iraq", "")])
    assert clusters[0].support == 2 and clusters[1].members == ["c"]


def test_quorum_needed_and_reached():
    q = Quorum(0.5, 2)
    assert q.needed(5) == 3 and q.reached(3, 5) and not q.reached(2, 5)
    assert q.needed(1) == 1 and q.reached(1, 1)
    assert Quorum(0.5, 2).needed(4) == 2


def test_borda_and_first_choices():
    ballots = {"a": ["k1", "k2", "k3"], "b": ["k2", "k1"], "c": ["k1", "k3"]}
    assert first_choices(ballots)["k1"] == 2
    assert borda(ballots, ["k1", "k2", "k3"])[0] == ("k1", 8)
