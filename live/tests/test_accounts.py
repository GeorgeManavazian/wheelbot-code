from live.accounts import (CAPITALS, NS, cap_label, account_label,
                           account_dir, account_paths, all_accounts)


def test_grid_is_5x5_unique():
    accts = all_accounts()
    assert len(accts) == 25
    assert len(set(accts)) == 25
    assert set(c for c, _ in accts) == set(CAPITALS)
    assert set(n for _, n in accts) == set(NS)


def test_cap_label():
    assert cap_label(5_000) == "5k"
    assert cap_label(250_000) == "250k"


def test_account_label_and_dir_unique_per_account():
    labels = {account_label(c, n) for c, n in all_accounts()}
    dirs = {account_dir(c, n) for c, n in all_accounts()}
    assert len(labels) == 25 and len(dirs) == 25
    assert account_label(5_000, 3) == "5k_N3"
    assert account_dir(250_000, 5).endswith("accounts/250k_N5")


def test_account_paths_shape():
    p = account_paths(100_000, 2)
    assert p["state"].endswith("100k_N2/state.json")
    assert p["trades"].endswith("100k_N2/trades.jsonl")
    assert p["snapshots"].endswith("100k_N2/snapshots.jsonl")
    assert p["dir"].endswith("100k_N2")


def test_b6_retired_names_are_out_of_the_universe():
    """B6 (owner kill-list 2026-08-02, all 11 approved): dead tickers, ghosts
    the vendor still quotes (PARA frozen at $11.04 since the PSKY merger),
    sub-$1 wrecks and surgery-scarred names are banned from the tradable
    universe -- subtracted, not deleted, so the source lists stay a faithful
    record and the exclusion states itself."""
    from live.universe import UNIVERSE, _RETIRED
    assert _RETIRED == frozenset({
        "PARA", "WBA", "GPRO", "CHPT", "PLUG",          # dead / penny
        "SIRI", "AMCR", "DD", "DOC", "FLEX", "FTV",     # surgery-scarred
    })
    leaks = _RETIRED & set(UNIVERSE)
    assert not leaks, f"B6: retired names still tradable: {sorted(leaks)}"
    # the ban must also hold against future list edits: a name re-added to a
    # source list must still be subtracted
    import live.universe as u
    assert all(t not in u.UNIVERSE for t in _RETIRED)
