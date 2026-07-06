import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared, style
from src.batch.runner import plateau_table


def render():
    st.title("Parameter plateau")
    st.caption("Is a good result robust, or a lucky parameter choice? "
               "Plateaus = robust; lone spikes = curve-fit.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Each cell = one parameter combination's score.\n"
            "- A **plateau** (whole region of similar green) means the strategy "
            "works across many settings — a real, robust effect.\n"
            "- A **lone bright spike** surrounded by weak neighbors means that "
            "one setting got lucky — don't trust it.\n"
            "- Switch the metric to check the same story holds for growth and "
            "drawdown, not just Sharpe."
        )
    ok, _ = loader.split_errors(shared.current_lb())
    if ok.empty:
        st.info("No successful runs in this batch.")
        return

    families = ok["name"].unique().tolist()

    def fam_title(n):
        cls = recompute.STRATEGIES.get(n)
        return getattr(cls, "display_name", "") or n

    name = st.selectbox("Strategy family", families, format_func=fam_title)
    cls = recompute.STRATEGIES.get(name)
    params = list(cls.DEFAULTS) if cls else []
    swept = [p for p in params if p in ok.columns
             and ok.loc[ok["name"] == name, p].nunique() > 1]
    if not swept:
        st.info(f"'{fam_title(name)}' has no swept parameters in this batch — "
                "plateau needs a grid with ≥2 values.")
        return
    param = st.selectbox("Parameter", swept)
    metric = st.selectbox("Metric", ["sharpe", "cagr", "max_dd"])

    table = plateau_table(ok, name, param, metric)
    ylab = " × ".join(str(n) for n in table.index.names if n != "_") or "(all)"
    fig = px.imshow(table, text_auto=".2f", aspect="auto",
                    color_continuous_scale="RdYlGn",
                    labels={"x": param, "y": ylab, "color": metric})
    st.plotly_chart(style.apply_plotly_defaults(fig))
    st.dataframe(table.style.format("{:.2f}", na_rep="—"),
                 width="stretch")
