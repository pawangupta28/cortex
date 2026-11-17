import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="cortex-test-"))
os.environ.update({"CORTEX_DATA_DIR": str(_TMP / "data"), "CORTEX_ARTIFACTS_DIR": str(_TMP / "artifacts"),
                   "CORTEX_DB": str(_TMP / "data" / "cortex.duckdb"), "CORTEX_STATE_DB": str(_TMP / "data" / "state.sqlite")})
for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(k, None)   # tests always run offline and deterministic


@pytest.fixture(scope="session")
def cortex_app():
    from cortex.agents.pipeline import Cortex
    from cortex.ingest.loader import ingest
    from cortex.retrieval.index import build_index
    ingest(force_synthetic=True)
    return Cortex(corpus=build_index())
