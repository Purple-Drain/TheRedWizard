# -*- coding: utf-8 -*-
"""Saved widget lists, served before their indexers load (#155, #163).

A widget whose contents can be keyed from local state alone (the watched and progress tables, the
provider's cached rows, favourites, settings) registers a Spec here. The router asks serve() before it
imports the indexer, and it answers in one of three ways:

* hit: the stored list was built from exactly the current state, after the last widget refresh (the
  widget_refresh_timer setting or a manual Refresh Widgets, so those still rebuild as they always did),
  within the spec's freshness, and the spec's own check passes (Next Episodes: no listed show has aired
  a next episode since). Served as is.
* stale: a home widget asked at the start of a session, nothing fresh is stored, but a list from an
  earlier run is (younger than STALE_MAX). Shown at once, so the row is never empty at start, and
  replaced within seconds by the rebuild the service asks for. An in-addon listing is never stale.
* miss: the caller builds the normal way, and the build stores a fresh list.

Rows are JSON-safe dicts the spec's render function turns into ListItems, so a served ListItem is made
by the same calls as a built one.

Keeping what is on screen honest. Every answer, hit, stale or built, records in a Home-window property
the key it was built for (record_served). service.SavedListsRevalidate compares that with the current
key at CHECKPOINTS seconds after the first answer and, on a mismatch, asks that list for one real build
and refreshes the widgets. That covers a stale list, and a hit or build overtaken by another widget's
provider sync. MAX_REBUILDS bounds it, because refreshing the widgets makes a provider sync again.

Session state lives in Home-window properties, which every Kodi process sees and Kodi clears on restart:
* SERVED_PROP % list name: {"key", "external", "anime"} of the last answer for that list ("anime" holds
  the spec's variant flag).
* REVALIDATE_PROP % list name: '' (stale allowed), REBUILD (the service asks this list for a real build)
  or DONE (no more stale this session). Per list, so a rebuild asked of one list never makes another
  rebuild. serve() turns REBUILD into DONE the moment it sees it, before building, so a build that fails
  or finds no key cannot leave every later request forced into a build.
A read that fails counts as DONE: no stale list and no forced build.
"""
import sys
import json
import time
import importlib
from caches.widget_cache import widget_cache
from modules import kodi_utils, settings

LIST_TTL = 12 * 3600
# How old a stored list may be and still be shown stale while a rebuild runs. It is also the row's own
# expiry in maincache, so forget(), delete_show() and Clear Main Cache still remove it.
STALE_MAX = 7 * 24 * 3600
# The service's checks, in seconds after the first answer of the session (and never before
# REVALIDATE_AFTER seconds after the service started). The last one is past an In Progress cold build
# (up to about 15 s) plus its MDBList sync. FIRST_ANSWER_WAIT covers a TV that is still off.
REVALIDATE_AFTER = 15
CHECKPOINTS = (15, 45, 90, 150, 240)
MAX_REBUILDS = 2
FIRST_ANSWER_WAIT = 30 * 60
# A rebuild request nobody answers (the home window was not showing) ends the checks after this.
# Nothing is lost: the list on record is behind, so its next request misses and builds anyway.
REBUILD_WAIT = 60

SERVED_PROP = 'redlight.saved_list.served.%s'
REVALIDATE_PROP = 'redlight.saved_list.revalidate.%s'
REBUILD, DONE = 'rebuild', 'done'
# Set by kodi_utils.refresh_widgets(): a list built before it is due for a rebuild.
REFRESHED_PROP = 'redlight.widgets_refreshed_at'
# What a stale answer records as its key: never equal to a current key, so the service always rebuilds
# it (a list stale only by age has the same key as the current state).
STALE_KEY = 'stale'

# Watched status providers whose list inputs can all be read locally: Red Light's own table (0) and
# MDBList (3). Trakt, Simkl and PunchPlay keep building the normal way.
LOCAL_PROVIDERS = (0, 3)

# Modules that register a Spec when imported. specs() imports them, so the service and forget_all()
# see every saved list without importing each one by name.
SPEC_MODULES = ('modules.nextep_list_cache', 'modules.inprogress_lists')
_SPECS = {}


