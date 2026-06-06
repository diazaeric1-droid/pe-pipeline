"""Tests for the fleet-triage ranking (pipeline_core.rank_fleet).

Deterministic, no API key. Uses the repo's own bootstrap to generate the synthetic
fleet + train the ESP model on first run, then exercises rank_fleet's contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline_core as pc  # noqa: E402

PRICE = 70.0
NRI = 0.80


@pytest.fixture(scope="module")
def board() -> pd.DataFrame:
    pc.bootstrap(log=lambda *_a, **_k: None)
    return pc.rank_fleet(price_per_bbl=PRICE, net_revenue_interest=NRI)


def test_returns_nonempty_dataframe(board: pd.DataFrame):
    assert isinstance(board, pd.DataFrame)
    assert not board.empty
    assert set(pc.RANK_COLUMNS).issubset(board.columns)


def test_sorted_descending_by_opportunity(board: pd.DataFrame):
    # opportunity_score must be monotonically non-increasing down the board.
    scores = board["opportunity_score"].tolist()
    assert scores == sorted(scores, reverse=True)
    assert board["opportunity_score"].is_monotonic_decreasing


def test_opportunity_equals_est_risked_npv(board: pd.DataFrame):
    # The sort key is exactly the risked-NPV estimate.
    assert (board["opportunity_score"] == board["est_risked_npv"]).all()


def test_deferred_usd_is_bopd_times_price_times_nri(board: pd.DataFrame):
    expected = board["deferred_bopd"] * PRICE * NRI
    # rank_fleet rounds deferred_usd_per_day to cents; compare within a cent.
    assert ((board["deferred_usd_per_day"] - expected).abs() < 0.01).all()


def test_top_well_score_ge_bottom(board: pd.DataFrame):
    assert board["opportunity_score"].iloc[0] >= board["opportunity_score"].iloc[-1]


def test_empty_fleet_returns_empty_typed_frame(monkeypatch):
    # Point the digest loader at an empty fleet → empty, correctly-typed frame.
    empty_dir = Path(__file__).resolve().parent / "_no_such_fleet"
    monkeypatch.setattr(pc, "DIGEST_FLEET", empty_dir)
    df = pc.rank_fleet(price_per_bbl=PRICE, net_revenue_interest=NRI)
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == list(pc.RANK_COLUMNS)


def test_deterministic_across_calls(board: pd.DataFrame):
    again = pc.rank_fleet(price_per_bbl=PRICE, net_revenue_interest=NRI)
    pd.testing.assert_frame_equal(board.reset_index(drop=True), again.reset_index(drop=True))


def test_handles_zero_price(board: pd.DataFrame):
    # Zero price must not raise and must zero out the deferred-$ flow.
    df = pc.rank_fleet(price_per_bbl=0.0, net_revenue_interest=NRI)
    assert not df.empty
    assert (df["deferred_usd_per_day"] == 0.0).all()
