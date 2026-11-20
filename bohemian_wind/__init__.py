"""Bohemian Wind: from met mast data to a permitting pack. Step 1 (the data checks) is also known as Bohemian Wind QC."""

__version__ = "0.2.7-dev"
APP_NAME = "Bohemian Wind QC"          # printed on the data-check reports
SUITE_NAME = "Bohemian Wind"
ORG = "Bohemian Renewables"

# [COMPLIANCE] Printed on every data-check report. Do not remove or soften.
DISCLAIMER = (
    "Data-quality screening prepared by Bohemian Renewables with open-source tools. "
    "It is not an accredited or bankable assessment and must not be used for financing decisions."
)
# [COMPLIANCE] Shown at the foot of the application at all times. Do not remove or soften.
APP_NOTICE = (
    "Developer estimates prepared by Bohemian Renewables with open-source tools. Nothing here is an accredited or "
    "bankable assessment, and it must not be used for financing decisions."
)
