"""Tests for aggregator."""
from __future__ import annotations
from eval.aggregator import VideoPairResult, aggregate_results, decide_merge


def make_review(a_score: int, b_score: int, preference: str) -> dict:
    return {
        "asr_issues": ["test"],
        "score_a": {
            "笔记A": {"分": a_score, "证据": ["e1"]},
            "笔记B": {"分": a_score, "证据": ["e2"]},
        },
        "score_b": {
            "笔记A": {"分": b_score, "证据": [{"链接": "[[X]]", "判断": "对", "理由": "ok"}]},
            "笔记B": {"分": b_score, "证据": [{"链接": "[[X]]", "判断": "对", "理由": "ok"}]},
        },
        "score_c": {
            "笔记A": {"分": 3, "证据": ["ok"]},
            "笔记B": {"分": 3, "证据": ["ok"]},
        },
        "score_d": {"偏好": preference, "理由": "test"},
        "self_doubt": {"最可能出偏误的维度": "C", "原因": "test"},
    }


class TestComputeDiff:
    def test_emb_wins_d_when_a_is_emb(self):
        r = VideoPairResult("v.mp4", True, True, make_review(4, 4, "笔记A"))
        diffs = r.compute_diffs()
        assert diffs["D"] == "emb_win"
        assert diffs["A"] == 0
        assert diffs["B"] == 0

    def test_emb_wins_d_when_b_is_emb(self):
        r = VideoPairResult("v.mp4", True, False, make_review(3, 3, "笔记B"))
        diffs = r.compute_diffs()
        assert diffs["D"] == "emb_win"

    def test_tie(self):
        r = VideoPairResult("v.mp4", False, True, make_review(3, 3, "持平"))
        diffs = r.compute_diffs()
        assert diffs["D"] == "tie"

    def test_main_wins(self):
        r = VideoPairResult("v.mp4", False, True, make_review(3, 3, "笔记B"))
        diffs = r.compute_diffs()
        assert diffs["D"] == "main_win"


class TestAggregateResults:
    def test_empty(self):
        s = aggregate_results([])
        assert s["total_pairs"] == 0

    def test_emb_sweeps(self):
        pairs = [VideoPairResult(f"v{i}.mp4", i < 5, True, make_review(5, 5, "笔记A")) for i in range(10)]
        s = aggregate_results(pairs)
        assert s["total_pairs"] == 10
        assert s["dim_D_emb_win_pct"] == 1.0
        assert s["dim_D_emb_win_pct_new"] == 1.0
        assert s["dim_D_emb_win_pct_existing"] == 1.0


class TestDecideMerge:
    def _summary(self, d_win, d_existing, d_new, n=20):
        return {
            "total_pairs": n,
            "dim_A_avg_diff": 0.5, "dim_B_avg_diff": 0.3, "dim_C_avg_diff": 0.2,
            "dim_D_emb_win_pct": d_win,
            "dim_D_emb_win_pct_existing": d_existing,
            "dim_D_emb_win_pct_new": d_new,
            "self_doubt_flags": {},
        }

    def test_merge_when_all_met(self):
        assert decide_merge(self._summary(0.65, 0.70, 0.50)) == "merge"

    def test_no_merge_when_low(self):
        assert decide_merge(self._summary(0.30, 0.30, 0.30)) == "no_merge"

    def test_rag_unclear(self):
        assert decide_merge(self._summary(0.65, 0.40, 0.60)) == "rag_gain_unclear"

    def test_insufficient_data(self):
        assert decide_merge({"total_pairs": 3}) == "insufficient_data"
