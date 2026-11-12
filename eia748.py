"""
EIA-748 guideline self-assessment checklist.

The standard (EIA-748, Earned Value Management Systems) groups 32 guidelines into
five categories. The summaries below are short paraphrases written for this
project, not the text of the standard. Always refer to the official standard and
your contract for the authoritative wording.

"Support" describes what Apex Baseline helps demonstrate with its own data and
screens. It is NOT a compliance claim: a contractor's EVMS is judged on its
processes, systems and an audit, most of which live outside any single tool.

    Supported    the tool calculates, shows or records this directly
    Partial      the tool covers part of it
    Not covered  a process or organizational matter, or planned future work
"""

ORG = "Organization"
PSB = "Planning, Scheduling & Budgeting"
ACC = "Accounting Considerations"
ANA = "Analysis & Management Reports"
REV = "Revisions & Data Maintenance"

# (number, category, summary, support, how it is covered)
GUIDELINES = [
    (1, ORG, "Define all authorized work with a work breakdown structure.",
     "Partial", "Every record is tied to a WBS ID and name. The tool does not author the WBS."),
    (2, ORG, "Identify the organization responsible for the work, including major subcontractors.",
     "Not covered", "No organizational breakdown in the current data."),
    (3, ORG, "Integrate planning, scheduling, budgeting, work authorization and cost collection.",
     "Partial", "Budget, plan, earned value and actual cost are tied together by WBS."),
    (4, ORG, "Identify who is responsible for controlling overhead (indirect) costs.",
     "Not covered", "Organizational and process matter."),
    (5, ORG, "Integrate the WBS and the organization so performance can be measured by either.",
     "Not covered", "Needs an organizational breakdown, which is not in the data yet."),
    (6, PSB, "Schedule the work in sequence and show the key dependencies between tasks.",
     "Not covered", "Needs an integrated master schedule. Planned future work."),
    (7, PSB, "Choose objective measures of progress such as milestones or technical indicators.",
     "Not covered", "Earned value is taken as given. Earning rules are not modeled."),
    (8, PSB, "Maintain a time-phased budget baseline at the control account level.",
     "Supported", "Monthly planned value curve for every work package."),
    (9, PSB, "Budget by significant cost element, such as labor and material.",
     "Partial", "Labor hours and dollars are shown. Material and other elements are not."),
    (10, PSB, "Budget discrete work packages in dollars or hours; plan far-term work in planning packages.",
     "Partial", "Work package dollars and labor hours are modeled. Planning packages are not."),
    (11, PSB, "Make work package and planning package budgets add up to the control account budget.",
     "Not covered", "Single reporting level in the current data."),
    (12, PSB, "Treat only truly unmeasurable work as level of effort, and budget it by time.",
     "Not covered", "Level of effort is not modeled."),
    (13, PSB, "Set overhead budgets and reflect planned overhead in program budgets.",
     "Not covered", "Indirect budgets are outside the current data."),
    (14, PSB, "Identify management reserve and undistributed budget.",
     "Partial", "Management reserve and its use are tracked. Undistributed budget is not."),
    (15, PSB, "Reconcile the target cost to the sum of internal budgets and management reserve.",
     "Partial", "Budget walk reconciles original and current budget, with reserve shown."),
    (16, ACC, "Record direct costs consistently with the budgets, in the books of account.",
     "Partial", "Actual cost uses the same WBS as budget. Integrity checks test the pairing."),
    (17, ACC, "Roll direct costs up the WBS without splitting a control account across elements.",
     "Supported", "One WBS element per record, rolled up to program level."),
    (18, ACC, "Roll direct costs up the organization without splitting a control account.",
     "Not covered", "Needs an organizational breakdown."),
    (19, ACC, "Record all indirect costs that will be allocated to the contract.",
     "Not covered", "Outside the current data."),
    (20, ACC, "Identify unit, equivalent unit or lot costs when needed.",
     "Not covered", "Not modeled."),
    (21, ACC, "Material accounting: accurate cost collection, timing consistent with earned value, accountability.",
     "Not covered", "Not modeled."),
    (22, ANA, "At least monthly, compare planned vs earned (schedule variance) and earned vs actual (cost variance).",
     "Supported", "CV, SV, CPI and SPI for every package, plus Earned Schedule in months."),
    (23, ANA, "Identify significant cost and schedule variances and explain the reasons.",
     "Supported", "Threshold flags and drafted Variance Analysis Reports."),
    (24, ANA, "Identify budgeted and actual indirect costs and summarize variances monthly.",
     "Not covered", "Indirect costs are outside the current data."),
    (25, ANA, "Summarize data and variances up through the WBS and organization for reporting.",
     "Partial", "Program-level rollup by WBS. No organizational rollup."),
    (26, ANA, "Take and track management action based on earned value information.",
     "Partial", "Corrective action section and sign-off block in each report."),
    (27, ANA, "Keep revised estimates at completion current and compare them to the baseline.",
     "Supported", "EAC range by three methods, compared with the manager's estimate and budget."),
    (28, REV, "Incorporate authorized changes promptly and record their effect on budgets and schedules.",
     "Supported", "Baseline change log feeds the current budget."),
    (29, REV, "Reconcile current budgets to prior budgets for changes and replanning.",
     "Supported", "Budget walk from original to current budget by work package."),
    (30, REV, "Control retroactive changes to actual cost, earned value or budget.",
     "Partial", "Integrity checks flag cumulative values that go backward."),
    (31, REV, "Prevent budget revisions other than authorized changes.",
     "Partial", "Only approved changes enter the budget. Pending changes are kept separate."),
    (32, REV, "Document changes to the performance measurement baseline.",
     "Supported", "Change log records ID, date, type, amount, status and description."),
]

assert len(GUIDELINES) == 32
assert [g[0] for g in GUIDELINES] == list(range(1, 33))
