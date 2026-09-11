"""The MCP server must list its tools and dispatch a call through the mcp 2.x handlers."""

import asyncio
import importlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import root_kg.paths as paths


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("ROOT_KG_HOME", str(tmp_path))
    shutil.copy(Path(paths.__file__).parent / "config.example.yaml", tmp_path / "config.yaml")
    importlib.reload(paths)
    try:
        if "root_kg.server" in sys.modules:
            yield importlib.reload(sys.modules["root_kg.server"])
        else:
            yield importlib.import_module("root_kg.server")
    finally:
        monkeypatch.delenv("ROOT_KG_HOME")
        importlib.reload(paths)


def test_lists_eighteen_tools_with_object_schemas(server):
    result = asyncio.run(server.on_list_tools(None, None))
    names = [tool.name for tool in result.tools]
    assert len(names) == 18
    assert len(set(names)) == 18
    assert names[0] == "root_search"
    for tool in result.tools:
        assert tool.input_schema["type"] == "object"


def test_unknown_tool_returns_text_not_exception(server, monkeypatch):
    monkeypatch.setattr(server, "get_db", lambda: None)
    monkeypatch.setattr(server, "get_embedder", lambda: None)
    params = SimpleNamespace(name="root_nope", arguments=None)
    result = asyncio.run(server.on_call_tool(None, params))
    assert result.content[0].type == "text"
    assert "root_nope" in result.content[0].text
