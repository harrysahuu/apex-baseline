"""
Apex Baseline - Dashboard (Phase 1)

Run with:   streamlit run app.py

Wraps engine.py (metrics) and var_generator.py (variance reports) in an
interactive page. All data is synthetic.
"""

import io
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import importer as imp
import var_generator as vg
from eia748 import GUIDELINES
from schedule import load_schedule, schedule_checks, schedule_summary
from engine import (
    CPI_THRESHOLD,
    MANAGEMENT_RESERVE,
    SPI_THRESHOLD,
    compute_metrics,
    flagged_packages,
    INTEGRITY_CHECK_COUNT,
    baseline_summary,
    earned_schedule_table,
    integrity_checks,
    labor_analysis,
    program_earned_schedule,
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
    "EAC_Weighted": "{:,.0f}", "EAC_Composite": "{:,.0f}", "EAC_Low": "{:,.0f}", "EAC_High": "{:,.0f}",
    "AT": "{:.0f}", "ES": "{:.2f}", "SPI_t": "{:.3f}", "SV_t": "{:+.2f}",
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
    if st.session_state.get("custom_df") is not None:
        st.warning(f"Showing: {st.session_state.get('custom_name', 'your data')}")
        if st.button("Back to the demo program", key="reset_sidebar"):
            for k in ("custom_df", "custom_name", "custom_mr"):
                st.session_state.pop(k, None)
            st.rerun()
    st.divider()
    if has_key:
        st.success("API key found. Claude drafts the VARs.")
    elif PRIVATE_MODE:
        st.info("No API key found. VARs use the built-in template.")
    else:
        st.info("Public demo: synthetic data, template-drafted reports.")

# --------------------------------------------------------------------- data
custom_df = st.session_state.get("custom_df")  # numbers the user typed or imported
using_custom = custom_df is not None
try:
    df = custom_df.copy() if using_custom else load_data()
except Exception as exc:  # noqa: BLE001 - show any data problem in plain words
    st.error(f"Could not load the data: {exc}")
    st.stop()

metrics = compute_metrics(df, cpi_t, spi_t)
reserve = float(st.session_state.get("custom_mr", 0.0)) if using_custom else MANAGEMENT_RESERVE
summary = program_summary(metrics, reserve)
flagged = flagged_packages(metrics)
data_key = int(pd.util.hash_pandas_object(df.astype(str), index=False).sum())

st.title("Apex Baseline")
st.caption("Program control and earned value engine  |  Modeled on EIA-748 and public IPMR guidance")
if using_custom:
    st.info(
        f"Showing your data: {st.session_state.get('custom_name', 'your data')}. "
        "Trends and Labor & Baseline need monthly history, hours and change files, so they are "
        "hidden for this data. Use 'Back to the demo program' in the sidebar to return."
    )
else:
    st.warning(
        "DEMO: all figures are synthetic. No real program, contractor, or contract data.",
        icon="⚠️",
    )

# ---------------------------------------------------------------- KPI row
c1, c2, c3 = st.columns(3)
c1.metric("Program CPI", f"{summary['CPI']:.3f}", f"{summary['CPI'] - cpi_t:+.3f} vs threshold")
c2.metric("Program SPI", f"{summary['SPI']:.3f}", f"{summary['SPI'] - spi_t:+.3f} vs threshold")
c3.metric("Flagged packages", f"{len(flagged)} of {len(metrics)}")
c4, c5, c6 = st.columns(3)
c4.metric("Estimate at completion (EAC)", vg.money(summary["EAC"]), vg.money(summary["VAC"]) + " vs budget")
c5.metric("Management reserve after overrun", vg.money(summary["MR_After_VAC"]))
c6.metric("% complete", f"{summary['Pct_Complete']:.1f}%")

tab_overview, tab_data, tab_eac, tab_trend, tab_labor, tab_sched, tab_integrity, tab_eia, tab_var = st.tabs(
    ["Overview", "Your Data", "Estimate at Completion", "Trends", "Labor & Baseline",
     "Schedule Health", "Data Integrity", "EIA-748 Checklist", "Variance Reports"]
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

    st.subheader("Independent forecast range")
    st.caption(
        "Three standard statistical methods forecast the final cost: CPI, an 80/20 CPI-SPI blend, "
        "and CPI x SPI. The bar spans the lowest to highest. A manager's estimate (LRE) far below "
        "the bar needs a documented basis."
    )
    rng = metrics[metrics["EAC_High"] > metrics["EAC_Low"]]
    bars = alt.Chart(rng).mark_bar(size=16, color="#C9CED6").encode(
        x=alt.X("WBS_ID:N", title="Work package"),
        y=alt.Y("EAC_Low:Q", title="Dollars", scale=alt.Scale(zero=False), axis=alt.Axis(format="~s")),
        y2="EAC_High:Q",
        tooltip=["WBS_ID", "WBS_Name", alt.Tooltip("EAC_Low:Q", format=",.0f"),
                 alt.Tooltip("EAC_High:Q", format=",.0f")],
    )
    ref_cols = [c for c in ("BAC", "LRE") if c in metrics.columns]
    refs = metrics[["WBS_ID", "WBS_Name"] + ref_cols].melt(
        id_vars=["WBS_ID", "WBS_Name"], var_name="Reference", value_name="Dollars")
    refs = refs[refs["WBS_ID"].isin(rng["WBS_ID"])]
    dots = alt.Chart(refs).mark_point(filled=True, size=110, opacity=1).encode(
        x="WBS_ID:N", y="Dollars:Q",
        color=alt.Color("Reference:N", legend=alt.Legend(title=None),
                        scale=alt.Scale(domain=["BAC", "LRE"], range=["#2E7D6B", "#B3261E"])),
        shape=alt.Shape("Reference:N", legend=None,
                        scale=alt.Scale(domain=["BAC", "LRE"], range=["square", "circle"])),
        tooltip=["WBS_ID", "Reference", alt.Tooltip("Dollars:Q", format=",.0f")],
    )
    st.altair_chart((bars + dots).properties(height=380), width="stretch")
    if "LRE_Position" in metrics.columns:
        st.dataframe(
            styled(metrics, ["WBS_ID", "WBS_Name", "BAC", "EAC_Low", "EAC", "EAC_Weighted",
                             "EAC_Composite", "EAC_High", "LRE", "LRE_Position"],
                   metrics["LRE_Position"] == "Below range"),
            width="stretch", hide_index=True,
        )
        st.caption("Red rows have a manager's estimate below the whole statistical range. "
                   "Complete work packages have one value across all methods.")

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
    if using_custom:
        st.info("Trends need a monthly history file, so they are hidden for your data. "
                "Switch back to the demo program to see them.")
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

            st.subheader("Earned Schedule: how late, in months")
            st.caption(
                "Dollar schedule variance always shrinks toward zero as a program finishes, which can "
                "hide lateness. Earned Schedule measures it in time instead: the month on the plan "
                "where today's earned value was supposed to be reached."
            )
            es_prog = program_earned_schedule(history)
            now = es_prog.iloc[-1]
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Months elapsed", f"{now['AT']:.0f}")
            e2.metric("Earned Schedule", f"{now['ES']:.2f} months")
            e3.metric("Schedule variance (time)", f"{now['SV_t']:+.2f} months")
            e4.metric("SPI (time) vs SPI ($)", f"{now['SPI_t']:.3f}", f"{now['SPI']:.3f} in dollars",
                      delta_color="off")
            es_long = es_prog.melt(id_vars=["Period"], value_vars=["SPI_t", "SPI"],
                                   var_name="Measure", value_name="Value")
            es_long["Measure"] = es_long["Measure"].map({"SPI_t": "SPI (time)", "SPI": "SPI (dollars)"})
            st.altair_chart(
                alt.Chart(es_long).mark_line(point=True, strokeWidth=3).encode(
                    x=alt.X("Period:N", title="Month"),
                    y=alt.Y("Value:Q", scale=alt.Scale(zero=False), title="Index"),
                    color=alt.Color("Measure:N", legend=alt.Legend(title=None),
                                    scale=alt.Scale(domain=["SPI (time)", "SPI (dollars)"],
                                                    range=["#3B6EA5", "#9AA5B1"])),
                    tooltip=["Period", "Measure", alt.Tooltip("Value:Q", format=".3f")],
                ).properties(height=300),
                width="stretch",
            )
            es_pkg = earned_schedule_table(history, df)
            st.dataframe(
                styled(es_pkg[~es_pkg["Complete"]].reset_index(drop=True),
                       ["WBS_ID", "WBS_Name", "AT", "ES", "SV_t", "SPI_t"],
                       es_pkg[~es_pkg["Complete"]].reset_index(drop=True)["SPI_t"] < spi_t),
                width="stretch", hide_index=True,
            )
            st.caption("AT = months elapsed. ES = Earned Schedule in months. SV(t) is months ahead (+) "
                       "or behind (-). Completed packages are left out. Red rows are below the SPI threshold.")

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
    if using_custom:
        st.info("Labor and baseline views need hours and change-log files, so they are hidden for "
                "your data. Switch back to the demo program to see them.")
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

with tab_data:
    st.subheader("Bring your own data")
    st.caption(
        "This is how a company's numbers get into Apex Baseline. Type numbers directly, or import "
        "an export from another system. Either way, the data is cleaned and checked first, and then "
        "every tab recomputes from it."
    )
    if PRIVATE_MODE:
        st.info("Private mode: files are processed in this session only.")
    else:
        st.warning(
            "Public demo: use sample or made-up numbers only. Do not enter real, proprietary, or "
            "export-controlled data here. In a real deployment, the data stays inside the "
            "customer's own environment.",
            icon="⚠️",
        )
    if using_custom:
        st.success(f"Currently showing: {st.session_state.get('custom_name')} "
                   f"({len(custom_df)} work packages).")
        if st.button("Go back to the demo program", key="reset_tab"):
            for k in ("custom_df", "custom_name", "custom_mr"):
                st.session_state.pop(k, None)
            st.rerun()

    def show_issues(issues: pd.DataFrame, blocked: bool) -> None:
        n_err = int((issues["Severity"] == "Error").sum())
        n_warn = int((issues["Severity"] == "Warning").sum())
        k1, k2 = st.columns(2)
        k1.metric("Errors", n_err)
        k2.metric("Warnings", n_warn)
        if issues.empty:
            st.success("All checks passed.")
            return
        st.dataframe(
            issues.style.apply(
                lambda row: ["background-color: rgba(179, 38, 30, 0.18)" if row["Severity"] == "Error"
                             else "background-color: rgba(214, 158, 46, 0.22)"] * len(row), axis=1),
            width="stretch", hide_index=True,
        )
        if blocked:
            st.error("Fix the errors above before this data can be loaded.")

    def load_custom(name: str, data: pd.DataFrame, mr: float) -> None:
        st.session_state["custom_df"] = data
        st.session_state["custom_name"] = name
        st.session_state["custom_mr"] = mr
        st.rerun()

    t_type, t_import = st.tabs(["Type your own numbers", "Import a file"])

    # ---- type numbers
    with t_type:
        st.caption(
            "Change any number, add a row at the bottom, or delete one. Then press the button and "
            "watch the whole dashboard follow your numbers."
        )
        editor_cols = ["WBS_ID", "WBS_Name", "BAC", "PV", "EV", "AC", "LRE"]
        base = custom_df if using_custom else load_data()
        base = base.reindex(columns=editor_cols)
        money_cfg = {c: st.column_config.NumberColumn(format="%.0f", min_value=0)
                     for c in ["BAC", "PV", "EV", "AC", "LRE"]}
        typed = st.data_editor(
            base, num_rows="dynamic", hide_index=True, width="stretch",
            column_config=money_cfg, key=f"editor_{st.session_state.get('custom_name', 'demo')}",
        )
        mr_typed = st.number_input("Management reserve ($, optional)", min_value=0.0, value=0.0,
                                   step=10000.0, key="mr_typed")
        st.caption("PV, EV and AC are cumulative to date. LRE is the manager's own estimate and may be left blank.")
        if st.button("Use these numbers", type="primary", key="use_typed"):
            ds, issues, blocked = imp.build_dataset(typed, {c: c for c in editor_cols}, 1.0)
            show_issues(issues, blocked)
            if not blocked and ds is not None:
                load_custom("Numbers you typed", ds, mr_typed)

    # ---- import a file
    with t_import:
        st.caption(
            "Other systems export with their own column names, units and number formats. Apex Baseline "
            "recognises the common ones, cleans the numbers, and checks everything before loading."
        )
        raw, src = None, None
        if PRIVATE_MODE:
            up = st.file_uploader("Upload a CSV export", type="csv", key="import_up")
            if up is not None:
                try:
                    raw, src = imp.read_raw(up), up.name
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not read that file: {exc}")
        if raw is None:
            st.markdown("**Try it with a sample export from a fictional other system**")
            st.download_button("Download the sample file", imp.SAMPLE_EXPORT.read_bytes(),
                               file_name="sample_export_other_system.csv", mime="text/csv")
            st.caption("Note the different column names (BCWS, BCWP, ACWP), dollar signs, and amounts in thousands.")
            if st.checkbox("Load the sample export", key="use_sample"):
                raw, src = imp.read_raw(imp.SAMPLE_EXPORT), "sample_export_other_system.csv"

        if raw is not None:
            st.markdown("**Step 1. What was in the file**")
            st.dataframe(raw.head(12), width="stretch", hide_index=True)

            st.markdown("**Step 2. Match the columns**")
            st.caption("Apex Baseline guessed the matches below. Change any that are wrong.")
            suggested = imp.suggest_mapping(list(raw.columns))
            options = ["(not in file)"] + list(raw.columns)
            mapping: dict[str, str | None] = {}
            grid = st.columns(2)
            for n, field in enumerate(imp.FIELDS):
                default = options.index(suggested[field]) if suggested[field] in options else 0
                pick = grid[n % 2].selectbox(imp.LABELS[field], options, index=default,
                                             key=f"map_{src}_{field}")
                mapping[field] = None if pick == "(not in file)" else pick
            unit_names = list(imp.UNIT_MULTIPLIER)
            u1, u2 = st.columns(2)
            unit = u1.selectbox("Money units in the file", unit_names,
                                index=unit_names.index(imp.detect_units(mapping)), key=f"unit_{src}")
            mr_import = u2.number_input("Management reserve ($, optional)", min_value=0.0, value=0.0,
                                        step=10000.0, key=f"mr_{src}")

            st.markdown("**Step 3. Checks**")
            ds, issues, blocked = imp.build_dataset(raw, mapping, imp.UNIT_MULTIPLIER[unit])
            show_issues(issues, blocked)

            st.markdown("**Step 4. Load it**")
            if st.button("Load into the dashboard", type="primary", key="use_import",
                         disabled=blocked or ds is None):
                load_custom(src, ds, mr_import)

with tab_sched:
    st.subheader("Schedule health")
    st.caption(
        "A program can look fine on cost and still have an unreliable schedule. These checks follow the "
        "publicly documented 14-point style of schedule assessment, using the commonly published thresholds."
    )
    if using_custom:
        st.info("Schedule checks use a task list, which the cost import does not include. "
                "Press 'Back to the demo program' in the sidebar to see them on the demo schedule.")
    else:
        try:
            sched = load_schedule()
            sched_checks = schedule_checks(sched)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read the schedule: {exc}")
            sched_checks = None
        if sched_checks is not None:
            ss = schedule_summary(sched_checks)
            h1, h2, h3 = st.columns(3)
            h1.metric("Checks passed", f"{ss['passed']} of {ss['ran']}")
            h2.metric("Checks failed", ss["failed"])
            h3.metric("Tasks in schedule", len(sched))
            st.dataframe(
                sched_checks.style.apply(
                    lambda row: ["background-color: rgba(179, 38, 30, 0.18)" if row["Status"] == "Fail"
                                 else "background-color: rgba(46, 125, 50, 0.15)" if row["Status"] == "Pass"
                                 else ""] * len(row), axis=1),
                width="stretch", hide_index=True,
            )
            st.caption(
                "Red = fails the threshold, green = passes. Check 12 (critical path test) needs a full scheduling "
                "engine and is not run. Demo schedule is fictional."
            )
            with st.expander("Show the demo schedule"):
                shown = sched.drop(columns=["Done"]).copy()
                for c in ("Start", "Finish", "Baseline_Finish", "Actual_Finish"):
                    shown[c] = shown[c].dt.strftime("%Y-%m-%d").fillna("")
                st.dataframe(shown, width="stretch", hide_index=True)

with tab_integrity:
    st.subheader("Data integrity checks")
    st.caption(
        "Before any number is trusted, the inputs are scanned for impossible or suspicious values. "
        "Errors must be fixed. Warnings deserve a second look."
    )
    if using_custom:
        st.info("For your data, the checks that need history or hours files are skipped.")
        found = integrity_checks(df)
    else:
        try:
            chk_hist, chk_hours = load_history(), load_hours()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load supporting files: {exc}")
            chk_hist, chk_hours = None, None
        chk_df = df
        demo_fail = st.checkbox("Show me what failed checks look like (injects sample errors into a copy)")
        if demo_fail and chk_hist is not None:
            chk_df = df.copy()
            chk_df.loc[chk_df["WBS_ID"] == "1.3.3", "EV"] = 0
            chk_df.loc[chk_df["WBS_ID"] == "1.2.2", "LRE"] = 600000
            chk_hist = chk_hist.copy()
            chk_hist.loc[(chk_hist["WBS_ID"] == "1.2.1") & (chk_hist["Period"] == "2026-09"), "EV"] = 100000
            chk_hours = chk_hours.copy()
            chk_hours.loc[chk_hours["WBS_ID"] == "1.1.2", "AC_Labor"] = 999999
        found = integrity_checks(chk_df, chk_hist, chk_hours)

    n_err = int((found["Severity"] == "Error").sum())
    n_warn = int((found["Severity"] == "Warning").sum())
    i1, i2, i3 = st.columns(3)
    i1.metric("Checks run", INTEGRITY_CHECK_COUNT)
    i2.metric("Errors", n_err)
    i3.metric("Warnings", n_warn)
    if found.empty:
        st.success("All checks passed. No impossible or suspicious values found.")
    else:
        st.dataframe(
            found.style.apply(
                lambda row: ["background-color: rgba(179, 38, 30, 0.18)" if row["Severity"] == "Error"
                             else "background-color: rgba(214, 158, 46, 0.22)"] * len(row), axis=1),
            width="stretch", hide_index=True,
        )
        st.caption("Red rows are errors. Amber rows are warnings.")

with tab_eia:
    st.subheader("EIA-748 self-assessment")
    st.warning(
        "This is a working checklist, not a compliance certification. Guideline wording is a short "
        "paraphrase. Use the official standard and your contract for the real text.",
        icon="⚠️",
    )
    guide = pd.DataFrame(GUIDELINES, columns=["#", "Category", "Guideline (summary)",
                                              "Tool support", "How it is covered"])
    guide["Your status"] = "Not assessed"
    guide["Notes"] = ""

    counts = guide["Tool support"].value_counts()
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Guidelines", len(guide))
    g2.metric("Fully supported", int(counts.get("Supported", 0)))
    g3.metric("Partly supported", int(counts.get("Partial", 0)))
    g4.metric("Not covered", int(counts.get("Not covered", 0)))
    st.caption("Most gaps are process or organizational matters (indirect costs, material accounting, "
               "organization structure) or need data this demo does not have yet, such as a schedule.")

    cats = ["All"] + list(dict.fromkeys(guide["Category"]))
    pick = st.radio("Category", cats, horizontal=True)
    view = guide if pick == "All" else guide[guide["Category"] == pick]
    edited = st.data_editor(
        view, hide_index=True, width="stretch", key=f"eia_{pick}",
        disabled=["#", "Category", "Guideline (summary)", "Tool support", "How it is covered"],
        column_config={
            "Your status": st.column_config.SelectboxColumn(
                options=["Not assessed", "Not started", "In progress", "Satisfied"], required=True),
            "Notes": st.column_config.TextColumn(width="medium"),
        },
    )
    st.download_button("Download this view as CSV", edited.to_csv(index=False),
                       file_name="eia748_assessment.csv", mime="text/csv")
    st.caption("Your status and notes last until you close or refresh the page. Download to keep them.")

with tab_var:
    st.subheader("Variance Analysis Reports")
    if flagged.empty:
        st.success("No work packages breach the current thresholds.")
    else:
        st.write(
            f"{len(flagged)} work packages breach the thresholds. "
            "Generate draft reports for each one."
        )
        key = (round(cpi_t, 2), round(spi_t, 2), data_key)

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
