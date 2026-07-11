import os
import pandas as pd
import pytest

FIX = "fixtures/spy_options_small.parquet"

@pytest.mark.skipif(not os.path.exists(FIX), reason="fixture not built yet")
def test_fixture_shape():
    df = pd.read_parquet(FIX)
    assert list(df.columns) == ["date","expiry","dte","strike","right","bid","ask","mid",
                                "close","delta","iv","underlying"]
    assert set(df["right"]) <= {"P","C"}
    assert (df["bid"] > 0).all() and (df["ask"] > 0).all()
    # both puts and calls, a spread of deltas incl. something near 0.30 magnitude
    assert (df["right"] == "P").any() and (df["right"] == "C").any()
    assert ((df["delta"].abs() - 0.30).abs() < 0.15).any()
    assert df["date"].nunique() >= 2  # multiple trading days
