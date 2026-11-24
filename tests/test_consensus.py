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


def test_borda_ties_follow_order_by_default_or_a_seeded_shuffle():
    import random

    from bannaha.consensus.ballots import tied_top

    ballots = {"a": ["k1", "k2"], "b": ["k2", "k1"]}
    assert borda(ballots, ["k1", "k2"])[0][0] == "k1"  # documented position bias
    assert borda(ballots, ["k2", "k1"])[0][0] == "k2"
    assert tied_top(borda(ballots, ["k1", "k2"])) == ["k1", "k2"]
    winners = {borda(ballots, ["k1", "k2"], rng=random.Random(s))[0][0] for s in range(20)}
    assert winners == {"k1", "k2"}
    assert borda(ballots, ["k1", "k2"], rng=random.Random(5)) == borda(ballots, ["k1", "k2"], rng=random.Random(5))


def test_first_choice_tie_goes_to_borda_not_arrival_order():
    from bannaha.consensus.ballots import leader

    # k2 and k1 tie on first choices; k1 has the higher Borda score. Arrival order lists k2 first.
    ballots = {"x": ["k2", "k1", "k3"], "y": ["k1", "k3", "k2"], "z": ["k3", "k1", "k2"], "w": ["k2", "k3", "k1"],
               "v": ["k1", "k2", "k3"]}
    ballots.pop("z")
    firsts = first_choices(ballots)
    scores = borda(ballots, ["k1", "k2", "k3"])
    top, votes, tied = leader(firsts, scores)
    assert (top, votes, sorted(tied)) == ("k1", 2, ["k1", "k2"])
