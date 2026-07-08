"""Streamlit entry point for the schema patch review UI.

Run from the repo root:

    streamlit run src/review_app.py -- --consensus-dir outputs/private_health/refine/round_1/consensus
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.refine.human_review.ui import main


main()
