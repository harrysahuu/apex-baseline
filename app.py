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
    baseline_summary,
    labor_analysis,
    load_changes,
    load_data,
    load_history,
    load_hours,
    package_trend,
    program_summary,
    program_trend,
)

st.set_page_config(page_title="Apex Baseline", layout="wide")

FORMATS = {
    "BAC": "{:,.0f}", "PV": "{:,.0f}", "EV": "{:,.0f}", "AC": "{:,.0f}",
    "CV": "{:,.0f}", "SV": "{:,.0f}", "EAC": "{:,.0f}", "ETC": "{:,.0f}",
    "VAC": "{:,.0f}", "LRE": "{:,.0f}",
    "CPI": "{:.3f}", "SPI": "{:.3f}", "TCPI_BAC": "{:.3f}", "TCPI_LRE": "{:.3f}",
    "Pct_Complete": "{:.1f}%", "VAC_Pct": "{:+.1f}%",
    "BAC_Hrs": "{:,.0f}", "EV_Hrs": "{:,.0f}", "AC_Hrs": "{:,.0f}",
    "Planned_Rate": "${:,.2f}", "Actual_Rate": "${:,.2f}", "Hours_Eff": "{:.3f}",
    "EV_Labor": "{:,.0f}", "AC_Labor": "{:,.0f}", "Labor_CV": "{:,.0f}",
    "Usage_Var": "{:,.0f}", "Rate_Var": "{:,.0f}",
    "Original_BAC": "{:,.0f}", "Approved_Changes": "{:+,.0f}", "Amount": "{:+,.0f}",
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
# PUBLIC DEMO vs PRIVATE USE
# On a public website, strangers could upload real data or run up your API bill.
# So uploads and AI drafting are OFF unless APEX_PRIVATE_MODE=1 is set on YOUR machine.
PRIVATE_MODE = os.environ.get("APEX_PRIVATE_MODE") == "1"
has_key = PRIVATE_MODE and bool(os.environ.get("ANTHROPIC_API_KEY"))

with st.sidebar:
    st.header("Settings")
    cpi_t = st.slider("CPI threshold", 0.70, 1.00, float(CPI_THRESHOLD), 0.01)
    spi_t = st.slider("SPI threshold", 0.70, 1.00, float(SPI_THRESHOLD), 0.01)
    upload = None
    if PRIVATE_MODE:
        upload = st.file_uploader("Use your own CSV (optional)", type="csv")
        st.caption(
            "Needs columns: WBS_ID, WBS_Name, BAC, PV, EV, AC. "
            "Optional: CP_PV, CP_EV, CP_AC, LRE."
        )
    st.divider()
    if has_key:
        st.success("API key found. Claude drafts the VARs.")
    elif PRIVATE_MODE:
        st.info("No API key found. VARs use the built-in template.")
    else:
        st.info("Public demo: synthetic data, template-drafted reports.")

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
st.caption("Program control and earned value engine  |  Modeled on EIA-748 and public IPMR guidance")
if not upload:
    st.warning(
        "DEMO: all figures are synthetic. No real program, contractor, or contract data.",
        icon="⚠️",
    )

# ---------------------------------------------------------------- KPI row
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Program CPI", f"{summary['CPI']:.3f}", f"{summary['CPI'] - cpi_t:+.3f} vs threshold")
c2.metric("Program SPI", f"{summary['SPI']:.3f}", f"{summary['SPI'] - spi_t:+.3f} vs threshold")
c3.metric("EAC", vg.money(summary["EAC"]), vg.money(summary["VAC"]) + " vs BAC")
c4.metric("% Complete", f"{summary['Pct_Complete']:.1f}%")
c5.metric("Mgmt reserve after VAC", vg.money(summary["MR_After_VAC"]))
c6.metric("Flagged packages", f"{len(flagged)} of {len(metrics)}")

tab_overview, tab_eac, tab_trend, tab_labor, tab_var = st.tabs(
    ["Overview", "Estimate at Completion", "Trends", "Labor & Baseline", "Variance Reports"]
)

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
with tab_trend:
    if upload:
        st.info("Trends use the built-in monthly history, so they are hidden for uploaded data.")
    else:
        try:
            history = load_history()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load the monthly history: {exc}")
            history = None

        if history is not None:
            prog = program_trend(history, df)
            pkg = package_trend(history)

            st.subheader("Program performance over time")
            st.caption(
                "CPI and SPI each month, cumulative to date. "
                "The dashed line is the flag threshold; falling toward or below it means trouble."
            )
            idx = prog.melt(id_vars=["Period"], value_vars=["CPI", "SPI"],
                            var_name="Index", value_name="Value")
            lines = alt.Chart(idx).mark_line(point=True, strokeWidth=3).encode(
                x=alt.X("Period:N", title="Month"),
                y=alt.Y("Value:Q", scale=alt.Scale(zero=False), title="Index"),
                color=alt.Color("Index:N", legend=alt.Legend(title=None),
                                scale=alt.Scale(domain=["CPI", "SPI"],
                                                range=["#B3261E", "#3B6EA5"])),
                tooltip=["Period", "Index", alt.Tooltip("Value:Q", format=".3f")],
            )
            rule = alt.Chart(pd.DataFrame({"y": [cpi_t]})).mark_rule(
                strokeDash=[5, 5], color="#888"
            ).encode(y="y:Q")
            st.altair_chart((lines + rule).properties(height=340), width="stretch")

            st.subheader("Forecast over time")
            st.caption(
                "EAC is the projected final cost. When it climbs above the budget line "
                "(BAC), the program is forecast to overrun."
            )
            fc = prog.melt(id_vars=["Period"], value_vars=["BAC", "EAC"],
                           var_name="Measure", value_name="Dollars")
            fc_chart = alt.Chart(fc).mark_line(point=True, strokeWidth=3).encode(
                x=alt.X("Period:N", title="Month"),
                y=alt.Y("Dollars:Q", scale=alt.Scale(zero=False),
                        axis=alt.Axis(format="~s"), title="Dollars"),
                color=alt.Color("Measure:N", legend=alt.Legend(title=None),
                                scale=alt.Scale(domain=["BAC", "EAC"],
                                                range=["#9AA5B1", "#B3261E"])),
                strokeDash=alt.StrokeDash("Measure:N", legend=None,
                                          scale=alt.Scale(domain=["BAC", "EAC"],
                                                          range=[[5, 5], [1, 0]])),
                tooltip=["Period", "Measure", alt.Tooltip("Dollars:Q", format=",.0f")],
            ).properties(height=340)
            st.altair_chart(fc_chart, width="stretch")

            st.subheader("Single work package")
            choices = df["WBS_ID"] + "  " + df["WBS_Name"]
            default = int(metrics["CPI"].idxmin())
            picked = st.selectbox("Work package", choices, index=default)
            wbs = picked.split("  ")[0]
            one = pkg[pkg["WBS_ID"] == wbs].melt(
                id_vars=["Period"], value_vars=["CPI", "SPI"],
                var_name="Index", value_name="Value",
            ).dropna()
            one_chart = alt.Chart(one).mark_line(point=True, strokeWidth=3).encode(
                x=alt.X("Period:N", title="Month"),
                y=alt.Y("Value:Q", scale=alt.Scale(zero=False), title="Index"),
                color=alt.Color("Index:N", legend=alt.Legend(title=None),
                                scale=alt.Scale(domain=["CPI", "SPI"],
                                                range=["#B3261E", "#3B6EA5"])),
                tooltip=["Period", "Index", alt.Tooltip("Value:Q", format=".3f")],
            ).properties(height=300)
            st.altair_chart(one_chart, width="stretch")
            st.caption("Packages that finished early show no change in later months.")

with tab_labor:
    if upload:
        st.info("Labor and baseline views use the built-in demo files, so they are hidden for uploaded data.")
    else:
        try:
            lab = labor_analysis(load_hours()).merge(df[["WBS_ID", "WBS_Name"]], on="WBS_ID")
            changes = load_changes()
            bsum, walk = baseline_summary(changes, df)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load the labor or baseline files: {exc}")
            st.stop()

        # ---------------- labor
        st.subheader("Labor: hours vs rate")
        st.caption(
            "An overrun can come from people taking more hours than the work earned, "
            "or from each hour costing more than planned. Knowing which one tells you the fix."
        )
        total_cv, total_usage, total_rate = (lab[c].sum() for c in ("Labor_CV", "Usage_Var", "Rate_Var"))
        eff = lab["EV_Hrs"].sum() / lab["AC_Hrs"].sum()
        l1, l2, l3, l4 = st.columns(4)
        l1.metric("Labor cost variance", vg.money(total_cv))
        l2.metric("From extra hours", vg.money(total_usage))
        l3.metric("From higher rates", vg.money(total_rate))
        l4.metric("Hours efficiency", f"{eff:.3f}")

        split = lab.melt(id_vars=["WBS_ID", "WBS_Name"], value_vars=["Usage_Var", "Rate_Var"],
                         var_name="Cause", value_name="Dollars")
        split["Cause"] = split["Cause"].map({"Usage_Var": "Hours (efficiency)", "Rate_Var": "Hourly rate"})
        st.altair_chart(
            alt.Chart(split).mark_bar().encode(
                x=alt.X("WBS_ID:N", title="Work package"),
                y=alt.Y("Dollars:Q", title="Labor variance ($), negative = unfavorable",
                        axis=alt.Axis(format="~s")),
                color=alt.Color("Cause:N", legend=alt.Legend(title=None),
                                scale=alt.Scale(domain=["Hours (efficiency)", "Hourly rate"],
                                                range=["#B3261E", "#3B6EA5"])),
                tooltip=["WBS_ID", "WBS_Name", "Cause", alt.Tooltip("Dollars:Q", format=",.0f")],
            ).properties(height=340),
            width="stretch",
        )
        st.dataframe(
            styled(lab, ["WBS_ID", "WBS_Name", "BAC_Hrs", "EV_Hrs", "AC_Hrs", "Hours_Eff",
                         "Planned_Rate", "Actual_Rate", "Labor_CV", "Usage_Var", "Rate_Var"],
                   lab["Hours_Eff"] < cpi_t),
            width="stretch", hide_index=True,
        )
        st.caption("Red rows are earning less than 0.90 hours of work per hour spent. "
                   "Labor is assumed to be 65% of cost in this synthetic data.")

        # ---------------- baseline
        st.subheader("Baseline changes")
        st.caption(
            "Only approved changes are in the current budget (BAC). Pending changes are work "
            "the customer has not yet approved."
        )
        b1, b2, b3 = st.columns(3)
        b1.metric("Original BAC", vg.money(bsum["Original_BAC"]))
        b2.metric("Approved changes (net)", vg.money(bsum["Approved_Net"]))
        b3.metric("Current BAC", vg.money(bsum["Current_BAC"]))
        b4, b5, b6 = st.columns(3)
        b4.metric("Management reserve used", vg.money(bsum["MR_Used"]),
                  f"of {vg.money(bsum['MR_Start'])} to start", delta_color="off")
        b5.metric("Management reserve left", vg.money(bsum["MR_Remaining"]))
        b6.metric("Pending, not in BAC", vg.money(bsum["Pending_Total"]),
                  f"{bsum['Pending_Count']} change awaiting approval", delta_color="off")

        st.dataframe(
            styled(changes, ["Change_ID", "Date", "WBS_ID", "Type", "Amount", "Status", "Description"],
                   changes["Status"] == "Pending"),
            width="stretch", hide_index=True,
        )
        st.caption("Tinted rows are pending approval.")

        st.markdown("**Budget walk by work package**")
        st.dataframe(
            styled(walk, ["WBS_ID", "WBS_Name", "Original_BAC", "Approved_Changes", "BAC"],
                   walk["Approved_Changes"] != 0),
            width="stretch", hide_index=True,
        )
        st.caption("Tinted rows had approved budget changes. "
                   "Original BAC + approved changes = current BAC.")

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
