import pandas as pd
from dashboard import labels

def test_known_columns_map_to_english():
    assert labels.label("cost_to_close") == "Cost to close"
    assert labels.label("pct_of_credit") == "Credit kept"
    assert labels.label("realized_pnl") == "Realized P&L"
    assert labels.label("days_held") == "Days held"
    assert labels.label("qty") == "Contracts"
    assert labels.label("mean_ret") == "Mean return"
    assert labels.label("60_40") == "60/40"

def test_unknown_column_falls_back_to_prettified_name():
    assert labels.label("some_new_column") == "Some new column"

def test_humanize_renames_and_does_not_mutate():
    df = pd.DataFrame({"realized_pnl": [1.0], "days_held": [3]})
    out = labels.humanize(df)
    assert list(out.columns) == ["Realized P&L", "Days held"]
    assert list(df.columns) == ["realized_pnl", "days_held"]  # original untouched

def test_every_blotter_and_regime_column_is_mapped():
    # Columns the app actually renders. None may fall through to the fallback.
    blotter = ["opened", "closed", "instrument", "strike", "expiry", "qty", "credit",
               "outcome", "cost_to_close", "realized_pnl", "pct_of_credit", "days_held"]
    regime = ["dimension", "regime", "sharpe", "mean_ret", "bars"]
    for col in blotter + regime:
        assert col in labels.COLUMN_LABELS, f"{col} has no explicit label"
