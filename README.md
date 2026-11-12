# Apex Baseline

Earned value management (EVM) engine and dashboard. Computes CPI, SPI, CV, SV,
EAC, ETC, VAC, TCPI; flags variance threshold breaches; drafts Variance Analysis
Reports in an IPMR / CPR Format 5 style.

Built on public standards: EIA-748, the DoD IPMR (DI-MGMT-81861), and standard
EVM formulas. All data in this repository is synthetic.

## Run locally
    pip install -r requirements.txt
    streamlit run app.py

## Private mode (your machine only)
Enables CSV upload and Claude-drafted reports (needs `pip install anthropic`
and an ANTHROPIC_API_KEY):
    set APEX_PRIVATE_MODE=1        (Windows cmd)
    $env:APEX_PRIVATE_MODE="1"     (PowerShell)
