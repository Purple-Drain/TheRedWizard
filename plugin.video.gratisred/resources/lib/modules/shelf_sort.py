# -*- coding: utf-8 -*-
"""Lightweight per-shelf list sort for My Simkl / My Trakt (Gratis Red).

Not Red Light's list_sort_cache stack — settings-backed specs and a simple picker.
"""
from __future__ import absolute_import

import re

from resources.lib.modules import control

SORT_DEFAULT = 'default'
SORT_CHOICES = (
    ('default', 'Provider Default'),
    ('title:asc', 'Title (A-Z)'),
    ('title:desc', 'Title (Z-A)'),
    ('date_added:desc', 'Date Added (newest)'),
    ('date_added:asc', 'Date Added (oldest)'),
    ('year:desc', 'Year (newest)'),
    ('year:asc', 'Year (oldest)'),
    ('random', 'Random'),
)
_VALID = frozenset(code for code, _ in SORT_CHOICES)

SHELF_LABELS = {
    'plantowatch': 'Plan to Watch',
    'watching': 'Watching',
    'completed': 'Completed',
    'hold': 'On Hold',
    'dropped': 'Dropped',
    'collection': 'Library',
    'watchlist': 'Watchlist',
    'favorites': 'Favorites',
}

SIMKL_SORTABLE = frozenset(('plantowatch', 'watching', 'completed', 'hold', 'dropped'))
TRAKT_SORTABLE = frozenset(('collection', 'watchlist', 'favorites'))


def trakt_shelf_from_url(url):
    """Map a My Trakt sync URL to a shelf key, or None if not sortable."""
    try:
        url = str(url or '')
    except Exception:
        return None
    if '/collection/' in url:
        return 'collection'
    if '/watchlist/' in url:
        return 'watchlist'
    if '/favorites/' in url:
        return 'favorites'
    return None


def setting_id(provider, media, shelf):
    return 'sort.%s.%s.%s' % (provider, media, shelf)


def get_list_sort(provider, media, shelf):
    raw = control.setting(setting_id(provider, media, shelf)) or ''
    if raw in _VALID:
        return raw
    # Legacy Gratis Red hard-sorted Trakt Library by title when no preference exists.
    if provider == 'trakt' and shelf == 'collection':
        return 'title:asc'
    # Simkl status shelves: Title A–Z when unset (same expectation as Content defaults elsewhere).
    if provider == 'simkl' and shelf in SIMKL_SORTABLE:
        return 'title:asc'
    return SORT_DEFAULT


def set_list_sort(provider, media, shelf, spec):
    control.setSetting(setting_id(provider, media, shelf), spec or SORT_DEFAULT)


def _title_key(title):
    try:
        return str(title or '').lower()
    except Exception:
        return ''


def _year_key(year):
    try:
        return int(re.sub(r'[^0-9]', '', str(year)) or '0')
    except Exception:
        return 0


def sort_items(items, provider, media, shelf, sortable=None):
    """Return a new list sorted by the user's per-shelf preference. Never raises."""
    if not items:
        return []
    if sortable is not None and shelf not in sortable:
        return list(items)
    try:
        spec = get_list_sort(provider, media, shelf)
        if spec == SORT_DEFAULT:
            return list(items)
        if spec == 'random':
            from random import random as _random
            return sorted(items, key=lambda _i: _random())
        field, _, direction = spec.partition(':')
        reverse = direction == 'desc'
        if field == 'title':
            return sorted(items, key=lambda i: _title_key(i.get('title')), reverse=reverse)
        if field == 'date_added':
            return sorted(items, key=lambda i: i.get('collected_at') or '', reverse=reverse)
        if field == 'year':
            return sorted(items, key=lambda i: _year_key(i.get('year')), reverse=reverse)
        return list(items)
    except Exception:
        return list(items)


def choose_list_sort(provider, media, shelf, sortable=None):
    """Context-menu picker; refreshes the container on change."""
    try:
        media = 'movies' if media == 'movies' else 'tvshows'
        shelf = str(shelf or '')
        if sortable is not None and shelf not in sortable:
            return
        current = get_list_sort(provider, media, shelf)
        labels = []
        for code, label in SORT_CHOICES:
            labels.append('[B]%s[/B]' % label if code == current else label)
        brand = 'Simkl' if provider == 'simkl' else 'Trakt'
        heading = '%s Sort — %s' % (brand, SHELF_LABELS.get(shelf, shelf))
        choice = control.selectDialog(labels, heading)
        if choice is None or choice < 0:
            return
        chosen = SORT_CHOICES[choice][0]
        if chosen == current:
            return
        set_list_sort(provider, media, shelf, chosen)
        control.infoDialog('Sort: %s' % SORT_CHOICES[choice][1], sound=False)
        control.refresh()
    except Exception:
        pass
