# -*- coding: utf-8 -*-
"""Play-path helpers in modules.sources: retry copies (#99, #111, #118), the failure reason
carried to the log line and the sources dialog (#86), and the mime hint (#124).

Pure functions only; the queue loop, the dialog and the ListItem are reasoned about in the PR.
"""
import pytest

from modules.sources import Sources, debrid_cdn_item, retry_copies


def _item(**kw):
    base = {'scrape_provider': 'tb_cloud', 'name': 'Show.S01E01.1080p.mkv', 'quality': '1080p',
            'size_label': '1.2 GB', 'extraInfo': '', 'display_name': 'Show S01E01'}
    base.update(kw)
    return base


def _queue(item, retry_easynews=False, en_limit=3, cloud_retries=2):
    # _queue_retry_copies reads nothing from self.
    return Sources._queue_retry_copies(None, item, 1, 'TB', 'x', 'NAME', retry_easynews, en_limit, cloud_retries)


# --- debrid_cdn_item ---------------------------------------------------------------------

@pytest.mark.parametrize('provider', ['rd_cloud', 'pm_cloud', 'ad_cloud', 'oc_cloud', 'tb_cloud'])
def test_cloud_scraper_items_are_debrid_cdn(provider):
    assert debrid_cdn_item(_item(scrape_provider=provider))


@pytest.mark.parametrize('cached', ['Real-Debrid', 'Premiumize.me', 'AllDebrid', 'Offcloud', 'TorBox', 'tb_cloud', 'torbox'])
def test_external_debrid_cached_items_are_debrid_cdn(cached):
    assert debrid_cdn_item(_item(scrape_provider='external', cache_provider=cached, debrid=cached))


def test_external_debrid_field_alone_counts():
    assert debrid_cdn_item({'scrape_provider': 'external', 'debrid': 'Real-Debrid'})


@pytest.mark.parametrize('item', [
    _item(scrape_provider='easynews'), _item(scrape_provider='folders'), _item(scrape_provider='nzb'),
    _item(scrape_provider='aiostreams'), _item(scrape_provider='external'),
    _item(scrape_provider='external', cache_provider=''), {},
])
def test_other_items_are_not_debrid_cdn(item):
    assert not debrid_cdn_item(item)


# --- retry_copies ------------------------------------------------------------------------

def test_retry_copies_rows_and_marker():
    item = _item()
    rows = retry_copies(item, 3, 'TB', 'extra', 'NAME', 2, marker='cloud_retry')
    assert [r['resolve_display'] for r in rows] == [
        '03. [B]TB (RETRYx1)[/B][CR]extra[CR]NAME', '03. [B]TB (RETRYx2)[/B][CR]extra[CR]NAME']
    assert [r['cloud_retry'] for r in rows] == [1, 2]
    assert all(r is not item and r['name'] == item['name'] for r in rows)
    assert 'cloud_retry' not in item


def test_retry_copies_without_marker_and_zero():
    rows = retry_copies(_item(), 1, 'EN', 'e', 'N', 2)
    assert len(rows) == 2 and all('cloud_retry' not in r for r in rows)
    assert retry_copies(_item(), 1, 'EN', 'e', 'N', 0) == []
    assert retry_copies(_item(), 1, 'EN', 'e', 'N', -1) == []


# --- _queue_retry_copies -----------------------------------------------------------------

def test_cloud_item_gets_cloud_retries():
    rows = _queue(_item(scrape_provider='rd_cloud'), cloud_retries=2)
    assert [r['cloud_retry'] for r in rows] == [1, 2]


def test_external_debrid_cached_item_gets_the_same_retries():
    # #111: an external result cached on TorBox plays from the same CDN as a tb_cloud item.
    rows = _queue(_item(scrape_provider='external', cache_provider='TorBox', debrid='TorBox'), cloud_retries=2)
    assert [r['cloud_retry'] for r in rows] == [1, 2]
    assert 'RETRYx2' in rows[1]['resolve_display']


