"""Tests for configuration parsing and validation."""

import importlib

import pytest

from inbox2pod import config as config_module
from inbox2pod.config import Config


class TestEnvParsers:
    def test_bool_accepts_the_usual_yeses(self, monkeypatch):
        for value in ("1", "true", "TRUE", "yes", "on", " true "):
            monkeypatch.setenv("SOME_FLAG", value)
            assert config_module._bool("SOME_FLAG") is True

    def test_bool_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("SOME_FLAG", raising=False)
        assert config_module._bool("SOME_FLAG") is False

    def test_bool_rejects_nonsense(self, monkeypatch):
        monkeypatch.setenv("SOME_FLAG", "maybe")
        assert config_module._bool("SOME_FLAG") is False

    def test_int_parses_numbers(self, monkeypatch):
        monkeypatch.setenv("SOME_NUMBER", " 42 ")
        assert config_module._int("SOME_NUMBER", "1") == 42

    def test_int_names_the_variable_when_it_cannot_parse(self, monkeypatch):
        monkeypatch.setenv("SOME_NUMBER", "lots")
        with pytest.raises(ValueError, match="SOME_NUMBER must be an integer, got 'lots'"):
            config_module._int("SOME_NUMBER", "1")

    def test_int_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.delenv("SOME_NUMBER", raising=False)
        assert config_module._int("SOME_NUMBER", "7") == 7

    def test_csv_splits_and_strips(self, monkeypatch):
        monkeypatch.setenv("SOME_LIST", "a, b ,,c,")
        assert config_module._csv("SOME_LIST") == ["a", "b", "c"]

    def test_lower_csv_lowercases(self, monkeypatch):
        monkeypatch.setenv("SOME_LIST", "Money@Example.COM, TLDR.com")
        assert config_module._lower_csv("SOME_LIST") == ["money@example.com", "tldr.com"]

    def test_empty_csv_is_an_empty_list(self, monkeypatch):
        monkeypatch.setenv("SOME_LIST", " , , ")
        assert config_module._csv("SOME_LIST") == []


class TestReloadedConfig:
    """Env parsing only happens at import, so reload the module to check it.

    Every other module keeps a reference to the Config class it imported, so the
    original class is put back afterwards to keep them all in agreement.
    """

    @pytest.fixture(autouse=True)
    def restore_config_class(self):
        original = config_module.Config
        yield
        config_module.Config = original

    def reload(self, monkeypatch, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config_module)

    def test_defaults(self, monkeypatch):
        for name in ("IMAP_PORT", "TTS_MODEL", "FEED_TOKEN", "EPISODE_RETENTION_DAYS", "IMAP_AFTER_ACTION"):
            monkeypatch.delenv(name, raising=False)
        module = importlib.reload(config_module)

        assert module.Config.IMAP_PORT == 993
        assert module.Config.TTS_MODEL == "kokoro"
        assert module.Config.FEED_TOKEN == ""
        assert module.Config.EPISODE_RETENTION_DAYS is None
        assert module.Config.IMAP_AFTER_ACTION == "mark_seen"

    def test_overrides(self, monkeypatch):
        module = self.reload(
            monkeypatch,
            IMAP_HOST="imap.example.com",
            IMAP_PORT="1143",
            SMTP_ALLOWED_RCPT="X7KQ2M@Pod.Example, secret",
            SENDER_ALLOWLIST="Money Stuff <money@ledger.example>, tldr.com",
            EPISODE_RETENTION_DAYS="30",
            IMAP_AFTER_ACTION="Move:Processed",
            FEED_TOKEN="  secret-token  ",
        )

        assert module.Config.IMAP_PORT == 1143
        assert module.Config.SMTP_ALLOWED_RCPT == ["x7kq2m@pod.example", "secret"]
        assert module.Config.EPISODE_RETENTION_DAYS == 30
        assert module.Config.IMAP_AFTER_ACTION == "move:Processed"
        assert module.Config.FEED_TOKEN == "secret-token"

    def test_relative_storage_paths_are_made_absolute(self, monkeypatch):
        module = self.reload(monkeypatch, RAW_STORAGE_PATH="data/raw", MP3_STORAGE_PATH="/srv/pod/audio")

        assert module.Config.RAW_STORAGE_PATH.startswith("/")
        assert module.Config.MP3_STORAGE_PATH == "/srv/pod/audio"


