# -*- coding: utf-8 -*-
"""#155: a listing entry must not republish every setting at a cold boot.

sync_kodi_profile_context() used to treat the first call of a Kodi session as a profile switch and
run a forced settings bootstrap in every widget process (~5 s each on the Shield). kodi_stub's
Window properties don't round-trip, so these fake the property store with monkeypatch.
"""
import pytest

from caches import base_cache
from caches import settings_cache as sc
from modules import kodi_utils

PROFILE_A = '/profiles/a/'
PROFILE_B = '/profiles/b/'


@pytest.fixture
def env(monkeypatch):
    props = {}
    calls = {'apply': 0, 'bootstrap': 0, 'clear_db_cache': 0, 'sync_settings': 0}
    state = {'profile': PROFILE_A}

    def count(name):
        calls[name] += 1

    def publish(*a, **k):
        props[sc._SETTINGS_PROPERTIES_LOADED] = 'true'

    monkeypatch.setattr(kodi_utils, 'get_property', lambda key: props.get(key, ''))
    monkeypatch.setattr(kodi_utils, 'set_property', lambda key, value: props.__setitem__(key, value))
    monkeypatch.setattr(kodi_utils, 'clear_property', lambda key: props.pop(key, None))
    monkeypatch.setattr(kodi_utils, 'addon_info', lambda key: 'special://profile/addon_data/plugin.video.redlight/')
    monkeypatch.setattr(kodi_utils, 'translate_path', lambda path: state['profile'])
    monkeypatch.setattr(sc, '_apply_settings_properties_from_db', lambda: (count('apply'), publish()))
    monkeypatch.setattr(sc, 'bootstrap_settings_properties', lambda force=False: (count('bootstrap'), publish()))
    monkeypatch.setattr(sc.settings_cache, 'clear_db_cache', lambda: count('clear_db_cache'))
    return props, calls, state


def test_cold_boot_listing_records_profile_without_publishing(env):
    props, calls, _ = env
    assert sc.sync_kodi_profile_context(publish=False) is False
    assert props[sc._ACTIVE_KODI_PROFILE] == PROFILE_A
    assert calls['apply'] == calls['bootstrap'] == 0


def test_cold_boot_publishing_entry_still_bootstraps(env):
    _, calls, _ = env
    assert sc.sync_kodi_profile_context() is True
    assert (calls['bootstrap'], calls['apply']) == (1, 0)


def test_cold_boot_publishes_from_db_once_synced(env):
    props, calls, _ = env
    props[sc._SETTINGS_DB_SYNCED] = 'true'
    assert sc.sync_kodi_profile_context() is True
    assert (calls['apply'], calls['bootstrap']) == (1, 0)


def test_first_sighting_keeps_properties_the_service_already_published(env):
    props, calls, _ = env
    props[sc._SETTINGS_PROPERTIES_LOADED] = 'true'
    for publish in (True, False):
        assert sc.sync_kodi_profile_context(publish=publish) is False
    assert props[sc._SETTINGS_PROPERTIES_LOADED] == 'true'
    assert calls == {'apply': 0, 'bootstrap': 0, 'clear_db_cache': 0, 'sync_settings': 0}


def test_profile_switch_on_listing_invalidates_without_publishing(env):
    props, calls, state = env
    props[sc._ACTIVE_KODI_PROFILE] = PROFILE_A
    props[sc._SETTINGS_PROPERTIES_LOADED] = 'true'
    state['profile'] = PROFILE_B
    assert sc.sync_kodi_profile_context(publish=False) is True
    assert props[sc._ACTIVE_KODI_PROFILE] == PROFILE_B
    assert sc._SETTINGS_PROPERTIES_LOADED not in props
    assert calls['clear_db_cache'] == 1
    assert calls['apply'] == calls['bootstrap'] == 0


def test_profile_switch_on_publishing_entry_republishes(env):
    props, calls, state = env
    props[sc._ACTIVE_KODI_PROFILE] = PROFILE_A
    props[sc._SETTINGS_PROPERTIES_LOADED] = 'true'
    props[sc._SETTINGS_DB_SYNCED] = 'true'
    state['profile'] = PROFILE_B
    assert sc.sync_kodi_profile_context() is True
    assert (calls['clear_db_cache'], calls['apply']) == (1, 1)


def test_unchanged_profile_with_properties_loaded_is_a_no_op(env):
    props, calls, _ = env
    props[sc._ACTIVE_KODI_PROFILE] = PROFILE_A
    props[sc._SETTINGS_PROPERTIES_LOADED] = 'true'
    assert sc.sync_kodi_profile_context() is False
    assert calls['apply'] == calls['bootstrap'] == 0


@pytest.fixture
def listing_env(env, monkeypatch):
    _, calls, _ = env
    state = {'empty': False}
    monkeypatch.setattr(base_cache, 'ensure_database_tables', lambda name: None)
    monkeypatch.setattr(sc.settings_cache, 'is_empty_strict', lambda: state['empty'])
    monkeypatch.setattr(sc, 'sync_settings', lambda params: calls.__setitem__('sync_settings', calls['sync_settings'] + 1))
    return calls, state


def test_listing_skips_the_settings_sync_when_rows_exist(listing_env):
    calls, _ = listing_env
    base_cache.ensure_listing_databases_ready()
    assert calls['sync_settings'] == 0


def test_listing_still_syncs_a_genuinely_empty_settings_db(listing_env):
    calls, state = listing_env
    state['empty'] = True
    base_cache.ensure_listing_databases_ready()
    assert calls['sync_settings'] == 1


def test_listing_skips_the_sync_once_this_session_already_synced(listing_env, env):
    calls, state = listing_env
    props, _, _ = env
    state['empty'] = True
    props['redlight.settings_db_synced'] = 'true'
    base_cache.ensure_listing_databases_ready()
    assert calls['sync_settings'] == 0


def test_listing_skips_the_sync_when_the_settings_db_is_unreadable(listing_env, monkeypatch):
    calls, _ = listing_env

    def locked():
        raise RuntimeError('database is locked')

    monkeypatch.setattr(sc.settings_cache, 'is_empty_strict', locked)
    base_cache.ensure_listing_databases_ready()
    assert calls['sync_settings'] == 0
