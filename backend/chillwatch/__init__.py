"""ChillWatch data, physics, ML and station-detection layer."""

import os

# OpenMP threads (LightGBM) that spin while waiting stall badly when another job shares the CPU
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
