"""
Apex Baseline - EVM Calculation Engine (Phase 1)

Loads work-package level cost data, computes EIA-748 style performance
indices, variances and at-completion forecasts, and flags work packages
breaching thresholds.

Cumulative performance:
    CPI = EV / AC          SPI = EV / PV
    CV  = EV - AC          SV  = EV - PV

Forecasting (standard EVM formulas):
    EAC = AC + (BAC - EV) / CPI          (CPI method, primary forecast)
    EAC_Composite = AC + (BAC - EV) / (CPI * SPI)
    ETC = EAC - AC                       VAC = BAC - EAC
    TCPI = (BAC - EV) / (BAC - AC)       (efficiency needed to finish at BAC)
    TCPI_LRE = (BAC - EV) / (LRE - AC)   (efficiency needed to hit the manager's estimate)

BAC  = Budget at Completion
LRE  = Latest Revised Estimate (the responsible manager's own forecast)

All data is synthetic.
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA_PATH = Path(__file__).parent / "synthetic_evm_data.csv"
CPI_THRESHOLD = 0.90
SPI_THRESHOLD = 0.90
LRE_GAP_THRESHOLD = 0.05  # LRE below the statistical EAC by more than 5% of BAC is flagged
MANAGEMENT_RESERVE = 500_000  # synthetic program-level management reserve

REQUIRED_COLUMNS = ["WBS_ID", "WBS_Name", "BAC", "PV", "EV", "AC"]
OPTIONAL_NUMERIC = ["CP_PV", "CP_EV", "CP_AC", "LRE"]

MONEY_COLS = {
    "BAC", "PV", "EV", "AC", "CV", "SV", "EAC", "EAC_Composite", "ETC", "VAC",
    "LRE", "LRE_Gap", "CP_PV", "CP_EV", "CP_AC", "CP_CV", "CP_SV",
}
RATIO_COLS = {"CPI", "SPI", "CP_CPI", "CP_SPI", "TCPI_BAC", "TCPI_LRE"}
PCT_COLS = {"CV_Pct", "SV_Pct", "Pct_Complete", "Pct_Spent", "VAC_Pct"}


def load_data(path: Path = DATA_PATH) -> pd.DataFrame:
    """Load and validate the EVM dataset."""
    df = pd.read_csv(path, dtype={"WBS_ID": str})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    numeric = ["BAC", "PV", "EV", "AC"] + [c for c in OPTIONAL_NUMERIC if c in df.columns]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="raise")
        if (df[col] < 0).any():
            raise ValueError(f"Negative values found in {col}")

    if df["WBS_ID"].duplicated().any():
        raise ValueError("Duplicate WBS_ID values found")
    if (df["EV"] > df["BAC"]).any():
        raise ValueError("EV cannot exceed BAC")
    if (df["PV"] > df["BAC"]).any():
        raise ValueError("PV cannot exceed BAC")

    return df


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide, returning NaN (not inf) where the denominator is zero."""
    return numerator / denominator.replace(0, np.nan)


