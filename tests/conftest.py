"""Keep automated contract tests isolated from the user's preview data."""

import os
import shutil
import tempfile
import time
import gc
from pathlib import Path


_TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="ai-svwf-pytest-"))
os.environ["DATABASE_PATH"] = str(_TEST_RUNTIME / "test.sqlite3")
os.environ["OUTPUT_DIR"] = str(_TEST_RUNTIME / "outputs")
os.environ["ASSET_DIR"] = str(_TEST_RUNTIME / "assets")
os.environ["FEISHU_SYNC_MODE"] = "local"
os.environ["MOCK_MODE"] = "true"
os.environ["MODEL_PROVIDER"] = "mock"


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Remove only the unique temporary directory created by this test process."""
    gc.collect()
    for _ in range(10):
        try:
            shutil.rmtree(_TEST_RUNTIME)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.05)
