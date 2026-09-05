# -*- coding: utf-8 -*-
"""#122: AddonXMLCheck's disable/enable restart is a ~7 s churn (plus a notification) that
today fires on every deploy whose shipped addon.xml differs from the device's
reuselanguageinvoker/icon settings -- values this same module already rewrites. Kodi
re-reads addon.xml at its next start on its own, so a mismatch confined to those two
values needs a plain file rewrite, not a restart. Any other difference still needs one.

This covers the pure decision only (modules.kodi_utils.addon_xml_rewrite_needs_restart);
the file rewrite itself (sync_addon_xml_from_settings) and the disable/enable call
(restart_addon_for_addon_xml_change) touch real Kodi APIs and are exercised on-device.
"""
import modules.kodi_utils as kodi_utils


def test_no_mismatch_no_restart():
    assert kodi_utils.addon_xml_rewrite_needs_restart(False, False) is False


def test_invoker_only_mismatch_no_restart():
    assert kodi_utils.addon_xml_rewrite_needs_restart(True, False) is False


def test_icon_only_mismatch_no_restart():
    assert kodi_utils.addon_xml_rewrite_needs_restart(False, True) is False


def test_both_rewritable_mismatch_no_restart():
    assert kodi_utils.addon_xml_rewrite_needs_restart(True, True) is False


def test_other_mismatch_still_restarts():
    assert kodi_utils.addon_xml_rewrite_needs_restart(True, True, other_mismatch=True) is True
    assert kodi_utils.addon_xml_rewrite_needs_restart(False, False, other_mismatch=True) is True


def test_reuse_language_invoker_check_skips_restart_for_rewritable_only(monkeypatch):
    """End-to-end through reuse_language_invoker_check: force=True, only the rewritable
    values differ -> file gets rewritten, one log line, no disable/enable call."""
    monkeypatch.setattr(kodi_utils, 'addon_xml_settings_diff', lambda: (True, False))
    monkeypatch.setattr(kodi_utils, 'sync_addon_xml_from_settings', lambda: (True, True))
    monkeypatch.setattr(kodi_utils, 'finish_addon_xml_sync', lambda: None)
    restarted = []
    monkeypatch.setattr(kodi_utils, 'restart_addon_for_addon_xml_change', lambda notify=True: restarted.append(notify))
    lines = []
    monkeypatch.setattr(kodi_utils, 'logger', lambda heading, message: lines.append((heading, message)))

    result = kodi_utils.reuse_language_invoker_check(force=True)

    assert result is False
    assert restarted == []
    assert any('no restart needed' in message for _heading, message in lines)
