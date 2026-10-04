"""Shared configuration of the deployment script tests."""

import os
import sys
from pathlib import Path


# Tests never write log files (must be set before app.logger is imported).
os.environ.setdefault("OPENMANUS_LOG_DIR", "")

DEPLOY_DIR = Path(__file__).resolve().parents[1]
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))
