from pathlib import Path

from src.config import load_config, tf_seconds


def test_load_real_config():
    cfg = load_config(Path("config.toml"))
    assert cfg["agent"]["dry_run"] is True
    assert cfg["timeframes"]["enabled"] == ["1m"]
    assert cfg["timeframes"]["cooldown_s"]["1m"] == 180
    assert cfg["signals"]["entry_off_r"] == 0.5


def test_tf_seconds():
    assert tf_seconds("5s") == 5
    assert tf_seconds("15s") == 15
    assert tf_seconds("1m") == 60
    assert tf_seconds("3m") == 180
    assert tf_seconds("5m") == 300