def compute_metrics(
    df: pd.DataFrame,
    cpi_threshold: float = CPI_THRESHOLD,
    spi_threshold: float = SPI_THRESHOLD,
    lre_gap_threshold: float = LRE_GAP_THRESHOLD,
) -> pd.DataFrame:
    """Add performance indices, forecasts and flags to the dataframe."""
    out = df.copy()

    # Cumulative performance
    out["CPI"] = _safe_ratio(out["EV"], out["AC"])
    out["SPI"] = _safe_ratio(out["EV"], out["PV"])
    out["CV"] = out["EV"] - out["AC"]
    out["SV"] = out["EV"] - out["PV"]
    out["CV_Pct"] = _safe_ratio(out["CV"], out["EV"]) * 100
    out["SV_Pct"] = _safe_ratio(out["SV"], out["PV"]) * 100
    out["Pct_Complete"] = _safe_ratio(out["EV"], out["BAC"]) * 100
    out["Pct_Spent"] = _safe_ratio(out["AC"], out["BAC"]) * 100

    # At-completion forecasts
    remaining_work = out["BAC"] - out["EV"]
    cpi_safe = out["CPI"].replace(0, np.nan)
    out["EAC"] = (out["AC"] + remaining_work / cpi_safe).fillna(out["BAC"])
    composite = (out["CPI"] * out["SPI"]).replace(0, np.nan)
    out["EAC_Composite"] = (out["AC"] + remaining_work / composite).fillna(out["EAC"])
    out["ETC"] = out["EAC"] - out["AC"]
    out["VAC"] = out["BAC"] - out["EAC"]
    out["VAC_Pct"] = _safe_ratio(out["VAC"], out["BAC"]) * 100

    # To-complete performance index (undefined once the work is done or the budget is spent)
    has_work_left = remaining_work > 0
    to_go_bac = (out["BAC"] - out["AC"]).where(lambda s: s > 0)
    out["TCPI_BAC"] = (remaining_work / to_go_bac).where(has_work_left)

    # Manager's estimate (LRE) realism check
    if "LRE" in out.columns:
        to_go_lre = (out["LRE"] - out["AC"]).where(lambda s: s > 0)
        out["TCPI_LRE"] = (remaining_work / to_go_lre).where(has_work_left)
        out["LRE_Gap"] = out["EAC"] - out["LRE"]  # positive = LRE more optimistic than EAC
        out["LRE_Optimistic"] = _safe_ratio(out["LRE_Gap"], out["BAC"]) >= lre_gap_threshold

    # Current period (last reporting period only)
    if {"CP_PV", "CP_EV", "CP_AC"} <= set(out.columns):
        out["CP_CPI"] = _safe_ratio(out["CP_EV"], out["CP_AC"])
        out["CP_SPI"] = _safe_ratio(out["CP_EV"], out["CP_PV"])
        out["CP_CV"] = out["CP_EV"] - out["CP_AC"]
        out["CP_SV"] = out["CP_EV"] - out["CP_PV"]

    # NaN comparisons evaluate False, so zero-AC / zero-PV packages are not flagged.
    out["Cost_Flag"] = out["CPI"] < cpi_threshold
    out["Schedule_Flag"] = out["SPI"] < spi_threshold
    out["Flagged"] = out["Cost_Flag"] | out["Schedule_Flag"]

    return out


def program_summary(metrics: pd.DataFrame) -> dict:
    """Roll up to program level. EAC is the bottom-up sum of work package EACs."""
    bac, pv, ev, ac = (metrics[c].sum() for c in ("BAC", "PV", "EV", "AC"))
    eac = metrics["EAC"].sum()
    vac = bac - eac
    to_go = bac - ac
    return {
        "BAC": bac,
        "PV": pv,
        "EV": ev,
        "AC": ac,
        "CPI": ev / ac if ac else float("nan"),
        "SPI": ev / pv if pv else float("nan"),
        "CV": ev - ac,
        "SV": ev - pv,
        "EAC": eac,
        "ETC": eac - ac,
        "VAC": vac,
        "VAC_Pct": vac / bac * 100 if bac else float("nan"),
        "Pct_Complete": ev / bac * 100 if bac else float("nan"),
        "TCPI_BAC": (bac - ev) / to_go if to_go > 0 else float("nan"),
        "LRE": metrics["LRE"].sum() if "LRE" in metrics.columns else float("nan"),
        "MR": MANAGEMENT_RESERVE,
        # Reserve left if it were used to cover any projected overrun
        "MR_After_VAC": MANAGEMENT_RESERVE + min(vac, 0),
    }


HISTORY_PATH = Path(__file__).parent / "synthetic_evm_history.csv"


def load_history(path: Path = HISTORY_PATH) -> pd.DataFrame:
    """Load monthly cumulative PV/EV/AC per work package (one row per package per period)."""
    hist = pd.read_csv(path, dtype={"WBS_ID": str, "Period": str})
    missing = [c for c in ["Period", "WBS_ID", "PV", "EV", "AC"] if c not in hist.columns]
    if missing:
        raise ValueError(f"History file missing columns: {missing}")
    return hist.sort_values(["Period", "WBS_ID"]).reset_index(drop=True)