class Spec:
	"""One saved widget list.

	key(is_external, variant) and render(row, make_listitem, kodi_actor) are called at use time, so a spec
	module whose own functions are patched (tests) is honoured. still_valid(payload) returns '' while the
	list may be served, else the reason it may not. view_when_external=False sets the view only in-addon,
	as the TV show and movie indexers do."""
	def __init__(self, list_id, base, label, content, key, render, category='', view=None, fallback_views=(),
				view_when_external=True, variants=(False,), fresh_for=LIST_TTL, still_valid=None):
		self.list_id, self.base, self.label, self.content = list_id, base, label, content
		self.key, self.render, self.category = key, render, category
		self.view, self.fallback_views, self.view_when_external = view, tuple(fallback_views), view_when_external
		self.variants, self.fresh_for, self.still_valid = tuple(variants), fresh_for, still_valid

	def list_name(self, is_external, variant=False):
		# One row per entry point and per variant: a widget and an in-addon listing render different
		# context menus, and variants (Anime Next Episodes) are different lists. Sharing a row would have
		# each evict the other on every build.
		return '%s%s%s' % (self.base, '_widget' if is_external else '', '_anime' if variant else '')

	def lists(self):
		return tuple((is_external, variant) for is_external in (True, False) for variant in self.variants)


def register(spec):
	_SPECS[spec.list_id] = spec
	return spec


def specs():
	for name in SPEC_MODULES:
		try: importlib.import_module(name)
		except Exception as e: kodi_utils.logger('Red Light', 'Saved lists: %s not loaded: %s' % (name, e))
	return list(_SPECS.values())


def revalidate_state(name):
	try: return kodi_utils.get_property(REVALIDATE_PROP % name) or ''
	except Exception: return DONE


def record_served(spec, is_external, variant, key):
	try: kodi_utils.set_property(SERVED_PROP % spec.list_name(is_external, variant),
		json.dumps({'key': key, 'external': bool(is_external), 'anime': bool(variant)}))
	except Exception: pass


def served_lists(spec):
	"""{list name: {"key", "external", "anime"}} for every list of spec answered this session."""
	served = {}
	for is_external, variant in spec.lists():
		name = spec.list_name(is_external, variant)
		try:
			raw = kodi_utils.get_property(SERVED_PROP % name)
			if raw: served[name] = json.loads(raw)
		except Exception: pass
	return served


def store(spec, key, is_external, variant, items, category, extra=None):
	"""items: [(url, row, is_folder)] in display order; extra: spec fields for the payload (Next Episodes'
	valid_until). Always records what was shown. Stored only if the key still matches, so a watched-table
	write that landed mid-build is never filed under the state before it; the service then sees the
	recorded key is out of date and asks for a rebuild."""
	record_served(spec, is_external, variant, key)
	try:
		name = spec.list_name(is_external, variant)
		if not key:
			# A provider with no local key (Trakt, Simkl, PunchPlay): a list from before a switch must not
			# come back as the saved list at a later start. For a local provider no key means a cached row
			# the provider refetches, or a read that failed (a build before the network is up): the saved
			# list stays for the next start.
			if settings.watched_indicators() not in LOCAL_PROVIDERS: widget_cache.delete_list(name)
			return
		if spec.key(is_external, variant) != key: return
		payload = {'items': [dict({'url': url, 'row': row}, **({'folder': True} if is_folder else {})) for url, row, is_folder in items],
				'category': category, 'built_at': int(time.time())}
		payload.update(extra or {})
		json.dumps(payload)
		widget_cache.set_list(name, key, payload, ttl=STALE_MAX)
	except Exception as e: kodi_utils.logger('Red Light', '%s list cache store failed: %s' % (spec.label, e))


def forget(spec):
	for is_external, variant in spec.lists(): widget_cache.delete_list(spec.list_name(is_external, variant))


def forget_all():
	for spec in specs(): forget(spec)


def _miss(spec, reason, started):
	kodi_utils.logger('Red Light', '%s list cache miss (%s), %.2fs' % (spec.label, reason, time.time() - started))
	return False


def _age_text(seconds):
	return '%.1f h' % (seconds / 3600.0) if seconds >= 3600 else '%d s' % seconds


def _may_answer_stale(spec, is_external, variant):
	"""Stale is for the start of a session only: while this list has had no answer yet, or only stale
	ones (a second container asking for the same path at boot). Once it was built or hit this session, a
	changed state builds. Never for a provider with no local key: its builds never replace the row."""
	try:
		if settings.watched_indicators() not in LOCAL_PROVIDERS: return False
		raw = kodi_utils.get_property(SERVED_PROP % spec.list_name(is_external, variant))
		return not raw or json.loads(raw).get('key') == STALE_KEY
	except Exception: return False


def _widgets_refreshed_at():
	try: return int(float(kodi_utils.get_property(REFRESHED_PROP) or 0))
	except Exception: return 0


