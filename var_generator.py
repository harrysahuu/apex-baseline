"""
Apex Baseline - VAR Generator (Phase 1)

Takes work packages flagged by engine.py and drafts a Variance Analysis
Report (VAR) for each: Root Cause, Impact, Corrective Action, plus an
Estimate at Completion (EAC) assessment.

Two drafting modes:
  - LLM mode:      used automatically when ANTHROPIC_API_KEY is set
                   (and the `anthropic` package is installed).
  - Template mode: used otherwise, or if the API call fails. No key needed.

Outputs:
  - var_reports.md    quick-read version
  - var_reports.docx  Format 5 style Word report (needs: pip install python-docx)

All reports are DRAFTS that a control account manager must validate. The
tool only sees cost data, so any root cause it proposes is a hypothesis,
not a finding. The EAC assessment is calculated, not drafted by the LLM.

All data is synthetic.
"""

import json
import math
import os
from datetime import date
from pathlib import Path

from engine import compute_metrics, flagged_packages, load_data, program_summary

try:  # Word output is optional: pip install python-docx
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    HAVE_DOCX = True
except ImportError:
    HAVE_DOCX = False

MODEL = "claude-sonnet-5-5"
OUTPUT_PATH = Path(__file__).parent / "var_reports.md"
DOCX_PATH = Path(__file__).parent / "var_reports.docx"

SYSTEM_PROMPT = (
    "You are a program control analyst drafting Variance Analysis Reports "
    "(VARs) for an earned value management system compliant with EIA-748. "
    "Write in a concise, factual, audit-ready tone. Use ONLY the numbers "
    "provided. Do not invent specific facts such as names, dates, part "
    "numbers, vendors, or contract details. The data does not reveal the "
    "true cause of a variance, so present root causes as hypotheses for the "
    "control account manager to confirm. Respond with ONLY a JSON object "
    "with exactly these keys: root_cause, impact, corrective_action. Each "
    "value is a string of 2 to 4 sentences."
)


# ---------------------------------------------------------------- helpers

def _num(row: dict, key: str):
    """Return row[key] as a number, or None if missing or NaN."""
    value = row.get(key)
    if value is None:
        return None
    try:
        return None if math.isnan(value) else value
    except TypeError:
        return value


