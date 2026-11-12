"""
Apex Baseline - Dashboard (Phase 1)

Run with:   streamlit run app.py

Wraps engine.py (metrics) and var_generator.py (variance reports) in an
interactive page. All data is synthetic.
"""

import io
import os

import altair as alt
import pandas as pd
import streamlit as st

import var_generator as vg
from engine import (
    CPI_THRESHOLD,
    SPI_THRESHOLD,
    compute_metrics,
    flagged_packages,
    load_data,
    program_summary,
)

st.set_page_config(page_title="Apex Baseline", layout="wide")

FORMATS = {
    "BAC": "{:,.0f}", "PV": "{:,.0f}", "EV": "{:,.0f}", "AC": "{:,.0f}",
    "CV": "{:,.0f}", "SV": "{:,.0f}", "EAC": "{:,.0f}", "ETC": "{:,.0f}",
    "VAC": "{:,.0f}", "LRE": "{:,.0f}",
    "CPI": "{:.3f}", "SPI": "{:.3f}", "TCPI_BAC": "{:.3f}", "TCPI_LRE": "{:.3f}",
    "Pct_Complete": "{:.1f}%", "VAC_Pct": "{:+.1f}%",
}


def styled(df: pd.DataFrame, cols: list[str], highlight: pd.Series):
    """Table with problem rows tinted red. `highlight` is a True/False Series."""
    shown = df[[c for c in cols if c in df.columns]]
    fmt = {k: v for k, v in FORMATS.items() if k in shown.columns}

    def tint(row):
        color = "background-color: rgba(179, 38, 30, 0.18)" if highlight.loc[row.name] else ""
        return [color] * len(row)

    return shown.style.format(fmt, na_rep="n/a").apply(tint, axis=1)


def performance_chart(m: pd.DataFrame, cpi_t: float, spi_t: float) -> alt.Chart:
    """CPI vs SPI scatter. Bottom-left of the dashed lines = trouble."""
    plot = m.assign(Status=m["Flagged"].map({True: "Flagged", False: "OK"}))
    lo = float(min(0.7, m["CPI"].min() - 0.05, m["SPI"].min() - 0.05))
    hi = float(max(1.1, m["CPI"].max() + 0.05, m["SPI"].max() + 0.05))
    scale = alt.Scale(domain=[lo, hi])

    base = alt.Chart(plot)
    points = base.mark_circle(size=170).encode(
        x=alt.X("SPI:Q", scale=scale, title="SPI (schedule efficiency)"),
        y=alt.Y("CPI:Q", scale=scale, title="CPI (cost efficiency)"),
        color=alt.Color(
            "Status:N",
            scale=alt.Scale(domain=["OK", "Flagged"], range=["#2E7D6B", "#B3261E"]),
            legend=alt.Legend(title=None),
        ),
        tooltip=[
            "WBS_ID", "WBS_Name",
            alt.Tooltip("CPI:Q", format=".3f"),
            alt.Tooltip("SPI:Q", format=".3f"),
        ],
    )
    labels = base.mark_text(dx=14, dy=-9, fontSize=11).encode(
        x="SPI:Q", y="CPI:Q", text="WBS_ID:N"
    )
    vline = alt.Chart(pd.DataFrame({"v": [spi_t]})).mark_rule(
        strokeDash=[5, 5], color="#888"
    ).encode(x="v:Q")
    hline = alt.Chart(pd.DataFrame({"v": [cpi_t]})).mark_rule(
        strokeDash=[5, 5], color="#888"
    ).encode(y="v:Q")
    return (points + labels + vline + hline).properties(height=430)


