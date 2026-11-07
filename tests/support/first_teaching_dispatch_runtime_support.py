"""Shared first teaching dispatch runtime fixtures and assertions; no collected scenarios."""
import pytest


@pytest.fixture(autouse=True)
def controller_clock(path_app,monkeypatch):
    from kanamemory import app as app_module, domain, kana_learning_intro
    # The inherited learning_app fixture installs the unified business clock.
    monkeypatch.setattr(domain,'utc_now',lambda:path_app._test_clock[0])
    monkeypatch.setattr(kana_learning_intro,'_utc',lambda value=None:value or path_app._test_clock[0])