def money(value) -> str:
    """Format dollars with the sign in front: -$70,000."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def classify(row: dict) -> str:
    """Return 'cost', 'schedule', or 'both' depending on which threshold broke."""
    if row["Cost_Flag"] and row["Schedule_Flag"]:
        return "both"
    return "cost" if row["Cost_Flag"] else "schedule"


def build_user_prompt(row: dict) -> str:
    text = (
        f"Draft a VAR for this work package.\n"
        f"WBS: {row['WBS_ID']} - {row['WBS_Name']}\n"
        f"PV: {money(row['PV'])}\n"
        f"EV: {money(row['EV'])}\n"
        f"AC: {money(row['AC'])}\n"
        f"CPI: {row['CPI']:.3f}\n"
        f"SPI: {row['SPI']:.3f}\n"
        f"CV: {money(row['CV'])}\n"
        f"SV: {money(row['SV'])}\n"
    )
    if _num(row, "BAC") is not None and _num(row, "EAC") is not None:
        text += (
            f"BAC: {money(row['BAC'])}\n"
            f"EAC (CPI method): {money(row['EAC'])}\n"
            f"VAC: {money(row['VAC'])}\n"
            f"Percent complete: {row['Pct_Complete']:.1f}%\n"
        )
    text += f"Threshold breached: {classify(row)} (threshold is 0.90)."
    return text


def eac_assessment(row: dict) -> str:
    """Calculated (not LLM-written) assessment of the at-completion forecast."""
    if _num(row, "EAC") is None:
        return "EAC data not available for this work package."

    parts = [
        f"The CPI-based EAC is {money(row['EAC'])} against a BAC of {money(row['BAC'])}, "
        f"a projected variance at completion of {money(row['VAC'])} "
        f"({row['VAC_Pct']:+.1f}%). The work package is {row['Pct_Complete']:.0f}% complete "
        f"with {row['Pct_Spent']:.0f}% of budget spent."
    ]

    lre = _num(row, "LRE")
    if lre is not None:
        gap = row["LRE_Gap"]
        direction = "below" if gap > 0 else "above"
        sentence = (
            f"The responsible manager's latest revised estimate (LRE) is {money(lre)}, "
            f"{money(abs(gap))} {direction} the statistical EAC."
        )
        if row.get("LRE_Optimistic"):
            sentence += (
                " The gap exceeds the review threshold, so the LRE needs a documented "
                "basis of estimate or a supported recovery plan."
            )
        parts.append(sentence)

        tcpi = _num(row, "TCPI_LRE")
        if tcpi is not None:
            sentence = (
                f"Meeting the LRE requires a cost efficiency of {tcpi:.2f} on remaining work, "
                f"versus {row['CPI']:.2f} achieved to date."
            )
            if tcpi > row["CPI"] * 1.10:
                sentence += " The required improvement is not supported by performance to date."
            else:
                sentence += " This is broadly consistent with performance to date."
            parts.append(sentence)

    return " ".join(parts)


def template_draft(row: dict) -> dict:
    """Deterministic fallback draft. No API needed."""
    kind = classify(row)
    cv, sv = abs(row["CV"]), abs(row["SV"])

    cost_cause = (
        f"Actual cost is running ahead of earned value (CPI {row['CPI']:.3f}). "
        "Candidate drivers to confirm: labor hours or rates above plan, "
        "unplanned rework, or material price growth."
    )
    sched_cause = (
        f"Earned value is behind plan (SPI {row['SPI']:.3f}). Candidate "
        "drivers to confirm: late inputs from predecessor tasks, resource "
        "availability, or underestimated task duration."
    )
    cost_impact = (
        f"Unfavorable cost variance of ${cv:,.0f} to date. If current "
        "efficiency persists, this package will overrun its budget and "
        "increase the program Estimate at Completion."
    )
    sched_impact = (
        f"Unfavorable schedule variance of ${sv:,.0f} of work behind plan. "
        "If it is on the critical path, delivery of dependent work packages "
        "may slip."
    )
    cost_action = (
        "Review period actuals for charging errors, validate the estimate "
        "to complete with the control account manager, and decide whether "
        "re-planning or a management reserve request is warranted."
    )
    sched_action = (
        "Review the schedule logic and resource loading, identify recovery "
        "options such as re-sequencing or added resources, and confirm "
        "whether downstream milestones are affected."
    )

    if kind == "cost":
        parts = (cost_cause, cost_impact, cost_action)
    elif kind == "schedule":
        parts = (sched_cause, sched_impact, sched_action)
    else:
        parts = (
            f"{cost_cause} {sched_cause}",
            f"{cost_impact} {sched_impact}",
            f"{cost_action} {sched_action}",
        )

    impact = parts[1]
    if _num(row, "VAC") is not None:
        impact += (
            f" At the current cost efficiency, the projected variance at completion is "
            f"{money(row['VAC'])} against a budget of {money(row['BAC'])}."
        )

    return {"root_cause": parts[0], "impact": impact, "corrective_action": parts[2]}


def llm_draft(row: dict) -> dict:
    """Ask Claude for a draft. Raises on any problem so the caller can fall back."""
    import anthropic  # imported here so template mode works without it

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    message = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(row)}],
    )
    text = message.content[0].text.strip()

    # Strip markdown code fences if the model added them.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    data = json.loads(text)
    required = {"root_cause", "impact", "corrective_action"}
    if not required.issubset(data):
        raise ValueError(f"LLM response missing keys: {required - set(data)}")
    return {k: str(data[k]) for k in required}


def generate_var(row: dict, use_llm: bool) -> tuple[dict, str]:
    """Return (draft, mode_used)."""
    if use_llm:
        try:
            return llm_draft(row), "LLM"
        except Exception as exc:  # noqa: BLE001 - any failure falls back safely
            print(f"  ! LLM draft failed for {row['WBS_ID']} ({exc}); using template.")
    return template_draft(row), "Template"


# --------------------------------------------------------------- markdown

def render_markdown(reports: list[dict], summary: dict) -> str:
    lines = [
        "# Variance Analysis Reports (DRAFT)",
        "",
        f"Generated: {date.today().isoformat()}  ",
        f"Program CPI: {summary['CPI']:.3f} | Program SPI: {summary['SPI']:.3f}  ",
        f"Program BAC: {money(summary['BAC'])} | EAC: {money(summary['EAC'])} | "
        f"VAC: {money(summary['VAC'])}  ",
        f"Reports: {len(reports)}",
        "",
        "> Drafts for control account manager review. Root causes are "
        "hypotheses based only on cost data. Synthetic data.",
        "",
    ]
    for r in reports:
        row, draft = r["row"], r["draft"]
        lines += [
            f"## {row['WBS_ID']} - {row['WBS_Name']}",
            "",
            f"**Drafted by:** {r['mode']}  ",
            f"**Breach:** {classify(row)}  ",
            f"**CPI:** {row['CPI']:.3f} | **SPI:** {row['SPI']:.3f} | "
            f"**CV:** {money(row['CV'])} | **SV:** {money(row['SV'])}",
            "",
            "**Root Cause**  ",
            draft["root_cause"],
            "",
            "**Impact**  ",
            draft["impact"],
            "",
            "**Corrective Action**  ",
            draft["corrective_action"],
            "",
            "**EAC Assessment**  ",
            eac_assessment(row),
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


# ------------------------------------------------------------------- docx

NAVY = "1F2A44"
GRAY = "E7E9EE"
RED = "9B1C1C"
VARIANCE_LABEL = {"cost": "COST", "schedule": "SCHEDULE", "both": "COST AND SCHEDULE"}


def _pct(numerator: float, denominator: float) -> str:
    return f"{numerator / denominator * 100:+.1f}%" if denominator else "N/A"


def _shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _write(cell, text, bold=False, size=9, color=None, center=False) -> None:
    cell.text = ""
    para = cell.paragraphs[0]
    if center:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def _page_number(paragraph) -> None:
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    fld.append(run)
    paragraph._p.append(fld)


def _gap(doc, points: int = 4) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.space_before = Pt(0)
    para.add_run(" ").font.size = Pt(points)


def _label(doc, text: str) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(2)
    para.paragraph_format.space_after = Pt(2)
    run = para.add_run(text)
    run.bold = True
    run.font.size = Pt(8)


def _table(doc, heads: list[str], vals: list[str], bad: set | None = None,
           header_fill: str = GRAY, header_color: str | None = None) -> None:
    """Two-row table: header row, value row. Cells in `bad` (by index) print red."""
    bad = bad or set()
    table = doc.add_table(rows=2, cols=len(heads))
    table.style = "Table Grid"
    for j, (h, v) in enumerate(zip(heads, vals)):
        _shade(table.cell(0, j), header_fill)
        _write(table.cell(0, j), h, bold=True, center=True, size=8, color=header_color)
        _write(table.cell(1, j), v, bold=(j in bad), center=True,
               color=RED if j in bad else None)


def _box(doc, label: str, body: str) -> None:
    table = doc.add_table(rows=2, cols=1)
    table.style = "Table Grid"
    _shade(table.cell(0, 0), GRAY)
    _write(table.cell(0, 0), label, bold=True)
    _write(table.cell(1, 0), body, size=9.5)
    _gap(doc, 6)


def render_docx(reports: list[dict], summary: dict, path: Path) -> None:
    """Format 5 style (Explanation and Problem Analyses) Word report."""
    doc = Document()
    section = doc.sections[0]
    section.left_margin = section.right_margin = Inches(0.8)
    section.top_margin = section.bottom_margin = Inches(0.8)

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(9)

    # Page header and footer
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = header.add_run("SYNTHETIC DATA - FOR DEMONSTRATION ONLY - NOT A REAL PROGRAM")
    run.bold = True
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(RED)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Apex Baseline  |  DRAFT - pending Control Account Manager validation  |  Page ")
    run.font.size = Pt(8)
    _page_number(footer)

    # Title block
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("EXPLANATION AND PROBLEM ANALYSES")
    run.bold = True
    run.font.size = Pt(15)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run("Variance Analysis Report - modeled on the public IPMR / CPR Format 5 structure")
    run.italic = True
    run.font.size = Pt(9)

    # Identification block
    today = date.today()
    info = [
        ("Contractor", "[Synthetic Contractor]", "Contract / Program", "[Synthetic Program]"),
        ("Reporting Period", today.strftime("%B %Y"), "Date Prepared", today.isoformat()),
        ("Reporting Level", "Work Package", "Variance Threshold", "CPI or SPI below 0.90"),
    ]
    table = doc.add_table(rows=len(info), cols=4)
    table.style = "Table Grid"
    for i, values in enumerate(info):
        for j, value in enumerate(values):
            cell = table.cell(i, j)
            if j % 2 == 0:
                _shade(cell, GRAY)
            _write(cell, value, bold=(j % 2 == 0))
    _gap(doc, 8)

    # Program summary (cumulative performance)
    _label(doc, "PROGRAM SUMMARY - CUMULATIVE TO DATE")
    heads = ["PV", "EV", "AC", "CV", "SV", "CPI", "SPI", "VARs"]
    vals = [
        money(summary["PV"]), money(summary["EV"]), money(summary["AC"]),
        money(summary["CV"]), money(summary["SV"]),
        f"{summary['CPI']:.3f}", f"{summary['SPI']:.3f}", str(len(reports)),
    ]
    _table(doc, heads, vals, bad={3, 4}, header_fill=NAVY, header_color="FFFFFF")
    _gap(doc, 6)

    # Program summary (at completion)
    _label(doc, "PROGRAM SUMMARY - AT COMPLETION")
    heads = ["BAC", "EAC", "VAC", "VAC %", "% Complete", "TCPI", "Mgmt Reserve", "MR After VAC"]
    tcpi = summary["TCPI_BAC"]
    vals = [
        money(summary["BAC"]), money(summary["EAC"]), money(summary["VAC"]),
        f"{summary['VAC_Pct']:+.1f}%", f"{summary['Pct_Complete']:.1f}%",
        "N/A" if math.isnan(tcpi) else f"{tcpi:.3f}",
        money(summary["MR"]), money(summary["MR_After_VAC"]),
    ]
    bad = set()
    if summary["VAC"] < 0:
        bad |= {2, 3}
    if summary["MR_After_VAC"] < 0:
        bad.add(7)
    _table(doc, heads, vals, bad=bad, header_fill=NAVY, header_color="FFFFFF")
    _gap(doc, 6)

    note = doc.add_paragraph()
    run = note.add_run(
        "EAC is the bottom-up sum of work package EACs calculated with the CPI method: "
        "AC + (BAC - EV) / CPI. MR After VAC shows management reserve remaining if it were "
        "used to cover the projected overrun; a negative value means reserve is insufficient."
    )
    run.italic = True
    run.font.size = Pt(8)

    # One page per variance
    for n, r in enumerate(reports, start=1):
        row, draft = r["row"], r["draft"]
        kind = classify(row)
        doc.add_page_break()

        banner = doc.add_table(rows=1, cols=1)
        _shade(banner.cell(0, 0), NAVY)
        _write(
            banner.cell(0, 0),
            f"VAR-{n:03d}   |   WBS {row['WBS_ID']}   |   {row['WBS_Name'].upper()}",
            bold=True, size=11, color="FFFFFF",
        )
        _gap(doc, 4)

        # Cumulative to date
        _label(doc, "CUMULATIVE TO DATE")
        heads = ["PV", "EV", "AC", "CV ($)", "CV (%)", "SV ($)", "SV (%)", "CPI", "SPI"]
        vals = [
            money(row["PV"]), money(row["EV"]), money(row["AC"]),
            money(row["CV"]), _pct(row["CV"], row["EV"]),
            money(row["SV"]), _pct(row["SV"], row["PV"]),
            f"{row['CPI']:.3f}", f"{row['SPI']:.3f}",
        ]
        bad = set()
        if row["CV"] < 0:
            bad |= {3, 4}
        if row["SV"] < 0:
            bad |= {5, 6}
        if row["Cost_Flag"]:
            bad.add(7)
        if row["Schedule_Flag"]:
            bad.add(8)
        _table(doc, heads, vals, bad=bad)

        # Current period
        if _num(row, "CP_PV") is not None:
            _label(doc, "CURRENT PERIOD")
            cp_cpi, cp_spi = _num(row, "CP_CPI"), _num(row, "CP_SPI")
            heads = ["PV", "EV", "AC", "CV ($)", "SV ($)", "CPI", "SPI"]
            vals = [
                money(row["CP_PV"]), money(row["CP_EV"]), money(row["CP_AC"]),
                money(row["CP_CV"]), money(row["CP_SV"]),
                "N/A" if cp_cpi is None else f"{cp_cpi:.3f}",
                "N/A" if cp_spi is None else f"{cp_spi:.3f}",
            ]
            bad = set()
            if row["CP_CV"] < 0:
                bad.add(3)
            if row["CP_SV"] < 0:
                bad.add(4)
            if cp_cpi is not None and cp_cpi < 0.90:
                bad.add(5)
            if cp_spi is not None and cp_spi < 0.90:
                bad.add(6)
            _table(doc, heads, vals, bad=bad)

        # At completion
        if _num(row, "EAC") is not None:
            _label(doc, "AT COMPLETION")
            lre, tcpi = _num(row, "LRE"), _num(row, "TCPI_LRE")
            heads = ["BAC", "% Complete", "EAC", "ETC", "VAC ($)", "VAC (%)", "LRE", "TCPI to LRE"]
            vals = [
                money(row["BAC"]), f"{row['Pct_Complete']:.1f}%", money(row["EAC"]),
                money(row["ETC"]), money(row["VAC"]), f"{row['VAC_Pct']:+.1f}%",
                money(lre), "N/A" if tcpi is None else f"{tcpi:.3f}",
            ]
            bad = set()
            if row["VAC"] < 0:
                bad |= {4, 5}
            if row.get("LRE_Optimistic"):
                bad.add(6)
            _table(doc, heads, vals, bad=bad)
        _gap(doc, 4)

        line = doc.add_paragraph()
        run = line.add_run(f"Variance type: {VARIANCE_LABEL[kind]}  |  Drafted by: {r['mode']}")
        run.bold = True
        _gap(doc, 2)

        _box(doc, "1.  PROBLEM / ROOT CAUSE", draft["root_cause"])
        _box(doc, "2.  IMPACT (PROGRAM, SCHEDULE, ESTIMATE AT COMPLETION)", draft["impact"])
        _box(doc, "3.  CORRECTIVE ACTION PLAN", draft["corrective_action"])
        _box(doc, "4.  ESTIMATE AT COMPLETION ASSESSMENT", eac_assessment(row))

        signoff = doc.add_table(rows=2, cols=4)
        signoff.style = "Table Grid"
        heads = ["Responsible Manager (CAM)", "Target Completion Date", "Status", "Program Control Review"]
        vals = ["[Name]", "[YYYY-MM-DD]", "Open - draft", "[Initials / Date]"]
        for j, (h, v) in enumerate(zip(heads, vals)):
            _shade(signoff.cell(0, j), GRAY)
            _write(signoff.cell(0, j), h, bold=True, center=True, size=8)
            _write(signoff.cell(1, j), v, center=True)

    doc.save(path)


# ------------------------------------------------------------------- main

def main() -> None:
    metrics = compute_metrics(load_data())
    flagged = flagged_packages(metrics)
    summary = program_summary(metrics)

    use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"Mode: {'LLM (Claude)' if use_llm else 'Template (no API key found)'}")
    print(f"Drafting {len(flagged)} VARs...")

    reports = []
    for row in flagged.to_dict("records"):
        draft, mode = generate_var(row, use_llm)
        reports.append({"row": row, "draft": draft, "mode": mode})
        print(f"  {row['WBS_ID']} {row['WBS_Name']}: done ({mode})")

    OUTPUT_PATH.write_text(render_markdown(reports, summary), encoding="utf-8")
    print(f"\nSaved: {OUTPUT_PATH.name}")

    if HAVE_DOCX:
        render_docx(reports, summary, DOCX_PATH)
        print(f"Saved: {DOCX_PATH.name}  (Format 5 style Word report)")
    else:
        print("Tip: run  pip install python-docx  to also get the Word report.")


if __name__ == "__main__":
    main()
