import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import loader, naming, recompute, shared, style
from src.engine.metrics import summarize, yearly_returns

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def render():
    ok, _ = loader.split_errors(shared.current_lb())
    labels = ok["label"].tolist()
    if not labels:
        st.title("Run detail")
        st.info("No successful runs in this batch.")
        return

    fmap = naming.friendly_map(ok)
    default = st.session_state.get("selected_label")
    idx = labels.index(default) if default in labels else 0
    label = st.selectbox("Run", labels, index=idx, format_func=lambda l: fmap[l])
    row = ok[ok["label"] == label].iloc[0].to_dict()
    color = style.family_color(row["name"])

    st.markdown(f'<h1><span style="color:{color}">●</span> {fmap[label]}</h1>',
                unsafe_allow_html=True)
    st.caption(f"Technical label: `{label}`")
    with st.expander("How to read this page"):
        st.markdown(
            "- **Equity** = what $100,000 would have grown to, day by day.\n"
            "- **Drawdown** = how far below its own record high the account was. "
            "The deepest dip is the pain you'd have had to sit through.\n"
            "- **Monthly heatmap** = green months made money, red months lost it. "
            "Whole red years show when this strategy breaks.\n"
            "- The numbers up top are the report card — hover each for what it means."
        )

    try:
        result = shared.run_result(row)
    except recompute.UnknownStrategyError as e:
        st.error(str(e))
        return

    problems = recompute.check_stale(row, result)
    if problems:
        st.error("**LEADERBOARD STALE** — engine changed since this batch "
                 "ran. Re-run: `.venv/bin/python -m scripts.screen`\n\n- "
                 + "\n- ".join(problems))

    stats = summarize(result)
    c = st.columns(6)
    c[0].metric("CAGR", f"{stats['cagr']:.1%}",
                help="Average yearly growth rate.")
    c[1].metric("Sharpe", f"{stats['sharpe']:.2f}",
                help="Return per unit of risk. Above 1 good, above 2 excellent.")
    c[2].metric("Max drawdown", f"{stats['max_dd']:.1%}",
                help="Worst peak-to-trough loss along the way.")
    c[3].metric("Trades", f"{stats['n_trades']} ({stats['sample_flag']})",
                help="Under 30 trades = the stats are noise; 100+ preferred.")
    c[4].metric("Turnover/yr", f"{stats['turnover']:.1f}x",
                help="How much of the portfolio is replaced per year.")
    c[5].metric("Exposure", f"{stats['exposure']:.0%}",
                help="Share of time invested rather than sitting in cash.")

    eq = result.equity
    fig = px.line(eq, title="Equity — growth of $100k (log scale)", log_y=True)
    fig.update_traces(line_color=color, showlegend=False)
    spy_eq, _ = shared.spy_benchmark()
    if spy_eq is not None:
        fig.add_scatter(x=spy_eq.index, y=spy_eq.values, mode="lines",
                        name="S&P 500 buy & hold",
                        line=dict(color=style.MUTED, width=1.5, dash="dot"))
    fig.update_yaxes(title="$")
    st.plotly_chart(style.apply_plotly_defaults(fig))

    dd = eq / eq.cummax() - 1
    fig = px.area(dd, title="Drawdown — % below record high")
    fig.update_traces(line_color=style.NEG, fillcolor="rgba(220,38,38,0.25)",
                      showlegend=False)
    fig.update_yaxes(tickformat=".0%", title="% below peak")
    st.plotly_chart(style.apply_plotly_defaults(fig))

    monthly = eq.resample("ME").last().pct_change().dropna()
    if len(monthly) >= 2:
        grid = pd.DataFrame({"year": monthly.index.year,
                             "month": monthly.index.strftime("%b"),
                             "ret": monthly.values})
        pivot = (grid.pivot(index="year", columns="month", values="ret")
                 .reindex(columns=MONTHS))
        lim = float(abs(pivot.fillna(0)).max().max()) or 0.01
        fig = px.imshow(pivot, color_continuous_scale="RdYlGn",
                        zmin=-lim, zmax=lim, aspect="auto",
                        title="Monthly returns", text_auto=".0%",
                        labels={"color": "return"})
        fig.update_coloraxes(colorbar_tickformat=".0%")
        st.plotly_chart(style.apply_plotly_defaults(fig))
    else:
        st.info("Run too short for a monthly heatmap.")

    yr = yearly_returns(eq)
    fig = px.bar(yr, title="Year by year")
    fig.update_traces(marker_color=[style.POS if v >= 0 else style.NEG
                                    for v in yr.values], showlegend=False)
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(style.apply_plotly_defaults(fig))
    with st.expander("Year-by-year table"):
        st.dataframe(yr.to_frame("return").style.format("{:.1%}"),
                     width="stretch")

    cls = recompute.STRATEGIES.get(row["name"])
    if cls:
        with st.expander("What this strategy does", expanded=True):
            st.markdown(cls.description)
