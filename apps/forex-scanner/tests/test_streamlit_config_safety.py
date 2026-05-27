from __future__ import annotations

import os
from unittest import mock

import pytest
from streamlit.testing.v1 import AppTest

# Use a mock object or minimal unit tests since Streamlit's AppTest is not always 100% reliable
# with complex multi-tab layouts and heavy dependencies without proper mocking.
# However, we can test the specific validations we enforce inside the page directly.

from app.config.settings import AppSettings, AdaptiveThresholdSettings
from app.ui.streamlit_app import _configuration_page

@pytest.fixture
def mock_streamlit(monkeypatch):
    """Mocks streamlit functions used by the configuration page to avoid browser requirements."""
    class MockSt:
        def __init__(self):
            self.markdown_calls = []
            self.error_calls = []
            self.success_calls = []

        def subheader(self, text): pass
        def caption(self, text): pass
        def markdown(self, text):
            self.markdown_calls.append(text)
        def write(self, text): pass
        def error(self, text):
            self.error_calls.append(text)
        def success(self, text):
            self.success_calls.append(text)
        def columns(self, num):
            return [MockSt() for _ in range(num)]
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass

        def expander(self, text): return self
        def table(self, df): pass
        def form(self, key): return self
        def checkbox(self, label, value=False): return value
        def selectbox(self, label, options, index=0, key=None): return options[index]
        def number_input(self, label, value=0.0, min_value=None): return value
        def form_submit_button(self, label): return False
        def button(self, label, key=None): return False
        def download_button(self, label, data, file_name, mime): pass
        def file_uploader(self, label, type): return None
        def spinner(self, text): return self
        def code(self, text): pass
        def info(self, text): pass
        def dataframe(self, df): pass

    mock_st = MockSt()
    monkeypatch.setattr("app.ui.streamlit_app.st", mock_st)
    return mock_st


def test_configuration_page_blocks_dangerous_env(monkeypatch, mock_streamlit):
    """If ALLOW_LIVE_TRADING is true in the environment, the UI should lock down."""
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    # We pass a minimal dummy settings object
    from app.config.settings import load_settings
    settings = load_settings()

    _configuration_page(settings)

    assert any("DANGEROUS" in call for call in mock_streamlit.markdown_calls)
    assert any("actions sensibles bloquées" in call.lower() for call in mock_streamlit.error_calls)


def test_configuration_page_shows_safe_when_paper(monkeypatch, mock_streamlit):
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("BROKER_MODE", "paper")

    from app.config.settings import load_settings
    settings = load_settings()

    _configuration_page(settings)
    assert any("SAFE" in call for call in mock_streamlit.markdown_calls)
