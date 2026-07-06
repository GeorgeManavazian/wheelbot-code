import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared
from src.engine.metrics import summarize, yearly_returns


def render():
    st.title("Run detail")
    ok, _ = loader.split_errors(shared.current_lb())
    labels = ok["label"].tolist()
    if not labels:
        st.info("No successful runs in this batch.")
        return

    default = st.session_state.get("selected_label")
    idx = labels.index(default) if default in labels else 0
    label = st.selectbox("Run", labels, index=idx)
    row = ok[ok["label"] == label].iloc[0].to_dict()

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
    c[0].metric("CAGR", f"{stats['cagr']:.1%}")
    c[1].metric("Sharpe", f"{stats['sharpe']:.2f}")
    c[2].metric("Max DD", f"{stats['max_dd']:.1%}")
    c[3].metric("Trades", f"{stats['n_trades']} ({stats['sample_flag']})")
    c[4].metric("Turnover/yr", f"{stats['turnover']:.1f}x")
    c[5].metric("Exposure", f"{stats['exposure']:.0%}")

    eq = result.equity
    st.plotly_chart(px.line(eq, title="Equity ($, log scale)", log_y=True),
                    use_container_width=True)
    dd = eq / eq.cummax() - 1
    st.plotly_chart(px.area(dd, title="Drawdown"), use_container_width=True)

    st.subheader("Year by year")
    yr = yearly_returns(eq)
    st.dataframe(yr.to_frame("return").style.format("{:.1%}"),
                 use_container_width=True)

    cls = recompute.STRATEGIES.get(row["name"])
    if cls:
        with st.expander("What this strategy does", expanded=True):
            st.markdown(cls.description)
