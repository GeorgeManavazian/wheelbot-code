# Dashboard Polish — Design Spec

**Date:** 2026-07-06
**Status:** Approved by owner (session 2026-07-06)
**Depends on:** `2026-07-05-dashboard-design.md` (dashboard v1, merged to main)

## Goal

Make the dashboard readable to the owner at a glance: human names instead of technical labels, consistent color coding, plain-language explainers on every page. Clean light theme.

## Owner decisions

- Auto-generated friendly names (not hand-written nicknames).
- Color coding: good/bad metric shading + one color per strategy family + red/green period coloring.
- Red/green period coloring implemented as **monthly returns heatmap + yearly return bars** on run detail (owner approved deviation: never segment-color the equity line itself).
- Clean light theme (not dark).
- Luck-line highlighting explicitly NOT selected — leaderboard keeps the existing warning banner only.

## Non-goals

- No behavior changes to loader/recompute/engine. Pure presentation layer.
- No verdict buttons, no new pages, no dark mode.

## Design

### 1. Naming — new `dashboard/naming.py`

- Strategy classes get `display_name` class attribute: `TSTrend.display_name = "Trend Following"`, `MomentumRotation.display_name = "Momentum Rotation"` (additive, like `description`).
- `friendly_label(row: dict) -> str` builds `"Momentum Rotation · 3mo · top 5 · min score 20"`:
  - `lookback` days → `{63: "3mo", 125: "6mo", 189: "9mo", 252: "12mo"}`, fallback `f"{days}d"`.
  - `top_n` → `"top N"`. `min_score` → `"min score X"` only when > 0. `vol_window` hidden always.
  - Unknown strategy name → prettified fallback: `name.replace("_", " ").title()` + raw swept params.
- `friendly_map(ok: pd.DataFrame) -> dict[str, str]` label→friendly for pickers. Collisions impossible in practice (params differ) but if two rows produce the same friendly name, append the raw label in parentheses.
- Views show friendly names in all tables, pickers, chart legends. Raw `label` remains: as a muted caption on run detail, and in leaderboard as a hidden-by-default column (st.dataframe column order puts it last).

### 2. Style — new `dashboard/style.py` + `.streamlit/config.toml`

- config.toml: light theme, `primaryColor = "#4F46E5"` (indigo), `backgroundColor = "#FFFFFF"`, `secondaryBackgroundColor = "#F8FAFC"`, `textColor = "#0F172A"`.
- `FAMILY_COLORS: dict` keyed by strategy `name`: `ts_trend → "#EA580C"` (orange), `momentum_rotation → "#2563EB"` (blue); `family_color(name)` with gray `"#64748B"` fallback for unknown families.
- `style_leaderboard(df, metric_cols)` → pandas Styler: `background_gradient` green (`"Greens"`) on sharpe + cagr, red (`"Reds"`) on abs(max_dd); numbers always rendered (color never sole carrier). Percent formatting for cagr/max_dd/exposure, 2dp for sharpe.
- `apply_plotly_defaults(fig)` helper: `template="simple_white"`, gridcolor `#E2E8F0`, margins tightened.
- POS/NEG semantic constants: `"#16A34A"` / `"#DC2626"`.

### 3. Explainers

- Each view opens with `st.caption` one-liner + `st.expander("How to read this page")` containing 3–6 plain-language bullets (what the page shows, what good/bad looks like, one caveat).
- Leaderboard column tooltips via `st.column_config` `help=`: Sharpe ("return per unit of risk — above 1 good, above 2 excellent, below the luck line meaningless"), CAGR, Max DD, Trades, Turnover, Exposure, sample_flag.
- Run detail metric tiles get plain-word `help=` captions too (st.metric supports help).

### 4. View upgrades

- **Leaderboard:** friendly name first column; family badge via colored dot/text; styled table (item 2); crashed section unchanged.
- **Run detail:** H1 = friendly name, colored accent bar/dot in family color; muted caption shows raw label + params verbatim; equity line in family color; drawdown area in NEG red; NEW monthly returns heatmap (px.imshow, RdYlGn diverging, months × years grid, computed from `result.equity.resample("ME").last().pct_change()` — use `"M"` instead of `"ME"` if the installed pandas predates 2.2) and yearly bars (px.bar, green ≥0 / red <0, from existing `yearly_returns`). Yearly table stays.
- **Plateau:** y-axis synthetic `"_"` renamed `"(all)"`; colorbar titled with the metric; explainer expander.
- **Compare:** curves colored by family (`color_discrete_map` on family, dash-varied within family); metrics table transposed with friendly-name columns + same gradient shading.

## Error handling

- `friendly_label` never raises: missing/NaN params skipped; unknown families fall back to prettified name.
- Monthly heatmap guards short runs (< 2 months → skip chart with st.info).

## Testing

- pytest on `naming.py`: known-strategy label building (months mapping, min_score elision, top_n), unknown-strategy fallback, collision suffix.
- `display_name` mandatory: extend `tests/test_descriptions.py` to also require non-empty `display_name` on every concrete strategy.
- `style.py` pure helpers (`family_color`, formatter dict) unit-tested lightly; Styler/plotly verified by running the app against the real batch CSV.

## Build order

1. `naming.py` + `display_name` attrs + tests.
2. `style.py` + config.toml.
3. Leaderboard upgrade.
4. Run detail upgrade (incl. monthly heatmap + yearly bars).
5. Plateau + Compare upgrades.