@pytest.fixture
def pristine(monkeypatch):
    """Restore the knobs each validation test cares about."""
    for name, value in (
        ("IMAP_HOST", "imap.test.example"),
        ("IMAP_USER", "pod@test.example"),
        ("IMAP_PASSWORD", "pw"),
        ("SMTP_ENABLED", False),
        ("IMAP_AFTER_ACTION", "mark_seen"),
        ("TTS_WOL_ENABLED", False),
        ("TTS_WOL_MAC", ""),
    ):
        monkeypatch.setattr(Config, name, value)
    return Config


class TestValidate:
    def test_imap_config_is_enough(self, pristine):
        pristine.validate()

    def test_smtp_alone_is_enough(self, pristine, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_HOST", None)
        monkeypatch.setattr(Config, "SMTP_ENABLED", True)
        pristine.validate()

    def test_no_ingest_source_at_all_fails(self, pristine, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_HOST", None)
        with pytest.raises(AssertionError, match="No ingest source configured"):
            pristine.validate()

    def test_imap_without_credentials_fails(self, pristine, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_PASSWORD", None)
        with pytest.raises(AssertionError, match="IMAP_USER/IMAP_PASSWORD is missing"):
            pristine.validate()

    def test_a_nonsense_after_action_fails(self, pristine, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_AFTER_ACTION", "incinerate")
        with pytest.raises(AssertionError, match="IMAP_AFTER_ACTION"):
            pristine.validate()

    @pytest.mark.parametrize("action", ["mark_seen", "none", "delete", "move:Processed"])
    def test_accepted_after_actions(self, pristine, monkeypatch, action):
        monkeypatch.setattr(Config, "IMAP_AFTER_ACTION", action)
        pristine.validate()

    def test_wake_on_lan_needs_a_mac(self, pristine, monkeypatch):
        monkeypatch.setattr(Config, "TTS_WOL_ENABLED", True)
        with pytest.raises(AssertionError, match="TTS_WOL_MAC"):
            pristine.validate()


class TestImapEnabled:
    def test_all_three_parts_are_required(self, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_HOST", "imap.test")
        monkeypatch.setattr(Config, "IMAP_USER", "pod@test")
        monkeypatch.setattr(Config, "IMAP_PASSWORD", "pw")
        assert Config.imap_enabled() is True

        monkeypatch.setattr(Config, "IMAP_PASSWORD", "")
        assert Config.imap_enabled() is False

    def test_nothing_configured(self, monkeypatch):
        monkeypatch.setattr(Config, "IMAP_HOST", None)
        assert Config.imap_enabled() is False


class TestSenderAllowed:
    def test_an_empty_allowlist_accepts_everything(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", [])
        assert Config.sender_allowed("anyone@example.com") is True
        assert Config.sender_allowed(None) is True

    def test_bare_domain_matches(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["dailyledger.example"])
        assert Config.sender_allowed("news@dailyledger.example") is True

    def test_subdomains_match_a_bare_domain(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["example.com"])
        assert Config.sender_allowed("news@mail.example.com") is True

    def test_a_suffix_that_is_not_a_domain_boundary_does_not_match(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["example.com"])
        assert Config.sender_allowed("news@notexample.com") is False

    def test_at_prefixed_entry_matches_only_the_domain(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["@dailyledger.example"])
        assert Config.sender_allowed("news@dailyledger.example") is True
        assert Config.sender_allowed("news@other.example") is False

    def test_full_address_matches_exactly(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["news@dailyledger.example"])
        assert Config.sender_allowed("news@dailyledger.example") is True
        assert Config.sender_allowed("other@dailyledger.example") is False

    def test_matching_ignores_case_and_padding(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["news@dailyleDGER.example"])
        assert Config.sender_allowed("  NEWS@dailyledger.EXAMPLE ") is True

    def test_no_sender_when_an_allowlist_is_set(self, monkeypatch):
        monkeypatch.setattr(Config, "SENDER_ALLOWLIST", ["dailyledger.example"])
        assert Config.sender_allowed(None) is False
        assert Config.sender_allowed("") is False


@pytest.mark.parametrize("origin", ["ftp://pod.example", "https://pod.example/path", "https://pod.example?token=x"])
def test_invalid_feed_origin_is_rejected(pristine, monkeypatch, origin):
    monkeypatch.setattr(Config, "FEED_BASE_URL", origin)
    with pytest.raises(AssertionError, match="FEED_BASE_URL"):
        pristine.validate()


@pytest.mark.parametrize("name", ["TTS_CONNECT_TIMEOUT", "TTS_READ_TIMEOUT"])
def test_tts_timeouts_must_be_positive(pristine, monkeypatch, name):
    monkeypatch.setattr(Config, name, 0)
    with pytest.raises(AssertionError, match="timeouts must be positive"):
        pristine.validate()
