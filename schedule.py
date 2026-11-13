"""
Apex Baseline - schedule health checks.

A schedule can be on budget and still be unreliable. These checks follow the
publicly documented "14-point" style of schedule assessment: missing logic,
leads and lags, constraints, float, long tasks, missed tasks and two
performance indices (CPLI and BEI). All thresholds are the commonly published
ones. Nothing here is specific to any company or tool.

Demo scale: the checks read a flat task list. They do not schedule anything,
so the "critical path test" is reported as not run.
"""
import re
from pathlib import Path

import pandas as pd

SCHEDULE_FILE = Path(__file__).parent / "synthetic_schedule.csv"
STATUS_DATE = pd.Timestamp("2026-11-30")
HIGH_FLOAT, HIGH_DURATION = 44, 44

_REL = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*:\s*(FS|SS|FF|SF)\s*:\s*(-?\d+)\s*$")


def load_schedule(path=SCHEDULE_FILE) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"Predecessors": str, "Actual_Finish": str}).fillna({"Predecessors": "", "Actual_Finish": ""})
    for c in ("Start", "Finish", "Baseline_Finish"):
        df[c] = pd.to_datetime(df[c])
    df["Actual_Finish"] = pd.to_datetime(df["Actual_Finish"].replace("", pd.NA))
    df["Done"] = df["Pct_Complete"] >= 100
    return df


