"""Constants moved from ``tracegraph.stage1``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import math
import shutil
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

_NORMAL_TERMINATIONS = {"user_stop", "agent_stop"}

_INFRASTRUCTURE_TERMINATION = "infrastructure_error"
