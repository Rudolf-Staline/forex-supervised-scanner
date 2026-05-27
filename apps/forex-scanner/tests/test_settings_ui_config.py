import json
from unittest import mock
import pytest
from app.config.settings import AppSettings
from app.ui.streamlit_app import _configuration_page

# Mock minimal settings wrapper
class MinimalMockStreamlit:
    def __init__(self):
        self.error_calls = []
        self.success_calls = []
        self._button_responses = {}
        self._file_uploader_response = None

    def subheader(self, text): pass
    def caption(self, text): pass
    def markdown(self, text): pass
    def write(self, text): pass
    def error(self, text): self.error_calls.append(text)
    def success(self, text): self.success_calls.append(text)
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass
    def columns(self, num): return [self for _ in range(num)]
    def expander(self, text): return self
    def table(self, df): pass
    def form(self, key): return self
    def checkbox(self, label, value=False): return value
    def selectbox(self, label, options, index=0, key=None): return options[index]
    def number_input(self, label, value=0.0, min_value=None): return value
    def form_submit_button(self, label): return False

    def button(self, label, key=None):
        return self._button_responses.get(label, False)

    def download_button(self, label, data, file_name, mime): pass
    def file_uploader(self, label, type):
        return self._file_uploader_response

    def spinner(self, text): return self
    def code(self, text): pass
    def info(self, text): pass
    def dataframe(self, df): pass

@pytest.fixture
def base_settings():
    from app.config.settings import load_settings
    # The default config contains all necessary structural requirements (weights, atr stops, auth).
    return load_settings()

def test_config_import_rejects_live_trading(monkeypatch, base_settings):
    st_mock = MinimalMockStreamlit()
    monkeypatch.setattr("app.ui.streamlit_app.st", st_mock)

    # We create a payload that attempts to sneak in live trading
    malicious_payload = base_settings.model_dump(mode="json")
    malicious_payload["broker"]["live_enabled"] = True
    malicious_payload["execution"]["mode"] = "broker_live"
    malicious_payload["execution_capabilities"]["broker_live_enabled"] = True

    import io
    fake_file = io.BytesIO(json.dumps(malicious_payload).encode("utf-8"))

    st_mock._file_uploader_response = fake_file
    st_mock._button_responses["Valider et appliquer"] = True

    # Run the UI function
    _configuration_page(base_settings)

    assert any("interdite via l'UI" in call for call in st_mock.error_calls)
    assert not any("succès" in call for call in st_mock.success_calls)

def test_config_import_accepts_safe_payload(monkeypatch, base_settings):
    st_mock = MinimalMockStreamlit()
    monkeypatch.setattr("app.ui.streamlit_app.st", st_mock)
    monkeypatch.setattr("app.ui.streamlit_app.save_settings", mock.MagicMock())

    safe_payload = base_settings.model_dump(mode="json")
    # Change a safe value
    safe_payload["adaptive_thresholds"]["enabled"] = True

    import io
    fake_file = io.BytesIO(json.dumps(safe_payload).encode("utf-8"))

    st_mock._file_uploader_response = fake_file
    st_mock._button_responses["Valider et appliquer"] = True

    # Run the UI function
    _configuration_page(base_settings)

    assert any("importée avec succès" in call for call in st_mock.success_calls)
    assert not st_mock.error_calls