def _relations(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for _, r in df.iterrows():
        for part in [p for p in str(r["Predecessors"]).split(";") if p.strip()]:
            m = _REL.match(part)
            if not m:
                raise ValueError(f"Task {r['Task_ID']}: cannot read predecessor '{part}'")
            out.append({"Pred": m.group(1), "Succ": r["Task_ID"], "Type": m.group(2), "Lag": int(m.group(3))})
    return pd.DataFrame(out, columns=["Pred", "Succ", "Type", "Lag"])


def _ids(series_mask, df, limit=6) -> str:
    ids = list(df.loc[series_mask, "Task_ID"])
    if not ids:
        return "-"
    return ", ".join(ids[:limit]) + (f" +{len(ids) - limit} more" if len(ids) > limit else "")


def schedule_checks(df: pd.DataFrame, status: pd.Timestamp = STATUS_DATE) -> pd.DataFrame:
    rel = _relations(df)
    open_ = df[~df["Done"]]
    n_open = max(len(open_), 1)
    rows = []

    def add(num, name, value, limit_text, passed, detail):
        rows.append({"#": num, "Check": name, "Result": value, "Threshold": limit_text,
                     "Status": "Info" if passed is None else ("Pass" if bool(passed) else "Fail"),
                     "Tasks to look at": detail})

    def pct(count, base):
        return 100.0 * count / max(base, 1)

    # 1 Logic: open tasks missing a predecessor or successor
    has_pred = df["Task_ID"].isin(rel["Succ"])
    has_succ = df["Task_ID"].isin(rel["Pred"])
    first, last = df.index[0], df.index[-1]
    missing = (~df["Done"]) & ((~has_pred) | (~has_succ)) & ~df.index.isin([first, last])
    p = pct(missing.sum(), n_open)
    add(1, "Missing logic", f"{p:.1f}% ({int(missing.sum())})", "5% or less", p <= 5, _ids(missing, df))
    # 2 Leads
    lead_ids = set(rel.loc[rel["Lag"] < 0, "Succ"])
    m = df["Task_ID"].isin(lead_ids)
    add(2, "Leads (negative lag)", str(int(m.sum())), "0", m.sum() == 0, _ids(m, df))
    # 3 Lags
    lag_ids = set(rel.loc[rel["Lag"] > 0, "Succ"])
    m = df["Task_ID"].isin(lag_ids)
    p = pct(m.sum(), len(df))
    add(3, "Lags (positive lag)", f"{p:.1f}% ({int(m.sum())})", "5% or less", p <= 5, _ids(m, df))
    # 4 Relationship types
    fs = pct((rel["Type"] == "FS").sum(), len(rel))
    nonfs = set(rel.loc[rel["Type"] != "FS", "Succ"])
    add(4, "Finish-to-start relationships", f"{fs:.1f}%", "90% or more", fs >= 90, _ids(df["Task_ID"].isin(nonfs), df))
    # 5 Hard constraints
    hard = df["Constraint"].isin(["Must Start On", "Must Finish On", "Start No Later Than", "Finish No Later Than"])
    p = pct((hard & ~df["Done"]).sum(), n_open)
    add(5, "Hard constraints", f"{p:.1f}% ({int((hard & ~df['Done']).sum())})", "5% or less", p <= 5, _ids(hard & ~df["Done"], df))
    # 6 High float
    m = (~df["Done"]) & (df["Total_Float_Days"] > HIGH_FLOAT)
    p = pct(m.sum(), n_open)
    add(6, f"High float (over {HIGH_FLOAT} days)", f"{p:.1f}% ({int(m.sum())})", "5% or less", p <= 5, _ids(m, df))
    # 7 Negative float
    m = (~df["Done"]) & (df["Total_Float_Days"] < 0)
    add(7, "Negative float", str(int(m.sum())), "0", m.sum() == 0, _ids(m, df))
    # 8 High duration
    m = (~df["Done"]) & (df["Duration_Days"] > HIGH_DURATION)
    p = pct(m.sum(), n_open)
    add(8, f"High duration (over {HIGH_DURATION} days)", f"{p:.1f}% ({int(m.sum())})", "5% or less", p <= 5, _ids(m, df))
    # 9 Invalid dates
    bad = ((df["Actual_Finish"] > status) | ((~df["Done"]) & (df["Finish"] < status)))
    add(9, "Invalid dates", str(int(bad.sum())), "0", bad.sum() == 0, _ids(bad, df))
    # 10 Resources
    m = (~df["Done"]) & (df["Resourced"].astype(str).str.upper() != "Y") & (df["Duration_Days"] > 0)
    add(10, "Open tasks with no resources", str(int(m.sum())), "0", m.sum() == 0, _ids(m, df))
    # 11 Missed tasks
    due = df[df["Baseline_Finish"] <= status]
    late = due[(due["Actual_Finish"].isna()) | (due["Actual_Finish"] > due["Baseline_Finish"])]
    p = pct(len(late), len(due))
    add(11, "Missed tasks (finished late or not finished)", f"{p:.1f}% ({len(late)} of {len(due)})", "5% or less",
        p <= 5, ", ".join(list(late['Task_ID'])[:6]) or "-")
    # 12 Critical path test
    rows.append({"#": 12, "Check": "Critical path test", "Result": "Not run", "Threshold": "Needs a scheduling engine",
                 "Status": "Info", "Tasks to look at": "-"})
    # 13 CPLI
    finish = open_["Finish"].max()
    length = max((finish - status).days, 1)
    min_float = float(open_["Total_Float_Days"].min()) if len(open_) else 0.0
    cpli = (length + min_float) / length
    add(13, "Critical path length index (CPLI)", f"{cpli:.2f}", "0.95 or more", cpli >= 0.95,
        _ids((~df['Done']) & (df['Total_Float_Days'] == min_float), df))
    # 14 BEI
    done_due = int(due["Actual_Finish"].notna().sum())
    bei = done_due / max(len(due), 1)
    add(14, "Baseline execution index (BEI)", f"{bei:.2f}", "0.95 or more", bei >= 0.95, "-")

    return pd.DataFrame(rows)


def schedule_summary(checks: pd.DataFrame) -> dict:
    ran = checks[checks["Status"] != "Info"]
    return {"passed": int((ran["Status"] == "Pass").sum()), "failed": int((ran["Status"] == "Fail").sum()),
            "ran": len(ran)}
