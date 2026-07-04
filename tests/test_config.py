"""Tests for configuration loading."""

from __future__ import annotations

from paper_assistant.config import Config, load_config


def test_qmd_xdg_roots_default_to_data_dir(tmp_path):
    config = Config(data_dir=tmp_path)

    assert config.resolved_qmd_cache_home == tmp_path / ".qmd-cache"
    assert config.resolved_qmd_config_home == tmp_path / ".qmd-config"


def test_load_config_reads_qmd_xdg_root_env(tmp_path, monkeypatch):
    cache_home = tmp_path / "cache"
    config_home = tmp_path / "config"
    monkeypatch.setenv("PAPER_ASSIST_QMD_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("PAPER_ASSIST_QMD_CONFIG_HOME", str(config_home))

    config = load_config()

    assert config.qmd_cache_home == cache_home
    assert config.qmd_config_home == config_home
    assert config.resolved_qmd_cache_home == cache_home
    assert config.resolved_qmd_config_home == config_home
