"""Project location shared by manifest resolution and local parser defaults.

Data locations and optional environment overrides belong to vertical manifests.
The former implicit search for a neighbouring ``konkrd-data`` tree is retired.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
