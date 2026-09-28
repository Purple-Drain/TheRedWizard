# -*- coding: utf-8 -*-
"""#1 Recently Updated sort: the local per-show touch record."""
import time

import pytest

from modules import show_touch as st
from modules import rewatch_cursor as rc


@pytest.fixture
def profile(tmp_path, monkeypatch):
    for mod in (st, rc):
        monkeypatch.setattr(mod.kodi_utils, 'addon_profile', lambda: str(tmp_path))
        monkeypatch.setattr(mod.kodi_utils, 'logger', lambda *a: None)
    return tmp_path


def test_touch_and_expiry(profile):
    st.touch(1668, now=1000.0)
    assert st.load(now=1001.0) == {'1668': 1000.0}
    assert st.load(now=1000.0 + st.TOUCH_MAX_AGE_SEC + 1) == {}


def test_rewatch_cursor_changes_touch_the_show(profile):
    before = time.time()
    rc.set_cursor(4608, 3, 13)
    assert st.get(4608) >= before
    st.touch(4608, now=5.0)
    rc.clear(4608)
    assert st.get(4608) >= before


def test_state_token_changes(profile):
    t0 = st.state_token()
    st.touch(1)
    assert st.state_token() != t0
