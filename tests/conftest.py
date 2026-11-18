import pytest


@pytest.fixture(autouse=True)
def czech_windows(monkeypatch):
    """Fresh settings start in Czech whatever the language of the machine running the tests."""
    monkeypatch.setattr("adacar.config.system_language", lambda: "cs")
