"""Where ROOT keeps its config, database and logs.

Resolution order:

1. ROOT_KG_HOME, when set.
2. The repo root, when this package sits inside a git checkout (the directory
   above it holds pyproject.toml). That is where a clone puts config.yaml,
   data/ and logs/.
3. ~/.root-kg otherwise, which is the case for a PyPI install.
"""

import os
from pathlib import Path


def default_home(package_dir: Path | None = None) -> Path:
    """Return the home ROOT uses when ROOT_KG_HOME is unset."""
    package_dir = package_dir or Path(__file__).resolve().parent
    checkout = package_dir.parent
    if (checkout / "pyproject.toml").exists():
        return checkout
    return Path.home() / ".root-kg"


PROJECT_ROOT = Path(os.environ.get("ROOT_KG_HOME") or default_home())
