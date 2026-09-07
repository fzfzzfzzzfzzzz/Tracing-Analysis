"""Constants moved from ``tracegraph.paired``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_NORMAL_TERMINATIONS = {"user_stop", "agent_stop"}

_INFRASTRUCTURE_TERMINATIONS = {"infrastructure_error", "timeout"}
