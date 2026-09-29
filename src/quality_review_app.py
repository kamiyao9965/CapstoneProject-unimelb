"""Local checker UI for post-extraction judge findings.

Run from the project root:

    .venv/bin/python -m streamlit run src/quality_review_app.py -- --quality-dir outputs/travel_insurance/quality_run_1
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.quality_review_ui import main


main()
