# -*- coding: utf-8 -*-
"""#168: folder scraper paths can be typed as well as browsed, and clearing one is its own choice.

Kodi's folder browser lists no WebDAV location, so a dav:// zurg path could not be set from Red Light's
settings at all. The dialogs are faked with monkeypatch, and set_setting is captured instead of
writing settings.db.
"""
import pytest

from caches import settings_cache as sc
from modules import kodi_utils

SETTING = 'folder1.tv_shows_directory'
DAV = 'dav://10.1.1.22:9999/dav/shows/'


class _Keyboard:
    def __init__(self, answers):
        self.answers, self.prefills = list(answers), []

    def input(self, heading, defaultt=''):
        self.prefills.append(defaultt)
        return self.answers.pop(0)


@pytest.fixture
def env(monkeypatch):
    state = {'current': 'None', 'choice': None, 'offered': None, 'saved': [], 'oks': 0, 'confirm': True,
             'confirm_kwargs': None, 'browsed': 0, 'keyboard': _Keyboard([])}

    def select(function_list, **kwargs):
        state['offered'] = list(function_list)
        return state['choice']

    def confirm(**kwargs):
        state['confirm_kwargs'] = kwargs
        return state['confirm']

    monkeypatch.setattr(sc, 'get_setting', lambda *a, **k: state['current'])
    monkeypatch.setattr(sc, 'set_setting', lambda setting_id, value: state['saved'].append((setting_id, value)))
    monkeypatch.setattr(sc, 'set_path', lambda params: state.__setitem__('browsed', state['browsed'] + 1))
    monkeypatch.setattr(kodi_utils, 'select_dialog', select)
    monkeypatch.setattr(kodi_utils, 'ok_dialog', lambda **k: state.__setitem__('oks', state['oks'] + 1))
    monkeypatch.setattr(kodi_utils, 'confirm_dialog', confirm)
    monkeypatch.setattr(kodi_utils, 'kodi_dialog', lambda: state['keyboard'])
    return state


def _run(state, choice, typed=()):
    state['choice'], state['keyboard'] = choice, _Keyboard(typed)
    sc.set_source_folder_path({'setting_id': SETTING})


def test_a_typed_path_is_normalized():
    assert sc.normalize_typed_folder_path(' dav://10.1.1.22:9999/dav/shows ') == DAV
    assert sc.normalize_typed_folder_path('DAVS://host/share/') == 'DAVS://host/share/'
    assert sc.normalize_typed_folder_path('/storage/emulated/0/Movies') == '/storage/emulated/0/Movies/'
    for bad in ('', '   ', None, 'shows', '10.1.1.22/dav/shows', 'C:\\media'):
        assert sc.normalize_typed_folder_path(bad) is None


def test_a_typed_dav_path_is_saved(env):
    _run(env, 'type', ['dav://10.1.1.22:9999/dav/shows'])
    assert env['saved'] == [(SETTING, DAV)]
    assert env['offered'] == ['browse', 'type']


def test_clearing_is_offered_for_a_set_path_and_needs_choosing(env):
    env['current'] = 'smb://10.1.1.22/debrid/shows/'
    _run(env, None)
    assert env['offered'] == ['browse', 'type', 'clear']
    assert env['saved'] == []
    _run(env, 'clear')
    assert env['saved'] == [(SETTING, 'None')]


def test_backing_out_changes_nothing(env):
    env['current'] = 'smb://10.1.1.22/debrid/shows/'
    _run(env, 'type', [''])
    _run(env, None)
    assert env['saved'] == [] and env['browsed'] == 0


def test_browse_is_still_there(env):
    _run(env, 'browse')
    assert env['browsed'] == 1 and env['saved'] == []


def test_a_path_kodi_cannot_open_asks_again(env):
    _run(env, 'type', ['10.1.1.22/dav/shows', DAV])
    assert env['oks'] == 1
    assert env['saved'] == [(SETTING, DAV)]


def test_the_current_path_is_offered_for_editing(env):
    env['current'] = 'smb://10.1.1.22/debrid/shows/'
    _run(env, 'type', [DAV])
    assert env['keyboard'].prefills == ['smb://10.1.1.22/debrid/shows/']


def test_a_login_is_never_shown_and_saving_one_needs_a_yes(env):
    env['current'] = 'smb://user:secret@10.1.1.22/debrid/shows/'
    env['confirm'] = False
    _run(env, 'type', ['smb://user:secret@10.1.1.22/debrid/shows/'])
    assert env['keyboard'].prefills == ['']
    assert env['saved'] == []
    assert env['confirm_kwargs']['default_control'] == 11  # Cancel has the focus
    env['confirm'] = True
    _run(env, 'type', ['smb://user:secret@10.1.1.22/debrid/shows/'])
    assert env['saved'] == [(SETTING, 'smb://user:secret@10.1.1.22/debrid/shows/')]