def package_trend(history: pd.DataFrame) -> pd.DataFrame:
    """CPI and SPI for every work package in every period."""
    out = history.copy()
    out["CPI"] = _safe_ratio(out["EV"], out["AC"])
    out["SPI"] = _safe_ratio(out["EV"], out["PV"])
    return out


def program_trend(history: pd.DataFrame, bac: pd.DataFrame) -> pd.DataFrame:
    """Program-level CPI, SPI, EAC and VAC for each period.

    `bac` needs columns WBS_ID and BAC. EAC is the sum of work package EACs
    (CPI method), the same way program_summary() does it for the latest period.
    """
    h = history.merge(bac[["WBS_ID", "BAC"]], on="WBS_ID", how="left")
    cpi = _safe_ratio(h["EV"], h["AC"])
    h["EAC"] = (h["AC"] + (h["BAC"] - h["EV"]) / cpi.replace(0, np.nan)).fillna(h["BAC"])
    g = h.groupby("Period")[["BAC", "PV", "EV", "AC", "EAC"]].sum()
    g["CPI"] = g["EV"] / g["AC"]
    g["SPI"] = g["EV"] / g["PV"]
    g["VAC"] = g["BAC"] - g["EAC"]
    return g.reset_index()


def flagged_packages(metrics: pd.DataFrame) -> pd.DataFrame:
    """Return only work packages breaching a CPI or SPI threshold."""
    return metrics[metrics["Flagged"]].reset_index(drop=True)


def _show(df: pd.DataFrame, cols: list[str]) -> str:
    """Format selected columns for readable terminal output."""
    cols = [c for c in cols if c in df.columns]
    shown = df[cols].copy()
    for c in cols:
        if c in MONEY_COLS:
            shown[c] = shown[c].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "n/a")
        elif c in RATIO_COLS:
            shown[c] = shown[c].map(lambda v: f"{v:.3f}" if pd.notna(v) else "n/a")
        elif c in PCT_COLS:
            shown[c] = shown[c].map(lambda v: f"{v:.1f}%" if pd.notna(v) else "n/a")
    return shown.to_string(index=False)


def main() -> None:
    metrics = compute_metrics(load_data())
    summary = program_summary(metrics)
    flagged = flagged_packages(metrics)

    pd.set_option("display.width", 200)

    print("=== CUMULATIVE PERFORMANCE ===")
    print(_show(metrics, ["WBS_ID", "WBS_Name", "PV", "EV", "AC", "CPI", "SPI", "CV", "SV",
                          "Cost_Flag", "Schedule_Flag"]))

    print("\n=== CURRENT PERIOD ===")
    print(_show(metrics, ["WBS_ID", "WBS_Name", "CP_PV", "CP_EV", "CP_AC", "CP_CPI", "CP_SPI",
                          "CP_CV", "CP_SV"]))

    print("\n=== ESTIMATE AT COMPLETION ===")
    print(_show(metrics, ["WBS_ID", "WBS_Name", "BAC", "Pct_Complete", "EAC", "ETC", "VAC",
                          "VAC_Pct", "LRE", "TCPI_BAC", "TCPI_LRE", "LRE_Optimistic"]))

    print("\n=== PROGRAM SUMMARY ===")
    money_keys = {"BAC", "PV", "EV", "AC", "CV", "SV", "EAC", "ETC", "VAC", "LRE", "MR", "MR_After_VAC"}
    for key, value in summary.items():
        if key in money_keys:
            print(f"{key:>13}: {value:,.0f}")
        elif key in ("VAC_Pct", "Pct_Complete"):
            print(f"{key:>13}: {value:.1f}%")
        else:
            print(f"{key:>13}: {value:.3f}")

    print(f"\n=== FLAGGED (CPI < {CPI_THRESHOLD} or SPI < {SPI_THRESHOLD}): {len(flagged)} ===")
    if flagged.empty:
        print("None.")
    else:
        print(_show(flagged, ["WBS_ID", "WBS_Name", "CPI", "SPI", "CV", "SV", "EAC", "VAC",
                              "LRE_Optimistic"]))


if __name__ == "__main__":
    main()