def test_external_non_debrid_item_gets_none():
    assert _queue(_item(scrape_provider='external', cache_provider='', debrid=''), cloud_retries=2) == []


def test_cloud_retries_zero_disables():
    assert _queue(_item(scrape_provider='tb_cloud'), cloud_retries=0) == []
    assert _queue(_item(scrape_provider='external', cache_provider='Real-Debrid'), cloud_retries=0) == []


def test_easynews_retry_semantics_unchanged():
    # limit counts the first attempt: limit 3 -> two RETRY rows, no cloud marker; off -> none.
    rows = _queue(_item(scrape_provider='easynews'), retry_easynews=True, en_limit=3)
    assert len(rows) == 2 and all('cloud_retry' not in r for r in rows)
    assert _queue(_item(scrape_provider='easynews'), retry_easynews=False, en_limit=3) == []
    assert _queue(_item(scrape_provider='easynews'), retry_easynews=True, en_limit=1) == []


def test_easynews_never_falls_through_to_cloud_retries():
    assert _queue(_item(scrape_provider='easynews', cache_provider='TorBox'), retry_easynews=False, cloud_retries=2) == []


# --- #86: failure reason ---------------------------------------------------------------

from modules.sources import describe_debrid_error, resolve_failure_reason  # noqa: E402


def test_describe_rd_error_body():
    assert describe_debrid_error('Real-Debrid', {'error': 'hoster_unavailable', 'error_code': 19}) == \
        'Real-Debrid: hoster_unavailable (error_code 19)'


def test_describe_error_without_code_and_torbox_detail():
    assert describe_debrid_error('Real-Debrid', {'error': 'bad_token', 'error_code': None}) == 'Real-Debrid: bad_token'
    assert describe_debrid_error('TorBox', {'error': 'requestdl gave up after 3 attempt(s), 30s deadline'}) == \
        'TorBox: requestdl gave up after 3 attempt(s), 30s deadline'
    assert describe_debrid_error('TorBox', 'plain text') == 'TorBox: plain text'


def test_describe_error_none_cases():
    assert describe_debrid_error('Real-Debrid', None) is None
    assert describe_debrid_error('Real-Debrid', {}) is None
    assert describe_debrid_error('Real-Debrid', {'error': '', 'error_code': None}) is None
    assert describe_debrid_error('TorBox', '   ') is None


def test_describe_error_is_capped():
    text = describe_debrid_error('TorBox', {'error': 'x' * 300})
    assert len(text) == 90 and text.startswith('TorBox: xxx')


def test_reason_no_url_precedence():
    assert resolve_failure_reason(None) == 'no url from resolver'
    assert resolve_failure_reason('', debrid_error='Real-Debrid: hoster_unavailable (error_code 19)') == \
        'Real-Debrid: hoster_unavailable (error_code 19)'
    assert resolve_failure_reason(None, debrid_error='Real-Debrid: x', note='resolver abandoned after the 240s deadline') == \
        'resolver abandoned after the 240s deadline'


def test_reason_player_failed_open():
    assert resolve_failure_reason('https://tb-cdn.io/f.mkv') == 'player could not open the stream'
    assert resolve_failure_reason('https://tb-cdn.io/f.mkv', debrid_error='ignored', player_error=True) == \
        'player could not open the stream (Kodi reported a playback error)'


class _Holder(Sources):
    # Sources without its __init__: only the small #86 methods are exercised.
    def __init__(self): pass


def test_record_failure_and_dialog_label():
    holder = _Holder()
    Sources._record_resolve_failure(holder, _item(scrape_provider='external', debrid='TorBox'), 'player could not open the stream')
    assert holder._resolve_failure == {'reason': 'player could not open the stream', 'name': 'Show.S01E01.1080p.mkv', 'provider': 'TorBox'}
    assert Sources._resolve_failure_label(holder) == 'Playback failed: player could not open the stream'
    Sources._record_resolve_failure(holder, _item(scrape_provider='rd_cloud'), 'Real-Debrid: hoster_unavailable (error_code 19)')
    assert holder._resolve_failure['provider'] == 'rd_cloud'


