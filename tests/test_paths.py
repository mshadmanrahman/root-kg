"""ROOT_KG_HOME, checkout root or ~/.root-kg: which one wins."""

import importlib
from pathlib import Path

import root_kg.paths as paths


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ROOT_KG_HOME", str(tmp_path / "custom"))
    reloaded = importlib.reload(paths)
    try:
        assert reloaded.PROJECT_ROOT == tmp_path / "custom"
    finally:
        monkeypatch.delenv("ROOT_KG_HOME")
        importlib.reload(paths)


def test_checkout_uses_repo_root(tmp_path):
    checkout = tmp_path / "root-kg"
    package_dir = checkout / "root_kg"
    package_dir.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\n")
    assert paths.default_home(package_dir) == checkout


def test_site_packages_falls_back_to_dot_root_kg(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    package_dir = tmp_path / "site-packages" / "root_kg"
    package_dir.mkdir(parents=True)
    assert paths.default_home(package_dir) == tmp_path / ".root-kg"


def test_init_templates_ship_inside_the_package():
    package_dir = Path(paths.__file__).parent
    assert (package_dir / "config.example.yaml").is_file()
    assert (package_dir / ".env.example").is_file()
