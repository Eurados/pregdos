"""Test-wide isolation from any config file that happens to exist on the machine.

Without this, a developer's own ``~/.config/pregdos/config.toml`` -- or the
``/etc/pregdos/config.toml`` that will exist inside the pregdos container, where this suite
also runs -- silently changes the outcome of tests that never mention configuration.
"""

import pytest

from pregdos import config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path_factory):
    """Every test starts from the built-in defaults and an empty discovery stack."""
    empty = tmp_path_factory.mktemp("xdg")
    monkeypatch.delenv(config.CONFIG_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty))
    monkeypatch.setattr(config, "SYSTEM_CONFIG_PATH", empty / "absent.toml")
    config.set_config_path(None)
    yield
    config.set_config_path(None)


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write a config file and make it the whole stack, via ``$PREGDOS_CONFIG``.

    Returns the path, so a test can also point ``--config`` at it.
    """
    def _write(text: str, name: str = "config.toml"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        monkeypatch.setenv(config.CONFIG_ENV, str(path))
        config.reset_cache()
        return path

    return _write