def test_no_failure_means_empty_label():
    holder = _Holder()
    assert Sources._resolve_failure_label(holder) == ''
    assert Sources._resolve_failure_text(holder) == ''
    holder._resolve_failure = None
    assert Sources._resolve_failure_label(holder) == ''


# --- #124: mime hint -------------------------------------------------------------------

from modules import sources as sources_mod  # noqa: E402
from modules.sources import PLAY_MIME_BY_EXT, PROP_PLAY_MIME, play_mime_for  # noqa: E402


@pytest.mark.parametrize('name,mime', [
    ('Show.S01E01.1080p.mkv', 'video/x-matroska'), ('Show.S01E01.MKV', 'video/x-matroska'),
    ('movie.mp4', 'video/mp4'), ('old.avi', 'video/x-msvideo'), ('cap.ts', 'video/mp2t'),
])
def test_mime_from_file_name(name, mime):
    assert play_mime_for(_item(name=name)) == mime
    assert play_mime_for(_item(scrape_provider='external', cache_provider='Real-Debrid', name=name)) == mime


def test_mime_from_url_when_name_has_no_extension():
    item = _item(scrape_provider='external', debrid='TorBox', name='Show S01E01 1080p WEB')
    assert play_mime_for(item, 'https://tb-cdn.io/dl/abc/Show.S01E01.mkv?token=1|User-Agent=x') == 'video/x-matroska'
    assert play_mime_for(item, 'https://tb-cdn.io/dl/abc/file.mp4#frag') == 'video/mp4'


def test_unknown_extension_keeps_today_behaviour():
    assert play_mime_for(_item(name='Show.S01E01.wmv'), 'https://x/y.wmv') is None
    assert play_mime_for(_item(name='Show.S01E01'), 'https://x/y') is None
    assert play_mime_for(_item(name='Show.S01E01.mkv.part')) is None
    assert play_mime_for(_item(name=None), None) is None


def test_non_debrid_items_get_no_hint():
    assert play_mime_for(_item(scrape_provider='easynews', name='a.mkv')) is None
    assert play_mime_for(_item(scrape_provider='folders', name='a.mkv'), 'smb://nas/a.mkv') is None
    assert play_mime_for(_item(scrape_provider='external', cache_provider='', name='a.mkv')) is None


def test_mime_table_covers_the_four_containers():
    assert dict(PLAY_MIME_BY_EXT) == {'.mkv': 'video/x-matroska', '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.ts': 'video/mp2t'}


def test_set_play_mime_hint_sets_logs_and_clears(monkeypatch):
    props, logs = {}, []
    monkeypatch.setattr(sources_mod.kodi_utils, 'set_property', lambda k, v: props.__setitem__(k, v))
    monkeypatch.setattr(sources_mod.kodi_utils, 'clear_property', lambda k: props.pop(k, None))
    monkeypatch.setattr(sources_mod.kodi_utils, 'logger', lambda h, m: logs.append(m))
    holder = _Holder()
    assert Sources._set_play_mime_hint(holder, _item(), 'https://tb-cdn.io/f.mkv') == 'video/x-matroska'
    assert props == {PROP_PLAY_MIME: 'video/x-matroska'}
    assert len(logs) == 1 and 'video/x-matroska' in logs[0] and 'Show.S01E01.1080p.mkv' in logs[0]
    assert Sources._set_play_mime_hint(holder, _item(scrape_provider='easynews'), 'https://x/f.mkv') is None
    assert props == {} and len(logs) == 1


def test_note_debrid_error_reads_api_attribute():
    holder = _Holder()
    api = _Holder()
    api.last_unrestrict_error = {'error': 'hoster_unavailable', 'error_code': 19}
    Sources._note_debrid_error(holder, 'Real-Debrid', api)
    assert holder._debrid_error == 'Real-Debrid: hoster_unavailable (error_code 19)'
    Sources._note_debrid_error(holder, 'TorBox', _Holder())
    assert holder._debrid_error is None
