"""The root-server console script must import against the pinned mcp SDK."""

import importlib
import shutil
import sys
from pathlib import Path

import root_kg.paths as paths


def test_server_imports_with_a_fresh_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ROOT_KG_HOME", str(tmp_path))
    shutil.copy(Path(paths.__file__).parent / "config.example.yaml", tmp_path / "config.yaml")
    importlib.reload(paths)
    try:
        if "root_kg.server" in sys.modules:
            server = importlib.reload(sys.modules["root_kg.server"])
        else:
            server = importlib.import_module("root_kg.server")
        assert callable(server.run)
    finally:
        monkeypatch.delenv("ROOT_KG_HOME")
        importlib.reload(paths)
