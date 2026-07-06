import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared
from src.batch.runner import plateau_table


def render():
    st.title("Parameter plateau")
    st.caption("Robust edges form PLATEAUS across neighboring parameters; "
               "isolated spikes are curve-fit artifacts.")
    ok, _ = loader.split_errors(shared.current_lb())
    if ok.empty:
        st.info("No successful runs in this batch.")
        return

    families = ok["name"].unique().tolist()
    name = st.selectbox("Strategy family", families)
    cls = recompute.STRATEGIES.get(name)
    params = list(cls.DEFAULTS) if cls else []
    swept = [p for p in params if p in ok.columns
             and ok.loc[ok["name"] == name, p].nunique() > 1]
    if not swept:
        st.info(f"'{name}' has no swept parameters in this batch — "
                "plateau needs a grid with ≥2 values.")
        return
    param = st.selectbox("Parameter", swept)
    metric = st.selectbox("Metric", ["sharpe", "cagr", "max_dd"])

    table = plateau_table(ok, name, param, metric)
    st.plotly_chart(
        px.imshow(table, text_auto=".2f", aspect="auto",
                  labels={"x": param, "y": " × ".join(map(str, table.index.names)),
                          "color": metric}),
        use_container_width=True)
    st.dataframe(table.style.format("{:.2f}"), use_container_width=True)
