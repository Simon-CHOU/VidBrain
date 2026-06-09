"""
Aggregation and decision logic for Agent EVAL results.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("vidbrain.eval")


@dataclass
class VideoPairResult:
    """A single A/B comparison result for one video pair."""
    video_name: str
    is_new_domain: bool
    review_a_is_emb: bool
    review: dict

    def compute_diffs(self) -> dict:
        r = self.review
        if self.review_a_is_emb:
            emb_side = "笔记A"
            main_side = "笔记B"
        else:
            emb_side = "笔记B"
            main_side = "笔记A"

        a_diff = r["score_a"][emb_side]["分"] - r["score_a"][main_side]["分"]
        b_diff = r["score_b"][emb_side]["分"] - r["score_b"][main_side]["分"]
        c_diff = r["score_c"][emb_side]["分"] - r["score_c"][main_side]["分"]

        pref = r["score_d"]["偏好"]
        if pref == emb_side:
            d_result = "emb_win"
        elif pref == main_side:
            d_result = "main_win"
        else:
            d_result = "tie"

        return {
            "A": a_diff, "B": b_diff, "C": c_diff, "D": d_result,
            "video_name": self.video_name, "is_new_domain": self.is_new_domain,
        }


def aggregate_results(pairs: list[VideoPairResult]) -> dict:
    n = len(pairs)
    if n == 0:
        return {"total_pairs": 0, "conclusion": "insufficient_data"}

    diffs = [p.compute_diffs() for p in pairs]

    a_avg = sum(d["A"] for d in diffs) / n
    b_avg = sum(d["B"] for d in diffs) / n
    c_avg = sum(d["C"] for d in diffs) / n

    d_new = [d for d in diffs if d["is_new_domain"]]
    d_existing = [d for d in diffs if not d["is_new_domain"]]

    def _win_pct(d_list):
        if not d_list:
            return 0.5
        wins = sum(1 for d in d_list if d["D"] == "emb_win")
        return wins / len(d_list)

    d_all_pct = _win_pct(diffs)
    d_new_pct = _win_pct(d_new)
    d_existing_pct = _win_pct(d_existing)

    doubt_counts: dict[str, int] = {}
    for p in pairs:
        dim = p.review.get("self_doubt", {}).get("最可能出偏误的维度", "")
        if dim:
            doubt_counts[dim] = doubt_counts.get(dim, 0) + 1

    doubt_flags = {}
    for dim, count in doubt_counts.items():
        if count / n > 0.3:
            doubt_flags[dim] = {"count": count, "pct": round(count / n, 2), "weight": 0.7}

    return {
        "total_pairs": n,
        "new_domain_pairs": len(d_new),
        "existing_domain_pairs": len(d_existing),
        "dim_A_avg_diff": round(a_avg, 2),
        "dim_B_avg_diff": round(b_avg, 2),
        "dim_C_avg_diff": round(c_avg, 2),
        "dim_D_emb_win_pct": round(d_all_pct, 2),
        "dim_D_emb_win_pct_new": round(d_new_pct, 2),
        "dim_D_emb_win_pct_existing": round(d_existing_pct, 2),
        "self_doubt_flags": doubt_flags,
    }


def decide_merge(summary: dict) -> str:
    n = summary.get("total_pairs", 0)
    if n < 10:
        return "insufficient_data"

    d_win = summary.get("dim_D_emb_win_pct", 0)
    d_existing = summary.get("dim_D_emb_win_pct_existing", 0)
    d_new = summary.get("dim_D_emb_win_pct_new", 0)

    cond1 = d_win >= 0.60
    cond2 = d_win >= 0.55
    cond3 = d_existing >= d_new

    if cond1 and cond2 and cond3:
        return "merge"
    elif cond1 and cond2 and not cond3:
        return "rag_gain_unclear"
    else:
        return "no_merge"
