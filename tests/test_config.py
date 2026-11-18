from pathlib import Path

import pytest

from mtg_card_bot.config import MTGConfig, load_env_file


def test_load_env_file_and_config_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in [
        "MTG_DISCORD_TOKEN",
        "MTG_COMMAND_PREFIX",
        "MTG_LOG_LEVEL",
        "MTG_JSON_LOGGING",
        "MTG_COMMAND_COOLDOWN",
    ]:
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'MTG_DISCORD_TOKEN="test-token"',
                "MTG_COMMAND_PREFIX=?",
                "MTG_LOG_LEVEL=WARNING",
                "MTG_JSON_LOGGING=true",
                "MTG_COMMAND_COOLDOWN=3.5",
            ]
        ),
        encoding="utf-8",
    )

    load_env_file(env_file)
    cfg = MTGConfig()

    assert cfg.discord_token == "test-token"
    assert cfg.command_prefix == "?"
    assert cfg.log_level == "warning"
    assert cfg.json_logging is True
    assert cfg.command_cooldown == 3.5

    cfg.validate_config()


def test_validate_config_rejects_invalid_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MTG_DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("MTG_LOG_LEVEL", "loud")

    cfg = MTGConfig()

    with pytest.raises(ValueError, match="Invalid log level"):
        cfg.validate_config()


def test_config_rejects_invalid_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MTG_DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("MTG_COMMAND_BURST", "0")

    with pytest.raises(ValueError, match="MTG_COMMAND_BURST"):
        MTGConfig().validate_config()


def test_config_new_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MTG_COMMAND_BURST", "MTG_CACHE_TTL", "MTG_SYNC_COMMANDS"):
        monkeypatch.delenv(key, raising=False)

    cfg = MTGConfig()

    assert cfg.command_burst == 3
    assert cfg.cache_ttl == 21600
    assert cfg.sync_commands is True
