# -*- coding: utf-8 -*-
"""#247: the Next Episode Start setting picks the quiet-start delay for next-episode plays only."""
import pytest

import modules.settings as settings


@pytest.mark.parametrize('mode, expected', [
    (settings.NEXTEP_HANDOFF_AUTO, 1000),
    (settings.NEXTEP_HANDOFF_QUIET, None),
    (settings.NEXTEP_HANDOFF_SCREEN, 0),
])
def test_nextep_play_follows_mode(mode, expected):
    assert settings.progress_quiet_delay_ms(True, mode=mode, quiet_ms=1000) == expected


@pytest.mark.parametrize('mode', [settings.NEXTEP_HANDOFF_AUTO, settings.NEXTEP_HANDOFF_QUIET, settings.NEXTEP_HANDOFF_SCREEN])
def test_other_plays_keep_quiet_start(mode):
    assert settings.progress_quiet_delay_ms(False, mode=mode, quiet_ms=2000) == 2000


@pytest.mark.parametrize('stored, expected', [('0', 0), ('1', 1), ('2', 2), ('7', 0), ('x', 0)])
def test_mode_reads_and_falls_back_to_auto(monkeypatch, stored, expected):
    monkeypatch.setattr(settings, 'get_setting', lambda key, default=None: stored)
    assert settings.nextep_handoff_mode() == expected


def test_setting_is_registered():
    from caches.settings_cache import default_settings
    rows = [r for r in default_settings() if r['setting_id'] == 'nextep.handoff_mode']
    assert rows and rows[0]['setting_default'] == '0' and set(rows[0]['settings_options']) == {'0', '1', '2'}
