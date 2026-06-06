"""Tests for the shared fleet registry (vendored fleet_registry.py)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleet_registry as fr


def test_hero_wells_present_and_consistent():
    heroes = fr.hero_wells()
    assert len(heroes) == 6
    ids = {h.well_id for h in heroes}
    assert {"well_007", "well_013", "well_022", "well_041"} <= ids
    for h in heroes:
        assert h.hero is True and h.storyline and h.basin in ("Midland", "Delaware")


def test_get_is_deterministic_and_never_raises():
    for wid in ("well_001", "well_013", "well_099", "well_100", "weird", ""):
        assert fr.get(wid) == fr.get(wid)               # stable
        m = fr.get(wid)
        assert m.basin in ("Midland", "Delaware")
        assert m.lift in ("ESP", "Rod pump", "Gas lift", "Flowing")
        assert m.formation and m.area and m.api14.startswith("42-")


def test_hero_metadata_matches_app_behavior():
    # well_013 is the triage board's flagship ESP gas-interference well
    w13 = fr.get("well_013")
    assert w13.lift == "ESP" and w13.basin == "Midland" and w13.formation == "Wolfcamp A"


def test_enrich_is_additive_and_lossless():
    df = pd.DataFrame({"well_id": ["well_007", "well_013", "well_055"], "x": [1, 2, 3]})
    out = fr.enrich(df)
    assert len(out) == len(df)                          # no rows dropped
    assert list(out["x"]) == [1, 2, 3]                  # originals preserved
    for col in fr.META_COLUMNS:
        assert col in out.columns and out[col].notna().all()
    assert bool(out.loc[out["well_id"] == "well_007", "hero"].iloc[0]) is True
    assert bool(out.loc[out["well_id"] == "well_055", "hero"].iloc[0]) is False


def test_enrich_empty_frame_ok():
    empty = pd.DataFrame({"well_id": pd.Series([], dtype=str), "x": pd.Series([], dtype=float)})
    out = fr.enrich(empty)
    assert len(out) == 0 and "formation" in out.columns