def _choose(spec, is_external, variant, revalidate):
	"""(kind, stored key, payload, age, reason): kind is 'hit', 'stale' or None (a miss, for reason)."""
	key = spec.key(is_external, variant)
	stored = widget_cache.get_list_any(spec.list_name(is_external, variant))
	if not stored or not stored[1]: return None, None, None, None, 'inputs not held locally' if key is None else 'nothing stored'
	stored_key, payload = stored
	built_at = int(payload.get('built_at') or 0)
	age = int(time.time()) - built_at
	# A widget refresh asks for current data, so a list built before it is never served, not even stale.
	refreshed = built_at < _widgets_refreshed_at()
	if refreshed: reason = 'widgets refreshed since'
	elif key is None: reason = 'inputs not held locally'
	elif stored_key != key: reason = 'state changed since it was stored'
	elif age >= spec.fresh_for: reason = 'older than %s' % _age_text(spec.fresh_for)
	else:
		reason = spec.still_valid(payload) if spec.still_valid else ''
		if not reason: return 'hit', stored_key, payload, age, ''
	if is_external and revalidate == '' and age < STALE_MAX and not refreshed and _may_answer_stale(spec, is_external, variant):
		return 'stale', stored_key, payload, age, reason
	return None, None, None, age, reason


def nav_row(label, icon, fanart):
	"""A folder item a list adds after its rows (Next Page): stored with the list and drawn the way
	kodi_utils.add_dir() draws it."""
	return {'nav': {'label': label, 'icon': icon, 'fanart': fanart}}


def _render_nav(nav, make_listitem):
	listitem = make_listitem()
	listitem.setLabel(nav['label'])
	kodi_utils.set_list_item_art(listitem, nav['icon'], fanart=nav['fanart'], banner=nav['fanart'])
	listitem.getVideoInfoTag(True).setPlot(' ')
	return listitem


def serve(spec, params, variant=False):
	"""Answer a widget request from a stored list. True when the directory was served; False means the
	caller builds it (and the build stores a fresh list)."""
	started = time.time()
	try: handle = int(sys.argv[1])
	except Exception: return _miss(spec, 'no directory handle', started)
	try:
		is_external = kodi_utils.external()
		name = spec.list_name(is_external, variant)
		revalidate = revalidate_state(name)
		if revalidate == REBUILD:
			kodi_utils.set_property(REVALIDATE_PROP % name, DONE)
			return _miss(spec, 'rebuild asked for by the service', started)
		kind, stored_key, payload, age, reason = _choose(spec, is_external, variant, revalidate)
		if kind is None: return _miss(spec, reason, started)
		make_listitem, kodi_actor = kodi_utils.make_listitem, kodi_utils.kodi_actor()
		items = [(i['url'], _render_nav(i['row']['nav'], make_listitem) if 'nav' in i['row'] else spec.render(i['row'], make_listitem, kodi_actor),
				bool(i.get('folder'))) for i in payload['items']]
	except Exception as e: return _miss(spec, 'error: %s' % e, started)
	# Committed from here: a build can no longer answer this handle, so the directory must end whatever
	# happens, or Kodi's fetch of the widget fails outright instead of showing a list.
	failed = None
	try:
		kodi_utils.add_items(handle, items)
		kodi_utils.set_content(handle, spec.content)
		kodi_utils.set_category(handle, payload.get('category') or spec.category)
	except Exception as e: failed = e
	finally: kodi_utils.end_directory(handle, cacheToDisc=False)
	if spec.view and (spec.view_when_external or not is_external):
		if spec.fallback_views: kodi_utils.set_view_mode(spec.view, spec.content, is_external, fallback_view_types=spec.fallback_views)
		else: kodi_utils.set_view_mode(spec.view, spec.content, is_external)
	record_served(spec, is_external, variant, STALE_KEY if kind == 'stale' else stored_key)
	if failed is not None:
		kodi_utils.logger('Red Light', '%s list cache serve failed after committing: %s' % (spec.label, failed))
	elif kind == 'stale':
		kodi_utils.logger('Red Light', '%s list cache stale (%s): %s listed, built %s ago, rebuild pending, %.2fs'
			% (spec.label, reason, len(items), _age_text(age), time.time() - started))
	else:
		kodi_utils.logger('Red Light', '%s list cache hit: %s listed, built %s ago, %.2fs'
			% (spec.label, len(items), _age_text(age), time.time() - started))
	return True
