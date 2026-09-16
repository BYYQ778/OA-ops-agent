import os
import tempfile
from pathlib import Path

_TEST_DATA_DIRECTORY = tempfile.TemporaryDirectory(prefix="oa-ops-tests-")
os.environ["OA_DATA_DIR"] = str(Path(_TEST_DATA_DIRECTORY.name).resolve())
os.environ["OA_ENABLE_BACKGROUND_STARTUP"] = "0"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