def eac_chart(m: pd.DataFrame) -> alt.Chart:
    """Budget vs statistical forecast vs the manager's own estimate."""
    measures = [c for c in ("BAC", "EAC", "LRE") if c in m.columns]
    long = m.melt(id_vars=["WBS_ID"], value_vars=measures, var_name="Measure", value_name="Dollars")
    return alt.Chart(long).mark_bar().encode(
        x=alt.X("WBS_ID:N", title="Work package"),
        xOffset="Measure:N",
        y=alt.Y("Dollars:Q", title="Dollars", axis=alt.Axis(format="~s")),
        color=alt.Color(
            "Measure:N",
            scale=alt.Scale(domain=["BAC", "EAC", "LRE"], range=["#9AA5B1", "#B3261E", "#2E7D6B"]),
            legend=alt.Legend(title=None),
        ),
        tooltip=["WBS_ID", "Measure", alt.Tooltip("Dollars:Q", format=",.0f")],
    ).properties(height=380)


# ------------------------------------------------------------------ sidebar
has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

with st.sidebar:
    st.header("Settings")
    cpi_t = st.slider("CPI threshold", 0.70, 1.00, float(CPI_THRESHOLD), 0.01)
    spi_t = st.slider("SPI threshold", 0.70, 1.00, float(SPI_THRESHOLD), 0.01)
    upload = st.file_uploader("Use your own CSV (optional)", type="csv")
    st.caption(
        "Needs columns: WBS_ID, WBS_Name, BAC, PV, EV, AC. "
        "Optional: CP_PV, CP_EV, CP_AC, LRE."
    )
    st.divider()
    if has_key:
        st.success("API key found. Claude drafts the VARs.")
    else:
        st.info("No API key found. VARs use the built-in template.")

# --------------------------------------------------------------------- data
try:
    df = load_data(upload) if upload else load_data()
except Exception as exc:  # noqa: BLE001 - show any data problem in plain words
    st.error(f"Could not load the data: {exc}")
    st.stop()

metrics = compute_metrics(df, cpi_t, spi_t)
summary = program_summary(metrics)
flagged = flagged_packages(metrics)

st.title("Apex Baseline")
st.caption("Program control and earned value engine  |  Synthetic data only")

# ---------------------------------------------------------------- KPI row
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Program CPI", f"{summary['CPI']:.3f}", f"{summary['CPI'] - cpi_t:+.3f} vs threshold")
c2.metric("Program SPI", f"{summary['SPI']:.3f}", f"{summary['SPI'] - spi_t:+.3f} vs threshold")
c3.metric("EAC", vg.money(summary["EAC"]), vg.money(summary["VAC"]) + " vs BAC")
c4.metric("% Complete", f"{summary['Pct_Complete']:.1f}%")
c5.metric("Mgmt reserve after VAC", vg.money(summary["MR_After_VAC"]))
c6.metric("Flagged packages", f"{len(flagged)} of {len(metrics)}")

tab_overview, tab_eac, tab_var = st.tabs(["Overview", "Estimate at Completion", "Variance Reports"])

