# -*- coding: utf-8 -*-
"""Logins stay out of the Home window properties (#176).

Every setting is mirrored to Window(10000).Property(redlight.<id>), which any add-on, skin or
JSON-RPC client can read. Login ids are now mirrored only as a stand-in (SECRET_MASK when set;
'', empty_setting, '0' and the shipped default as they are), and get_setting / read_db_value read
them from settings.db on every call. The window properties are a dict and settings.db is a
temporary SQLite file; the settings_manager.xml check guards the stand-in against new comparisons.
"""
import os
import re
import sqlite3

import pytest

from caches import settings_cache as sc
from modules import kodi_utils

SKIN = os.path.join(os.path.dirname(__file__), '..', 'plugin.video.redlight', 'resources', 'skins', 'Default', '1080i', 'settings_manager.xml')


@pytest.fixture
def home(monkeypatch):
    props = {}
    monkeypatch.setattr(kodi_utils, 'set_property', lambda key, value: props.__setitem__(key, value))
    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: props.get(key, ''))
    monkeypatch.setattr(kodi_utils, 'clear_property', lambda key: props.pop(key, None))
    return props


@pytest.fixture
def db(monkeypatch, tmp_path):
    con = sqlite3.connect(str(tmp_path / 'settings.db'), isolation_level=None, check_same_thread=False)
    con.execute('CREATE TABLE settings (setting_id text unique, setting_type text, setting_default text, setting_value text)')
    cache = sc.SettingsCache()
    monkeypatch.setattr(sc, 'connect_database', lambda name: con)
    monkeypatch.setattr(sc, 'settings_cache', cache)
    return con, cache


def _row(con, setting_id, value):
    con.execute('INSERT OR REPLACE INTO settings VALUES (?, ?, ?, ?)', (setting_id, 'string', '', value))


def test_a_set_login_is_masked_in_its_home_property(home, db):
    con, cache = db
    cache.set_memory_cache('rd.token', 'REALTOKEN123')
    cache.set_memory_cache('easynews_password', 'hunter2')
    assert home['redlight.rd.token'] == sc.SECRET_MASK
    assert home['redlight.easynews_password'] == sc.SECRET_MASK


@pytest.mark.parametrize('setting_id, value', [
    ('rd.token', 'empty_setting'),
    ('tb.token', ''),
    ('trakt.token', '0'),
    ('tmdb.lists_read_token', sc.default_setting_values('tmdb.lists_read_token')['setting_default']),
    ('trakt.secret', sc.default_setting_values('trakt.secret')['setting_default']),
])
def test_unset_and_default_values_pass_through(home, db, setting_id, value):
    db[1].set_memory_cache(setting_id, value)
    assert home['redlight.%s' % setting_id] == value


def test_other_settings_are_mirrored_unchanged(home, db):
    db[1].set_memory_cache('folders.only_list', 'true')
    db[1].set_memory_cache('easynews_user', 'someone')
    assert home['redlight.folders.only_list'] == 'true'
    assert home['redlight.easynews_user'] == 'someone'


def test_setting_a_login_writes_the_database_and_masks_the_property(home, db):
    con, cache = db
    home['redlight.settings_properties_loaded'] = 'true'
    cache.set('rd.token', 'ABC123')
    assert con.execute("SELECT setting_value FROM settings WHERE setting_id = 'rd.token'").fetchone()[0] == 'ABC123'
    assert home['redlight.rd.token'] == sc.SECRET_MASK


def test_get_setting_reads_a_login_from_the_database_not_the_mask(home, db):
    con, cache = db
    home['redlight.settings_properties_loaded'] = 'true'
    home['redlight.rd.token'] = sc.SECRET_MASK
    _row(con, 'rd.token', 'ABC123')
    assert sc.get_setting('redlight.rd.token') == 'ABC123'


