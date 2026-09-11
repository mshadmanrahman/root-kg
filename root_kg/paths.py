"""Where ROOT keeps its config, database and logs.

Default is the repo root (the directory above this package), which is where a
git clone puts config.yaml, data/ and logs/. Set ROOT_KG_HOME to point somewhere
else, for example when the package is installed from PyPI rather than cloned.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(
    os.environ.get("ROOT_KG_HOME") or Path(__file__).resolve().parent.parent
)