# ----------------------------------------------------------------- overview
with tab_overview:
    st.subheader("Performance map")
    st.caption(
        "Each dot is a work package. Anything left of the vertical dashed line is behind "
        "schedule, and anything below the horizontal one is over cost."
    )
    st.altair_chart(performance_chart(metrics, cpi_t, spi_t), width="stretch")

    st.subheader("Work packages (cumulative to date)")
    st.dataframe(
        styled(
            metrics,
            ["WBS_ID", "WBS_Name", "PV", "EV", "AC", "CPI", "SPI", "CV", "SV"],
            metrics["Flagged"],
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption("Red rows breach a threshold.")

    if "CP_CPI" in metrics.columns:
        st.subheader("Current period")
        cur = metrics[["WBS_ID", "WBS_Name", "CP_PV", "CP_EV", "CP_AC",
                       "CP_CPI", "CP_SPI", "CP_CV", "CP_SV"]].copy()
        cur.columns = ["WBS_ID", "WBS_Name", "PV", "EV", "AC", "CPI", "SPI", "CV", "SV"]
        weak = (cur["CPI"] < cpi_t) | (cur["SPI"] < spi_t)
        st.dataframe(
            styled(cur, ["WBS_ID", "WBS_Name", "PV", "EV", "AC", "CPI", "SPI", "CV", "SV"], weak),
            width="stretch",
            hide_index=True,
        )

# ---------------------------------------------------------------------- EAC
with tab_eac:
    st.subheader("Budget vs forecast")
    st.caption(
        "BAC is the budget. EAC is the forecast from performance to date. "
        "LRE is the responsible manager's own estimate."
    )
    st.altair_chart(eac_chart(metrics), width="stretch")

    st.subheader("At-completion detail")
    optimistic = metrics["LRE_Optimistic"] if "LRE_Optimistic" in metrics.columns else metrics["VAC"] < 0
    st.dataframe(
        styled(
            metrics,
            ["WBS_ID", "WBS_Name", "BAC", "Pct_Complete", "EAC", "ETC", "VAC", "VAC_Pct",
             "LRE", "TCPI_BAC", "TCPI_LRE"],
            optimistic,
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Red rows have an LRE that sits well below the statistical EAC (more than 5% of BAC), "
        "meaning the manager's estimate may be too optimistic. EAC = AC + (BAC - EV) / CPI."
    )

    st.subheader("Program rollup")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("BAC", vg.money(summary["BAC"]))
    r2.metric("EAC", vg.money(summary["EAC"]))
    r3.metric("VAC", vg.money(summary["VAC"]), f"{summary['VAC_Pct']:+.1f}%")
    r4.metric("Management reserve", vg.money(summary["MR"]))
    if summary["MR_After_VAC"] < 0:
        st.warning(
            f"The projected overrun exceeds management reserve by "
            f"{vg.money(abs(summary['MR_After_VAC']))}."
        )

# ---------------------------------------------------------------------- VARs
with tab_var:
    st.subheader("Variance Analysis Reports")
    if flagged.empty:
        st.success("No work packages breach the current thresholds.")
    else:
        st.write(
            f"{len(flagged)} work packages breach the thresholds. "
            "Generate draft reports for each one."
        )
        key = (round(cpi_t, 2), round(spi_t, 2), upload.name if upload else "default")

        if st.button("Generate variance reports", type="primary"):
            reports = []
            with st.spinner("Drafting reports..."):
                for row in flagged.to_dict("records"):
                    draft, mode = vg.generate_var(row, has_key)
                    reports.append({"row": row, "draft": draft, "mode": mode})
            st.session_state["reports"] = reports
            st.session_state["reports_key"] = key

        reports = st.session_state.get("reports")
        if reports and st.session_state.get("reports_key") != key:
            st.warning("Settings changed since these reports were drafted. Generate them again.")
        elif reports:
            st.caption("Drafts only. A control account manager must confirm every root cause.")
            for n, r in enumerate(reports, start=1):
                row, draft = r["row"], r["draft"]
                title = f"VAR-{n:03d}  |  {row['WBS_ID']}  {row['WBS_Name']}  |  {vg.classify(row)} breach"
                with st.expander(title):
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("CPI", f"{row['CPI']:.3f}")
                    k2.metric("SPI", f"{row['SPI']:.3f}")
                    k3.metric("EAC", vg.money(row["EAC"]))
                    k4.metric("VAC", vg.money(row["VAC"]))
                    st.markdown("**Root cause**")
                    st.write(draft["root_cause"])
                    st.markdown("**Impact**")
                    st.write(draft["impact"])
                    st.markdown("**Corrective action**")
                    st.write(draft["corrective_action"])
                    st.markdown("**EAC assessment**")
                    st.write(vg.eac_assessment(row))
                    st.caption(f"Drafted by: {r['mode']}")

            d1, d2, _ = st.columns([1, 1, 3])
            if vg.HAVE_DOCX:
                buffer = io.BytesIO()
                vg.render_docx(reports, summary, buffer)
                d1.download_button(
                    "Download Word report",
                    buffer.getvalue(),
                    file_name="var_reports.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            else:
                d1.info("Run: pip install python-docx")
            d2.download_button(
                "Download Markdown",
                vg.render_markdown(reports, summary),
                file_name="var_reports.md",
            )
