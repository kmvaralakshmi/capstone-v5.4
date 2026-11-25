"""Import first in every test module: sends all agent output to a throw-away folder."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ESG_OUT_DIR", tempfile.mkdtemp(prefix="esg_test_"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
