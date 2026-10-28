"""Gemini 3.5 Flash-Lite is offered beside Claude in API mode, with its own key.

No test here touches the network: the provider takes the function that posts, and each
test hands it a fake that answers the way Google's API does.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from atlas.providers import api_choice, keystore
from atlas.providers.base import AuditVerdict, ProviderRequest
from atlas.providers.errors import FailureKind
from atlas.providers.gemini_provider import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_KEY_ENV,
    GeminiProvider,
    gemini_role_models,
)
from atlas.state.models import Role

SCHEMA = {"type": "object", "properties": {"word": {"type": "string"}},
          "required": ["word"], "additionalProperties": False}


def _answer(text: str, *, finish: str = "STOP", thought: str | None = None) -> str:
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return json.dumps({
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "modelVersion": DEFAULT_GEMINI_MODEL,
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3,
                          "totalTokenCount": 13},
    })


class _Fake:
    """Answers in turn with each (status, body) it was given, and records what it was sent."""

    def __init__(self, *answers: tuple[int, str]) -> None:
        self.answers = list(answers)
        self.sent: list[tuple[str, dict, dict]] = []

    async def __call__(self, url, headers, payload, timeout):
        self.sent.append((url, headers, payload))
        return self.answers.pop(0)


def _ask(provider, *, schema=None, role=Role.ADVISOR):
    return asyncio.run(provider.invoke(ProviderRequest(
        role=role, prompt="Say ready.", model=DEFAULT_GEMINI_MODEL, schema=schema,
        timeout_s=10.0)))


def test_every_seat_is_flash_lite_by_default(monkeypatch):
    monkeypatch.delenv("ATLAS_GEMINI_MODEL", raising=False)
    pins = gemini_role_models()
    assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert {model for _, model in pins.values()} == {"gemini-3.5-flash-lite"}
    assert Role.WORLD_SIM in pins and Role.ENGINE not in pins


def test_a_structured_answer_uses_the_api_json_mode_and_the_key_header():
    fake = _Fake((200, _answer('{"word": "ready"}')))
    result = _ask(GeminiProvider("AIza-test", post=fake), schema=SCHEMA)
    assert result.ok and result.data == {"word": "ready"}
    url, headers, payload = fake.sent[0]
    assert url.endswith(f"/models/{DEFAULT_GEMINI_MODEL}:generateContent")
    assert headers["x-goog-api-key"] == "AIza-test"
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == SCHEMA
    assert result.audit.verdict is AuditVerdict.MATCH


def test_a_refused_schema_is_retried_as_json_in_the_prompt():
    refused = json.dumps({"error": {"code": 400, "message": "Invalid response_json_schema"}})
    fake = _Fake((400, refused), (200, _answer('```json\n{"word": "ready"}\n```')))
    result = _ask(GeminiProvider("AIza-test", post=fake), schema=SCHEMA)
    assert result.ok and result.data == {"word": "ready"}
    retry = fake.sent[1][2]
    assert "responseJsonSchema" not in retry["generationConfig"]
    assert "JSON schema" in retry["contents"][0]["parts"][0]["text"]


def test_thought_summaries_are_not_the_answer():
    fake = _Fake((200, _answer("ready", thought="Let me think about this...")))
    result = _ask(GeminiProvider("AIza-test", post=fake))
    assert result.ok and result.text == "ready"


def test_a_bad_key_is_reported_as_a_bad_key():
    bad = json.dumps({"error": {"code": 400, "message": "API key not valid. Please pass a "
                                                         "valid API key."}})
    result = _ask(GeminiProvider("nope", post=_Fake((400, bad))))
    assert not result.ok and result.failure.kind is FailureKind.NOT_AUTHENTICATED
    assert "Gemini" in result.failure.message


def test_an_overloaded_api_is_retried_before_it_is_a_failure():
    busy = json.dumps({"error": {"code": 503, "message": "The model is overloaded."}})
    fake = _Fake((503, busy), (200, _answer("ready")))
    assert _ask(GeminiProvider("AIza-test", post=fake, backoff=0.0)).ok
    fake = _Fake((503, busy), (503, busy), (503, busy))
    result = _ask(GeminiProvider("AIza-test", post=fake, backoff=0.0))
    assert not result.ok and result.failure.kind is FailureKind.CRASHED
    assert len(fake.sent) == 3


def test_a_cut_off_answer_says_so():
    result = _ask(GeminiProvider("AIza-test", post=_Fake((200, _answer(
        '{"word": "rea', finish="MAX_TOKENS")))), schema=SCHEMA)
    assert result.failure.kind is FailureKind.MALFORMED_OUTPUT
    assert "output limit" in result.failure.message


def test_no_key_is_a_sentence_not_a_crash(monkeypatch, keys):
    result = _ask(GeminiProvider(post=_Fake()))
    assert result.failure.kind is FailureKind.NOT_AUTHENTICATED


# ------------------------------------------------------------------- keys and the choice

@pytest.fixture
def keys(tmp_path, monkeypatch):
    """A keystore with no system keychain, writing into a folder of the test's own."""
    from atlas.config import paths

    monkeypatch.setattr(paths, "app_data_root", lambda: tmp_path)
    monkeypatch.setattr(keystore, "_keyring", lambda: None)
    for name in (GEMINI_KEY_ENV, "ATLAS_ANTHROPIC_API_KEY", api_choice.CHOICE_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_the_two_keys_are_kept_apart(keys):
    keystore.store_key("sk-ant-claude")
    keystore.store_key("AIza-gemini", "gemini-api-key")
    assert keystore.stored_key() == "sk-ant-claude"
    assert keystore.stored_key("gemini-api-key") == "AIza-gemini"
    # Claude's file keeps its old name, so a key saved before Gemini existed is found.
    assert (keys / "api_key").read_text() == "sk-ant-claude"
    keystore.forget_key("gemini-api-key")
    assert keystore.stored_key("gemini-api-key") == ""
    assert keystore.stored_key() == "sk-ant-claude"


def test_the_choice_follows_the_keys_until_one_is_made(keys):
    assert api_choice.chosen() == "claude"
    keystore.store_key("AIza-gemini", "gemini-api-key")
    assert api_choice.chosen() == "gemini"          # the only key there is
    keystore.store_key("sk-ant-claude")
    assert api_choice.chosen() == "claude"          # Claude first when both are saved
    api_choice.set_chosen("gemini")
    assert api_choice.chosen() == "gemini"          # an explicit choice wins over that
    os.environ[api_choice.CHOICE_ENV] = "claude"
    try:
        assert api_choice.chosen() == "claude"      # and the session variable over both
    finally:
        del os.environ[api_choice.CHOICE_ENV]
    assert isinstance(api_choice.api_provider(), GeminiProvider)
    assert api_choice.role_models()[Role.WORLD_SIM][1] == DEFAULT_GEMINI_MODEL


def test_the_app_opens_in_api_mode_with_either_key(keys):
    import importlib.util
    from pathlib import Path

    entry = Path(__file__).resolve().parents[1] / "packaging" / "atlas_entry.py"
    spec = importlib.util.spec_from_file_location("atlas_entry_under_test", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._double_click() == ["start", "--no-ai"]
    keystore.store_key("AIza-gemini", "gemini-api-key")
    assert module._double_click() == ["start", "--api"]


def test_settings_switches_the_key_box_to_gemini(keys):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from atlas.ui.preferences import Preferences
    from atlas.ui.settings import SettingsScreen
    from atlas.ui.theme import DARK

    QApplication.instance() or QApplication([])
    sheet = SettingsScreen(Preferences(), theme=DARK)
    assert sheet._key_label.text() == "Anthropic API key"
    sheet._choice_chips["gemini"].clicked.emit()
    assert sheet._key_label.text() == "Gemini API key"
    assert sheet._key_box.placeholderText().startswith("AIza")
    assert api_choice.chosen() == "gemini"
    sheet._key_box.setText("AIza-from-the-box")
    sheet._save_key()
    assert keystore.stored_key("gemini-api-key") == "AIza-from-the-box"
    assert keystore.stored_key() == ""              # Claude's slot untouched
    assert sheet._key_box.text() == ""              # never left on screen