def test_logins_are_kept_briefly_and_a_write_here_shows_at_once(home, db, monkeypatch):
    con, cache = db
    clock = [100.0]
    monkeypatch.setattr(sc.time, 'monotonic', lambda: clock[0])
    home['redlight.settings_properties_loaded'] = 'true'
    _row(con, 'rd.token', 'OLD')
    assert sc.get_setting('redlight.rd.token') == 'OLD'
    _row(con, 'rd.token', 'NEW')
    clock[0] += sc._SECRET_TTL / 2
    assert sc.get_setting('redlight.rd.token') == 'OLD'
    clock[0] += sc._SECRET_TTL
    assert sc.get_setting('redlight.rd.token') == 'NEW'
    cache.set('rd.token', 'MINE')
    assert sc.get_setting('redlight.rd.token') == 'MINE'
    cache.write_db('rd.token', 'WRITTEN')
    assert sc.get_setting('redlight.rd.token') == 'WRITTEN'


def test_a_login_written_by_another_process_is_seen_at_once(home, db, monkeypatch):
    """Trakt's refresh waits on a Home flag and reads the new token straight after, so a process
    must not serve a cached token once another has written one."""
    con, reader = db
    monkeypatch.setattr(sc.time, 'monotonic', lambda: 100.0)
    home['redlight.settings_properties_loaded'] = 'true'
    _row(con, 'trakt.token', 'OLD')
    assert reader.read_db_value('trakt.token') == 'OLD'
    writer = sc.SettingsCache()
    writer.set('trakt.token', 'NEW')
    assert reader.read_db_value('trakt.token') == 'NEW'
    writer.write_db('trakt.refresh', 'R2')
    assert reader.read_db_value('trakt.refresh') == 'R2'
    writer.set_many([('rd.token', 'string', 'empty_setting', 'RD2')], load_properties=False)
    assert reader.read_db_value('rd.token') == 'RD2'


def test_a_login_changed_by_another_process_is_read_fresh(home, db, monkeypatch):
    con, cache = db
    monkeypatch.setattr(sc, '_SECRET_TTL', 0)
    home['redlight.settings_properties_loaded'] = 'true'
    _row(con, 'rd.token', 'OLD')
    _row(con, 'mdblist.token', 'OLD')
    assert sc.get_setting('redlight.rd.token') == 'OLD'
    assert cache.read_db_value('mdblist.token') == 'OLD'
    _row(con, 'rd.token', 'NEW')
    _row(con, 'mdblist.token', 'NEW')
    assert sc.get_setting('redlight.rd.token') == 'NEW'
    assert cache.read_db_value('mdblist.token') == 'NEW'
    assert 'rd.token' not in cache._db_cache and 'mdblist.token' not in cache._db_cache


def test_missing_login_falls_back(home, db):
    home['redlight.settings_properties_loaded'] = 'true'
    assert sc.get_setting('redlight.tb.token', 'fallback') == 'fallback'


def test_other_settings_are_still_served_from_the_property(home, db):
    con, cache = db
    home['redlight.settings_properties_loaded'] = 'true'
    home['redlight.folders.only_list'] = 'true'
    _row(con, 'folders.only_list', 'false')
    assert sc.get_setting('redlight.folders.only_list') == 'true'


def test_startup_mirror_masks_logins(home, db):
    con, cache = db
    _row(con, 'rd.token', 'ABC123')
    _row(con, 'folders.only_list', 'true')
    sc._apply_settings_properties_from_db()
    assert home['redlight.rd.token'] == sc.SECRET_MASK
    assert home['redlight.folders.only_list'] == 'true'


def test_every_login_id_is_a_known_setting():
    known = {i['setting_id'] for i in sc.default_settings()}
    assert sc.SECRET_SETTING_IDS <= known


def test_settings_window_compares_logins_only_with_values_the_mask_keeps():
    """The settings window can tell set from unset only through comparisons with '', empty_setting,
    '0' or the shipped default; any other comparison would break once the value is masked."""
    xml = open(SKIN, encoding='utf-8').read()
    for setting_id in sc.SECRET_SETTING_IDS:
        default = sc.default_setting_values(setting_id)['setting_default']
        pattern = r'String\.IsEqual\(Window\(10000\)\.Property\(redlight\.%s\),([^)]*)\)' % re.escape(setting_id)
        for compared in re.findall(pattern, xml):
            assert compared in ('empty_setting', '0', default), (setting_id, compared[:20])
