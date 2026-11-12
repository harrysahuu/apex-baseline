"""
Apex Baseline - data importer.

Real programs export their numbers from other systems with different column
names, units and number formats. This module:

  1. recognises the common EVM column names (including the older BCWS / BCWP /
     ACWP terms used in public EVM literature),
  2. cleans messy numbers ("$1,250.5", "(300)", values in thousands),
  3. checks the result before anything is loaded.

Nothing here is specific to any company or tool. It only knows public EVM terms.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from engine import integrity_checks

REQUIRED = ["WBS_ID", "WBS_Name", "BAC", "PV", "EV", "AC"]
OPTIONAL = ["LRE", "CP_PV", "CP_EV", "CP_AC"]
FIELDS = REQUIRED + OPTIONAL
MAX_ROWS = 500

LABELS = {
    "WBS_ID": "Work package or control account ID",
    "WBS_Name": "Name or description",
    "BAC": "Budget at completion (BAC)",
    "PV": "Planned value (PV, or BCWS)",
    "EV": "Earned value (EV, or BCWP)",
    "AC": "Actual cost (AC, or ACWP)",
    "LRE": "Manager's latest estimate (optional)",
    "CP_PV": "Current period planned value (optional)",
    "CP_EV": "Current period earned value (optional)",
    "CP_AC": "Current period actual cost (optional)",
}

ALIASES = {
    "WBS_ID": ["wbs", "wbs id", "wbs_id", "work package", "work package id", "control account",
               "control account id", "ca", "ca id", "element", "wbs element", "task id", "id"],
    "WBS_Name": ["wbs name", "name", "description", "work package name", "work package description",
                 "control account name", "title", "wbs description"],
    "BAC": ["bac", "budget at completion", "budget", "total budget"],
    "PV": ["pv", "planned value", "bcws", "budgeted cost of work scheduled", "cum pv",
           "cumulative pv", "cum bcws"],
    "EV": ["ev", "earned value", "bcwp", "budgeted cost of work performed", "cum ev",
           "cumulative ev", "cum bcwp"],
    "AC": ["ac", "actual cost", "acwp", "actual cost of work performed", "cum ac",
           "cumulative ac", "cum acwp"],
    "LRE": ["lre", "latest revised estimate", "management estimate", "manager eac",
            "manager estimate", "mgmt eac", "cam eac", "eac manager"],
    "CP_PV": ["cp pv", "current period pv", "cur pv", "period pv", "cp bcws", "current period bcws"],
    "CP_EV": ["cp ev", "current period ev", "cur ev", "period ev", "cp bcwp", "current period bcwp"],
    "CP_AC": ["cp ac", "current period ac", "cur ac", "period ac", "cp acwp", "current period acwp"],
}

UNIT_MULTIPLIER = {"Dollars": 1.0, "Thousands of dollars": 1_000.0, "Millions of dollars": 1_000_000.0}


def _norm(text: str) -> str:
    """Lowercase, drop anything in brackets (like units), keep letters and digits only."""
    text = re.sub(r"[\(\[].*?[\)\]]", "", str(text))
    return re.sub(r"[^a-z0-9]", "", text.lower())


_ALIAS_LOOKUP = {field: {_norm(a) for a in names} for field, names in ALIASES.items()}


def read_raw(source) -> pd.DataFrame:
    """Read a CSV (path or uploaded file) as text, so nothing is guessed or altered."""
    raw = pd.read_csv(source, dtype=str, encoding="utf-8-sig")
    raw = raw.replace(r"^\s*$", np.nan, regex=True).dropna(how="all").reset_index(drop=True)
    if raw.empty:
        raise ValueError("The file has no data rows.")
    if len(raw) > MAX_ROWS:
        raise ValueError(f"The file has {len(raw)} rows. This tool accepts up to {MAX_ROWS}.")
    return raw


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    """Guess which file column holds each Apex Baseline field."""
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for field in FIELDS:
        mapping[field] = None
        for col in columns:
            if col not in used and _norm(col) in _ALIAS_LOOKUP[field]:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def detect_units(mapping: dict[str, str | None]) -> str:
    """Look for '$K', '(000s)', 'millions' and similar in the money column headers."""
    heads = " ".join(str(mapping[f]) for f in ("BAC", "PV", "EV", "AC", "LRE") if mapping.get(f)).lower()
    if re.search(r"\$\s*m\b|\(\s*m\s*\)|million|\$\s*mm", heads):
        return "Millions of dollars"
    if re.search(r"\$\s*k\b|\(\s*k\s*\)|thousand|\(\s*000s?\s*\)|\$\s*000", heads):
        return "Thousands of dollars"
    return "Dollars"


def _to_number(series: pd.Series) -> pd.Series:
    """'$1,250.5' -> 1250.5, '(300)' -> -300, blank or text -> NaN."""
    text = series.fillna("").astype(str).str.strip()
    negative = text.str.match(r"^\(.*\)$")
    cleaned = text.str.replace(r"[\$,\s\(\)]", "", regex=True)
    number = pd.to_numeric(cleaned, errors="coerce").astype(float)
    return number.where(~negative, -number)


def _frame(issues: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(issues, columns=["Severity", "Check", "WBS_ID", "Detail"])


def build_dataset(raw: pd.DataFrame, mapping: dict[str, str | None],
                  multiplier: float = 1.0) -> tuple[pd.DataFrame | None, pd.DataFrame, bool]:
    """Turn raw file contents into a clean Apex Baseline dataset.

    Returns (dataset or None, issues, blocked). `blocked` is True when any error
    means the data should not be loaded. Warnings never block.
    """
    issues: list[dict] = []

    def add(severity: str, check: str, where: str, detail: str) -> None:
        issues.append({"Severity": severity, "Check": check, "WBS_ID": where, "Detail": detail})

    raw = raw.replace(r"^\s*$", np.nan, regex=True).dropna(how="all").reset_index(drop=True)
    if raw.empty:
        add("Error", "No data rows", "-", "There are no rows to load.")
        return None, _frame(issues), True

    missing = [f for f in REQUIRED if not mapping.get(f)]
    for f in missing:
        add("Error", "Required field not matched", "-", f"Choose a column for: {LABELS[f]}")
    if missing:
        return None, _frame(issues), True

    id_col = mapping["WBS_ID"]
    blank_id = raw[id_col].isna()
    for i in raw.index[blank_id]:
        add("Error", "Missing ID", f"Row {i + 2}", "This row has no work package ID.")
    ids = raw[id_col].fillna("").astype(str).str.strip()
    for dup in ids[ids.duplicated(keep=False) & ~blank_id].unique():
        add("Error", "Duplicate ID", dup, "This ID appears more than once.")

    names = raw[mapping["WBS_Name"]].fillna("").astype(str).str.strip()
    names = names.where(names != "", ids)
    out = pd.DataFrame({"WBS_ID": ids, "WBS_Name": names})

    required_numeric = ["BAC", "PV", "EV", "AC"]
    for field in required_numeric + [f for f in OPTIONAL if mapping.get(f)]:
        col = mapping[field]
        number = _to_number(raw[col])
        blank = raw[col].isna()
        bad = number.isna() & ~blank
        shown = list(raw.index[bad][:5])
        for i in shown:
            add("Error", "Not a number", f"Row {i + 2}", f"'{raw.at[i, col]}' in column {col}")
        if bad.sum() > len(shown):
            add("Error", "Not a number", "-", f"...and {int(bad.sum()) - len(shown)} more in column {col}")
        if field in required_numeric:
            for i in raw.index[blank]:
                add("Error", "Missing value", f"Row {i + 2}", f"{col} is blank")
        elif blank.any():
            add("Warning", "Some values blank", "-", f"{col} is blank in {int(blank.sum())} row(s)")
        for i in raw.index[number < 0]:
            add("Error", "Negative value", f"Row {i + 2}", f"{col} is negative")
        out[field] = number * multiplier

    current = [c for c in ("CP_PV", "CP_EV", "CP_AC") if mapping.get(c)]
    if 0 < len(current) < 3:
        out = out.drop(columns=current)
        add("Warning", "Current-period fields ignored", "-",
            "Current period needs all three of PV, EV and AC. Those columns were left out.")

    if any(i["Severity"] == "Error" for i in issues):
        return None, _frame(issues), True

    checks = integrity_checks(out)
    found = pd.concat([_frame(issues), checks], ignore_index=True)
    order = {"Error": 0, "Warning": 1}
    found = found.sort_values("Severity", key=lambda s: s.map(order), kind="stable").reset_index(drop=True)
    blocked = bool((found["Severity"] == "Error").any())
    return out, found, blocked


SAMPLE_EXPORT = Path(__file__).parent / "sample_export_other_system.csv"
