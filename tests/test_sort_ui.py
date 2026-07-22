"""The settings rows and dialogs that drive the unified sort engine.

Two halves. The first loads indexers/dialogs.py against stub Kodi modules and exercises the
two stage picker and both handlers as ordinary functions - they are pure apart from
select_dialog(). The second pins the string literals that the skin XML, the router and the
call sites have to agree on: a setting id, a mode name, a scope key or an adapter name that
drifts by one character breaks the feature silently, and nothing else in the suite notices.

The skin XML and the Kodi dialogs themselves cannot be run here (there is no Kodi runtime);
the manual checklist in docs/superpowers/plans/2026-07-20-unified-list-sort.md Task 9 Step 6
covers what these tests cannot.
"""
import ast
import importlib.util
import re
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from test_trakt_sync_list_sort import ROOT, list_sort

LIB = ROOT / 'plugin.video.redlight' / 'resources' / 'lib'
DIALOGS = LIB / 'indexers' / 'dialogs.py'
NAVIGATOR = LIB / 'indexers' / 'navigator.py'
PERSONAL_LISTS = LIB / 'indexers' / 'personal_lists.py'
TMDB_LISTS = LIB / 'indexers' / 'tmdb_lists.py'
TRAKT_LISTS = LIB / 'indexers' / 'trakt_lists.py'
ROUTER = LIB / 'modules' / 'router.py'
SIMKL_API = LIB / 'apis' / 'simkl_api.py'
MDBLIST_API = LIB / 'apis' / 'mdblist_api.py'
TRAKT_API = LIB / 'apis' / 'trakt_api.py'
SETTINGS_CACHE = LIB / 'caches' / 'settings_cache.py'
SETTINGS_MANAGER_XML = ROOT / 'plugin.video.redlight' / 'resources' / 'skins' / 'Default' / '1080i' / 'settings_manager.xml'

_STUB_KEYS = ('caches', 'caches.list_sort_cache', 'caches.settings_cache', 'modules', 'modules.kodi_utils',
	'modules.list_sort', 'modules.settings')


def _source(path):
	with open(str(path), 'r', encoding='utf-8') as f:
		return f.read()


def _tree(path):
	return ast.parse(_source(path))


def _function(path, name):
	for node in ast.walk(_tree(path)):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name: return node
	raise AssertionError('%s not found in %s' % (name, path.name))


def _called_names(node):
	"""Every plain function name called anywhere inside an ast node."""
	names = set()
	for child in ast.walk(node):
		if not isinstance(child, ast.Call): continue
		if isinstance(child.func, ast.Name): names.add(child.func.id)
		elif isinstance(child.func, ast.Attribute): names.add(child.func.attr)
	return names


def _string_constants(node, prefix):
	return set(i.value for i in ast.walk(node)
		if isinstance(i, ast.Constant) and isinstance(i.value, str) and i.value.startswith(prefix))


def _call_args(node, called_name):
	"""(positional literal values, keyword literal values) for the first called_name(...) call found
	in node. None for any argument that is not itself a literal constant (a Name, a BinOp, ...)."""
	for child in ast.walk(node):
		if not isinstance(child, ast.Call): continue
		func = child.func
		name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
		if name != called_name: continue
		positional = [a.value if isinstance(a, ast.Constant) else None for a in child.args]
		keywords = dict((k.arg, k.value.value if isinstance(k.value, ast.Constant) else None)
			for k in child.keywords if k.arg)
		return positional, keywords
	raise AssertionError('%s(...) not called in %s' % (called_name, node.name if hasattr(node, 'name') else node))


def _call_dict_literal(node, called_name, index=0):
	"""The {str: str} literal at position `index` of the first called_name(...) call found in node."""
	for child in ast.walk(node):
		if not isinstance(child, ast.Call): continue
		func = child.func
		name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
		if name != called_name: continue
		if len(child.args) <= index or not isinstance(child.args[index], ast.Dict): continue
		arg = child.args[index]
		return dict((k.value, v.value) for k, v in zip(arg.keys, arg.values)
			if isinstance(k, ast.Constant) and isinstance(v, ast.Constant))
	raise AssertionError('%s(...) with a dict literal at position %d not found' % (called_name, index))


# --------------------------------------------------------------------------------------------
# Half one: the dialog handlers, loaded against stubs and run for real.
# --------------------------------------------------------------------------------------------

class _Dialogs:
	"""The loaded dialogs module plus the stub state its handlers read and write."""

	def __init__(self):
		self.selections = []
		self.dialog_calls = []
		self.settings = {}
		self.overrides = {}
		self.refreshed = 0
		self.ok_dialogs = []
		self.store_writable = True


def _load_dialogs(state):
	caches = types.ModuleType('caches')
	caches.__path__ = []
	settings_cache = types.ModuleType('caches.settings_cache')
	settings_cache.get_setting = lambda setting_id, fallback='': state.settings.get(setting_id, fallback)

	def set_setting(setting_id, value, *args, **kwargs):
		state.settings['redlight.%s' % setting_id] = value
		return True

	settings_cache.set_setting = set_setting
	settings_cache.set_default = lambda *args, **kwargs: True
	settings_cache.default_setting_values = lambda *args, **kwargs: {}

	list_sort_cache = types.ModuleType('caches.list_sort_cache')
	_media = {'movie': 'movies', 'movies': 'movies', 'show': 'shows', 'shows': 'shows', 'tvshow': 'shows'}
	list_sort_cache.normalize_media_type = lambda m: _media.get(str(m).lower(), '') if m else ''
	list_sort_cache.scope_key = lambda list_key, media_type=None: (
		'%s:%s' % (list_key, _media[str(media_type).lower()]) if media_type and str(media_type).lower() in _media else list_key)
	list_sort_cache.get_override = lambda scope: state.overrides.get(scope, '')

	def set_override(scope, spec_string):
		if not state.store_writable: return False
		state.overrides[scope] = spec_string
		return True

	def delete_override(scope):
		if not state.store_writable: return False
		state.overrides.pop(scope, None)
		return True

	list_sort_cache.set_override = set_override
	list_sort_cache.delete_override = delete_override

	kodi_utils = types.ModuleType('modules.kodi_utils')

	def select_dialog(choices, **kwargs):
		state.dialog_calls.append({'choices': list(choices), 'kwargs': dict(kwargs)})
		if not state.selections: return None
		return state.selections.pop(0)

	kodi_utils.select_dialog = select_dialog

	def kodi_refresh(*args, **kwargs):
		state.refreshed += 1

	kodi_utils.kodi_refresh = kodi_refresh
	kodi_utils.ok_dialog = lambda *args, **kwargs: state.ok_dialogs.append(args)
	settings = types.ModuleType('modules.settings')
	settings.ignore_articles = lambda: False
	modules = types.ModuleType('modules')
	modules.__path__ = []
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	modules.list_sort = list_sort
	sys.modules['caches'] = caches
	sys.modules['caches.settings_cache'] = settings_cache
	sys.modules['caches.list_sort_cache'] = list_sort_cache
	sys.modules['modules'] = modules
	sys.modules['modules.kodi_utils'] = kodi_utils
	sys.modules['modules.list_sort'] = list_sort
	sys.modules['modules.settings'] = settings
	spec = importlib.util.spec_from_file_location('dialogs_source_under_test', DIALOGS)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class _DialogTestCase(unittest.TestCase):
	"""Other modules in this suite install their own 'caches'/'modules' stubs and never clean up,
	and the run order is randomised, so save and restore everything this file replaces."""

	def setUp(self):
		self._saved = dict((k, sys.modules[k]) for k in _STUB_KEYS if k in sys.modules)
		self.state = _Dialogs()
		self.dialogs = _load_dialogs(self.state)

	def tearDown(self):
		for key in _STUB_KEYS:
			if key in self._saved: sys.modules[key] = self._saved[key]
			else: sys.modules.pop(key, None)

	def line1s(self, call_index):
		import json
		return [i['line1'] for i in json.loads(self.state.dialog_calls[call_index]['kwargs']['items'])]


class PickSortSpecTests(_DialogTestCase):
	def test_field_then_direction(self):
		self.state.selections = ['date_added', 'desc']
		spec = self.dialogs._pick_sort_spec('Heading', 'trakt_sync')
		self.assertEqual({'field': 'date_added', 'direction': 'desc'}, spec)
		self.assertEqual(2, len(self.state.dialog_calls))
		self.assertEqual('Heading: Field', self.state.dialog_calls[0]['kwargs']['heading'])
		self.assertEqual('Heading: Direction', self.state.dialog_calls[1]['kwargs']['heading'])

	def test_only_the_adapters_own_fields_are_offered(self):
		self.state.selections = ['title', 'asc']
		self.dialogs._pick_sort_spec('Heading', 'trakt_sync')
		self.assertEqual(list(list_sort.field_choices('trakt_sync')), self.state.dialog_calls[0]['choices'])
		self.assertNotIn('rank', self.state.dialog_calls[0]['choices'])

	def test_a_directionless_field_skips_the_direction_dialog(self):
		self.state.selections = ['random']
		spec = self.dialogs._pick_sort_spec('Heading', 'trakt_sync')
		self.assertEqual({'field': 'random', 'direction': 'asc'}, spec)
		self.assertEqual(1, len(self.state.dialog_calls))

	def test_cancelling_either_stage_returns_none(self):
		self.state.selections = []
		self.assertIsNone(self.dialogs._pick_sort_spec('Heading', 'trakt_sync'))
		self.state.dialog_calls = []
		self.state.selections = ['title']
		self.assertIsNone(self.dialogs._pick_sort_spec('Heading', 'trakt_sync'))
		self.assertEqual(2, len(self.state.dialog_calls))

	def test_use_default_is_first_and_only_when_asked_for(self):
		self.state.selections = ['use_default']
		self.assertEqual('use_default', self.dialogs._pick_sort_spec('Heading', 'trakt_sync', allow_default=True))
		self.assertEqual('use_default', self.state.dialog_calls[0]['choices'][0])
		self.state.dialog_calls = []
		self.state.selections = ['title', 'asc']
		self.dialogs._pick_sort_spec('Heading', 'trakt_sync')
		self.assertNotIn('use_default', self.state.dialog_calls[0]['choices'])

	def test_the_current_spec_is_marked_in_both_stages(self):
		self.state.selections = ['date_added', 'desc']
		current = {'field': 'date_added', 'direction': 'desc'}
		self.dialogs._pick_sort_spec('Heading', 'trakt_sync', current=current)
		field_marked = [i for i in self.line1s(0) if 'CURRENT' in i]
		direction_marked = [i for i in self.line1s(1) if 'CURRENT' in i]
		self.assertEqual(1, len(field_marked))
		self.assertTrue(field_marked[0].startswith(list_sort.FIELD_LABELS['date_added']))
		self.assertEqual(1, len(direction_marked))
		self.assertTrue(direction_marked[0].startswith('Descending'))

	def test_the_direction_marker_is_dropped_when_the_field_changes(self):
		self.state.selections = ['title', 'asc']
		self.dialogs._pick_sort_spec('Heading', 'trakt_sync', current={'field': 'date_added', 'direction': 'desc'})
		self.assertEqual([], [i for i in self.line1s(1) if 'CURRENT' in i])

	def test_an_unknown_adapter_returns_none_instead_of_an_empty_dialog(self):
		self.assertIsNone(self.dialogs._pick_sort_spec('Heading', 'not_an_adapter'))
		self.assertEqual([], self.state.dialog_calls)


class SortDefaultChoiceTests(_DialogTestCase):
	def test_writes_the_spec_and_its_label_for_the_chosen_mediatype(self):
		self.state.selections = ['release_date', 'desc']
		self.dialogs.sort_default_choice({'media_type': 'movies'})
		self.assertEqual('release_date:desc', self.state.settings['redlight.sort.default.movies'])
		self.assertEqual('Release Date (descending)', self.state.settings['redlight.sort.default.movies_name'])
		self.assertNotIn('redlight.sort.default.shows', self.state.settings)
		self.assertEqual(1, self.state.refreshed)

	def test_the_setting_id_is_the_one_resolve_reads(self):
		self.state.selections = ['title', 'asc']
		self.dialogs.sort_default_choice({'media_type': 'shows'})
		self.assertIn(list_sort.DEFAULT_SETTING_IDS['shows'], self.state.settings)

	def test_cancelling_writes_nothing(self):
		self.state.selections = []
		self.dialogs.sort_default_choice({'media_type': 'movies'})
		self.assertEqual({}, self.state.settings)
		self.assertEqual(0, self.state.refreshed)

	def test_the_picker_offers_exactly_the_governed_adapters_intersection(self):
		self.state.selections = ['title', 'asc']
		self.dialogs.sort_default_choice({'media_type': 'movies'})
		offered = self.state.dialog_calls[0]['choices']
		expected = [i for i in list_sort.VALID_FIELDS
			if all(i in list_sort.field_choices(a) for a in list_sort.DEFAULT_GOVERNED_ADAPTERS)]
		self.assertEqual(expected, offered)
		# Spelled out so a change to the adapters has to be looked at rather than absorbed.
		self.assertEqual(['title', 'date_added', 'release_date', 'random'], offered)

	def test_every_offered_field_can_be_extracted_by_every_governed_adapter(self):
		"""The defect this replaces: the picker was built from field_choices('trakt_list') and offered
		rank/rating/votes/runtime. resolve() hands the stored spec to whichever adapter is building the
		list; with no extractor for the field apply() returns the payload untouched, so picking one of
		those four silently left every mediatype-split list in raw cache order."""
		self.state.selections = ['title', 'asc']
		self.dialogs.sort_default_choice({'media_type': 'movies'})
		offered = self.state.dialog_calls[0]['choices']
		self.assertTrue(offered)
		for field in offered:
			for name in list_sort.DEFAULT_GOVERNED_ADAPTERS:
				adapter = list_sort.ADAPTERS[name]
				self.assertIn(field, adapter['capabilities'], '%s cannot sort by %s' % (name, field))
				if field in list_sort.DIRECTIONLESS_FIELDS: continue
				self.assertIn(field, adapter['fields'], '%s has no %s extractor' % (name, field))

	def test_the_stored_default_is_marked_as_current(self):
		self.state.settings['redlight.sort.default.movies'] = 'date_added:desc'
		self.state.selections = ['title', 'asc']
		self.dialogs.sort_default_choice({'media_type': 'movies'})
		marked = [i for i in self.line1s(0) if 'CURRENT' in i]
		self.assertEqual(1, len(marked))
		self.assertTrue(marked[0].startswith(list_sort.FIELD_LABELS['date_added']))


class ListSortOverrideChoiceTests(_DialogTestCase):
	def test_a_mediatype_split_list_writes_a_suffixed_scope(self):
		self.state.selections = ['date_added', 'desc']
		self.dialogs.list_sort_override_choice({'list_key': 'trakt.watchlist', 'media_type': 'movies', 'adapter': 'trakt_sync'})
		self.assertEqual({'trakt.watchlist:movies': 'date_added:desc'}, self.state.overrides)
		self.assertEqual(1, self.state.refreshed)

	def test_a_mixed_list_writes_the_bare_scope(self):
		self.state.selections = ['rank', 'asc']
		self.dialogs.list_sort_override_choice({'list_key': 'trakt.list:42', 'adapter': 'trakt_list'})
		self.assertEqual({'trakt.list:42': 'rank:asc'}, self.state.overrides)

	def test_use_default_deletes_the_override(self):
		self.state.overrides['trakt.watchlist:shows'] = 'title:asc'
		self.state.selections = ['use_default']
		self.dialogs.list_sort_override_choice({'list_key': 'trakt.watchlist', 'media_type': 'shows', 'adapter': 'trakt_sync'})
		self.assertEqual({}, self.state.overrides)
		self.assertEqual(1, self.state.refreshed)

	def test_a_write_failure_reports_instead_of_refreshing(self):
		self.state.store_writable = False
		self.state.selections = ['title', 'asc']
		self.dialogs.list_sort_override_choice({'list_key': 'simkl', 'media_type': 'movies', 'adapter': 'simkl'})
		self.assertEqual(0, self.state.refreshed)
		self.assertEqual(1, len(self.state.ok_dialogs))

	def test_the_current_marker_follows_the_stored_override(self):
		self.state.overrides['mdblist.collection:movies'] = 'release_date:desc'
		self.state.selections = ['title', 'asc']
		self.dialogs.list_sort_override_choice(
			{'list_key': 'mdblist.collection', 'media_type': 'movies', 'adapter': 'mdblist_collection'})
		marked = [i for i in self.line1s(0) if 'CURRENT' in i]
		self.assertEqual([list_sort.FIELD_LABELS['release_date']], [i.split('  ')[0] for i in marked])

	def test_with_no_override_the_current_marker_follows_the_callers_fallback(self):
		"""A Trakt user list is ordered by the sort Trakt declares for it until someone overrides it,
		so marking title as current - what resolve() alone would say - would be a lie."""
		self.state.selections = ['title', 'asc']
		self.dialogs.list_sort_override_choice({'list_key': 'trakt.list:7', 'adapter': 'trakt_list', 'fallback': 'rank:asc'})
		marked = [i for i in self.line1s(0) if 'CURRENT' in i]
		self.assertEqual([list_sort.FIELD_LABELS['rank']], [i.split('  ')[0] for i in marked])

	def test_cancelling_leaves_an_existing_override_alone(self):
		self.state.overrides['simkl:shows'] = 'date_added:desc'
		self.state.selections = []
		self.dialogs.list_sort_override_choice({'list_key': 'simkl', 'media_type': 'shows', 'adapter': 'simkl'})
		self.assertEqual({'simkl:shows': 'date_added:desc'}, self.state.overrides)
		self.assertEqual(0, self.state.refreshed)


# --------------------------------------------------------------------------------------------
# Half two: the literals the XML, the router and the call sites have to agree on.
# --------------------------------------------------------------------------------------------

def _skin_items():
	"""(onclick, {property name: text}) for every <item> in the settings manager skin."""
	root = ET.parse(str(SETTINGS_MANAGER_XML)).getroot()
	items = []
	for item in root.iter('item'):
		onclick = ''.join(i.text or '' for i in item.findall('onclick'))
		properties = dict((i.get('name'), i.text or '') for i in item.findall('property'))
		items.append((onclick, properties))
	return items


def _router_branch_literals():
	"""The substring each router branch tests, in source order, up to and including 'choice'."""
	literals = []
	for line in _source(ROUTER).splitlines():
		match = re.search(r"(?:if|elif) '([^']+)' in mode|mode\.startswith\('([^']+)'\)", line)
		if not match: continue
		literal = match.group(1) or match.group(2)
		literals.append(literal)
		if literal == 'choice': break
	return literals


class SkinRowTests(unittest.TestCase):
	def test_the_two_default_rows_exist_and_point_at_the_dialog_handler(self):
		rows = [(onclick, props) for onclick, props in _skin_items() if 'mode=sort_default_choice' in onclick]
		self.assertEqual(2, len(rows))
		media_types = []
		for onclick, props in rows:
			media_type = re.search(r'media_type=(\w+)', onclick).group(1)
			media_types.append(media_type)
			self.assertIn(media_type, list_sort.DEFAULT_SETTING_IDS)
			self.assertEqual('action', props['setting_type'])
			# The row displays the companion _name setting the handler writes.
			self.assertIn('%s_name' % list_sort.DEFAULT_SETTING_IDS[media_type], props['setting_value'])
		self.assertEqual(['movies', 'shows'], sorted(media_types))

	def test_the_rows_are_not_gated_on_any_provider_being_logged_in(self):
		"""The old rows were Trakt/Simkl gated. These two apply to every provider's lists."""
		root = ET.parse(str(SETTINGS_MANAGER_XML)).getroot()
		for item in root.iter('item'):
			onclick = ''.join(i.text or '' for i in item.findall('onclick'))
			if 'mode=sort_default_choice' not in onclick: continue
			visibles = [i.text or '' for i in item.findall('visible')]
			self.assertEqual(1, len(visibles))
			self.assertNotIn('String.IsEqual', visibles[0])

	def test_the_settings_the_rows_write_are_declared_in_default_settings(self):
		declared = set(re.findall(r"'setting_id': '([^']+)'", _source(SETTINGS_CACHE)))
		for setting_id in list_sort.DEFAULT_SETTING_IDS.values():
			bare = setting_id.replace('redlight.', '')
			self.assertIn(bare, declared)
			self.assertIn('%s_name' % bare, declared)

	def test_the_superseded_per_provider_sort_rows_are_gone(self):
		onclicks = ' '.join(onclick for onclick, _props in _skin_items())
		for setting_id in ('sort.watchlist', 'sort.collection', 'sort.simkl', 'tmdbsort.watchlist', 'tmdbsort.favorites'):
			self.assertNotIn('setting_id=%s' % setting_id, onclicks)

	def test_the_tmdb_list_display_order_row_is_untouched(self):
		"""A different feature: it orders the folder of lists, not the contents of one."""
		onclicks = ' '.join(onclick for onclick, _props in _skin_items())
		self.assertIn('mode=list_display_order_choice&list_type=tmdb', onclicks)
		self.assertIn('mode=list_display_order_choice&list_type=personal', onclicks)


class RouterTests(unittest.TestCase):
	MODES = ('sort_default_choice', 'list_sort_override_choice')

	def test_both_handler_modes_reach_the_dialogs_branch(self):
		literals = _router_branch_literals()
		self.assertEqual('choice', literals[-1])
		for mode in self.MODES:
			self.assertIn('choice', mode)
			for earlier in literals[:-1]:
				self.assertNotIn(earlier, mode, '%s is swallowed by the %r branch' % (mode, earlier))

	def test_the_dialogs_branch_calls_the_mode_by_name(self):
		"""router.py execs 'dialogs.%s(params)' % mode, so the mode has to be the function name."""
		defined = set(i.name for i in ast.walk(_tree(DIALOGS)) if isinstance(i, ast.FunctionDef))
		for mode in self.MODES:
			self.assertIn(mode, defined)


def _sort_source_calls(path):
	"""(list_key, media_type, adapter) for every list_sort.sort_source() call in a module.
	None where the call site passes a variable rather than a literal."""
	calls = []
	for node in ast.walk(_tree(path)):
		if not isinstance(node, ast.Call): continue
		func = node.func
		if not isinstance(func, ast.Attribute) or func.attr != 'sort_source': continue
		literals = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
		calls.append((literals[1], literals[2], literals[3]))
	return calls


def _sort_cm_calls():
	"""(list_key, media_type, adapter) for every sort context menu the navigator builds.

	Direct self._sort_cm('trakt.watchlist', 'movies', 'trakt_sync') calls are read straight off.
	A helper that pins the list key and the adapter and takes only the media type - Simkl's, whose
	every status list shares one scope - is followed through to its own call sites."""
	tree = _tree(NAVIGATOR)
	calls, wrappers = [], {}
	for node in ast.walk(tree):
		if not isinstance(node, ast.FunctionDef): continue
		for child in ast.walk(node):
			if not isinstance(child, ast.Call): continue
			if not isinstance(child.func, ast.Attribute) or child.func.attr != '_sort_cm': continue
			args = [a.value if isinstance(a, ast.Constant) else None for a in child.args]
			if args[1] is None: wrappers[node.name] = (args[0], args[2])
			else: calls.append(tuple(args))
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call): continue
		if not isinstance(node.func, ast.Attribute) or node.func.attr not in wrappers: continue
		list_key, adapter = wrappers[node.func.attr]
		media_type = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
		calls.append((list_key, media_type, adapter))
	return calls


def _media_typed_sort_source_adapters():
	"""Adapter names of every sort_source() call site that can pass a media type.

	The third argument is unparsed rather than read as a literal: the literal None marks a mixed list,
	which resolve() can never hand the mediatype default to, while a variable can normalize to
	movies/shows and therefore can. Those are exactly the adapters the global default governs."""
	adapters = set()
	for path in (TRAKT_API, SIMKL_API, MDBLIST_API, PERSONAL_LISTS, TMDB_LISTS):
		for node in ast.walk(_tree(path)):
			if not isinstance(node, ast.Call): continue
			func = node.func
			if not isinstance(func, ast.Attribute) or func.attr != 'sort_source': continue
			if len(node.args) < 4: continue
			if ast.unparse(node.args[2]) == 'None': continue
			adapters.add(ast.literal_eval(node.args[3]))
	return adapters


class DefaultGovernedAdapterTests(unittest.TestCase):
	def test_the_governed_adapter_list_matches_the_call_sites(self):
		"""DEFAULT_GOVERNED_ADAPTERS is a hand-maintained mirror of which call sites pass a media type.
		Drift either way is silent: too wide and the picker offers a field some list cannot sort by,
		too narrow and a field that would have worked everywhere is withheld."""
		self.assertEqual(_media_typed_sort_source_adapters(), set(list_sort.DEFAULT_GOVERNED_ADAPTERS))
		self.assertEqual(len(list_sort.DEFAULT_GOVERNED_ADAPTERS), len(set(list_sort.DEFAULT_GOVERNED_ADAPTERS)))

	def test_the_mixed_list_adapters_are_excluded(self):
		for name in ('trakt_list', 'tmdb', 'personal'):
			self.assertNotIn(name, list_sort.DEFAULT_GOVERNED_ADAPTERS)

	def test_default_field_choices_is_the_intersection(self):
		expected = set(list_sort.field_choices(list_sort.DEFAULT_GOVERNED_ADAPTERS[0]))
		for name in list_sort.DEFAULT_GOVERNED_ADAPTERS[1:]:
			expected &= set(list_sort.field_choices(name))
		self.assertEqual(expected, set(list_sort.default_field_choices()))
		self.assertEqual(('title', 'date_added', 'release_date', 'random'), list_sort.default_field_choices())


class ContextMenuTests(unittest.TestCase):
	def test_every_sort_context_menu_matches_a_real_sort_source_call_site(self):
		"""The context menu writes an override the list builder has to read back. The pair that
		matters is (list_key, adapter): a mismatch stores a row nothing will ever look up."""
		known = set((list_key, adapter) for path in (TRAKT_API, SIMKL_API, MDBLIST_API)
			for list_key, _media_type, adapter in _sort_source_calls(path))
		calls = _sort_cm_calls()
		self.assertTrue(calls)
		for list_key, media_type, adapter in calls:
			self.assertIn((list_key, adapter), known, '%s/%s has no sort_source call site' % (list_key, adapter))
			self.assertIn(adapter, list_sort.ADAPTERS)
			# None where the media type is a loop variable (the flat Simkl menu). Every literal one
			# has to normalize, or scope_key() drops the suffix and two lists share one override.
			self.assertIn(media_type, ('movies', 'shows', None))

	def test_the_mediatype_split_lists_all_carry_the_entry(self):
		pairs = set((list_key, media_type) for list_key, media_type, _adapter in _sort_cm_calls())
		for list_key in ('trakt.watchlist', 'trakt.collection', 'mdblist.watchlist', 'mdblist.collection', 'simkl'):
			for media_type in ('movies', 'shows'):
				self.assertIn((list_key, media_type), pairs)

	def test_the_entry_routes_to_the_override_handler(self):
		self.assertIn("'mode': 'list_sort_override_choice'", _source(NAVIGATOR))


class ContextMenuLabelTests(unittest.TestCase):
	"""Simkl's five status lists share one scope per media type, so setting the sort from any one of
	them reorders the other four. The label has to say so; a plain 'Set Custom Sort' reads as
	list-specific and the reordering looks like a bug."""

	def _sort_cm_def(self):
		for node in ast.walk(_tree(NAVIGATOR)):
			if isinstance(node, ast.FunctionDef) and node.name == '_sort_cm': return node
		self.fail('_sort_cm not found in navigator.py')

	def _label_kwarg(self, wrapper_name):
		for node in ast.walk(_tree(NAVIGATOR)):
			if not isinstance(node, ast.FunctionDef) or node.name != wrapper_name: continue
			for child in ast.walk(node):
				if not isinstance(child, ast.Call): continue
				if not isinstance(child.func, ast.Attribute) or child.func.attr != '_sort_cm': continue
				for kw in child.keywords:
					if kw.arg == 'label': return ast.unparse(kw.value)
		return None

	def test_the_default_label_is_the_plain_one(self):
		defaults = self._sort_cm_def().args
		names = [a.arg for a in defaults.args]
		self.assertIn('label', names, '_sort_cm takes no label, so a shared scope cannot say so')
		offset = len(names) - len(defaults.defaults)
		default = defaults.defaults[names.index('label') - offset]
		self.assertEqual('Set Custom Sort', ast.literal_eval(default))

	def test_the_label_reaches_the_menu_entry(self):
		"""Guards the guard: a label parameter nothing interpolates would pass every other test here."""
		body = ast.unparse(self._sort_cm_def())
		self.assertIn('label', body.split('return', 1)[1])

	def test_simkl_says_the_entry_covers_every_simkl_list_of_that_media_type(self):
		label = self._label_kwarg('_simkl_sort_cm')
		self.assertIsNotNone(label, 'Simkl uses the plain label, so it claims to sort one list')
		self.assertIn('All Simkl', label)

	def test_the_per_list_scopes_keep_the_plain_label(self):
		"""Only a scope backing several visible lists should widen its label."""
		for node in ast.walk(_tree(NAVIGATOR)):
			if not isinstance(node, ast.Call): continue
			if not isinstance(node.func, ast.Attribute) or node.func.attr != '_sort_cm': continue
			if not node.args or not isinstance(node.args[0], ast.Constant): continue
			if node.args[0].value == 'simkl': continue
			self.assertEqual([], [kw for kw in node.keywords if kw.arg == 'label'],
				'%s backs one visible list, so it should not widen its label' % node.args[0].value)


class CallSiteRewiringTests(unittest.TestCase):
	"""The three UIs that used to write stores nothing reads."""

	def test_the_trakt_list_context_menu_delegates_to_the_override_dialog(self):
		node = _function(TRAKT_LISTS, 'set_list_custom_sort')
		self.assertIn('list_sort_override_choice', _called_names(node))
		scopes = _string_constants(node, 'trakt.list:')
		self.assertEqual({'trakt.list:%s'}, scopes)
		self.assertIn('trakt.list:%s', _string_constants(_tree(TRAKT_API), 'trakt.list:'))
		# The adapter has to be 'trakt_list' (mixed, rank/rating/etc.), not 'trakt_sync' (the
		# watchlist/collection adapter with none of those fields) - a swap would silently drop every
		# field a Trakt user list's own picker offers.
		payload = _call_dict_literal(node, 'list_sort_override_choice')
		self.assertEqual('trakt_list', payload['adapter'])

	def test_no_trakt_ui_writes_the_legacy_per_list_sort_store_any_more(self):
		source = _source(TRAKT_LISTS)
		for legacy in ('set_list_custom_sort(list_id', 'delete_list_custom_sort', 'get_list_custom_sort('):
			self.assertNotIn(legacy, source)

	def test_the_trakt_artwork_builder_no_longer_reads_the_legacy_store(self):
		node = _function(TRAKT_LISTS, 'trakt_image_maker')
		self.assertNotIn('get_list_custom_sort', _called_names(node))
		self.assertIn('get_trakt_list_contents', _called_names(node))

	def test_the_tmdb_properties_dialog_writes_the_override_store(self):
		node = _function(TMDB_LISTS, 'adjust_tmdb_list_properties')
		called = _called_names(node)
		self.assertIn('set_override', called)
		self.assertIn('_pick_sort_spec', called)
		self.assertIn('resolve', called)
		self.assertEqual({'tmdb:%s'}, _string_constants(node, 'tmdb:'))
		get_tmdb_list_node = _function(TMDB_LISTS, 'get_tmdb_list')
		self.assertIn('tmdb:%s', _string_constants(get_tmdb_list_node, 'tmdb:'))
		# The picker must be handed the tmdb adapter, not some other list's - a swap would silently
		# offer the wrong field set and let the wrong adapter's fields drive this list's sort.
		pick_args, _pick_kwargs = _call_args(node, '_pick_sort_spec')
		self.assertEqual('tmdb', pick_args[1])
		# The 'Currently ...' label's fallback and get_tmdb_list's own fallback have to be the same
		# literal - the comment above the resolve() call says so, but nothing besides this enforced
		# it: a drift here means the label can say something get_tmdb_list would never produce.
		resolve_args, _resolve_kwargs = _call_args(node, 'resolve')
		label_fallback = resolve_args[2]
		_list_args, list_kwargs = _call_args(get_tmdb_list_node, 'sort_source')
		self.assertEqual('default:asc', label_fallback)
		self.assertEqual(label_fallback, list_kwargs.get('fallback'))

	def test_no_tmdb_ui_writes_the_legacy_sort_order_column(self):
		self.assertNotIn('set_sort_order', _source(TMDB_LISTS))

	def test_creating_a_personal_list_stores_the_chosen_sort_as_an_override(self):
		"""The whole point: the creation prompt used to write only the legacy column, so every list
		made after the upgrade came back title sorted no matter what the user picked."""
		node = _function(PERSONAL_LISTS, 'make_new_personal_list')
		called = _called_names(node)
		self.assertIn('personal_sort_order', called)
		self.assertIn('_set_sort_override', called)
		self.assertIn('make_list', called)

	def test_importing_a_list_stores_its_import_order_as_an_override(self):
		node = _function(PERSONAL_LISTS, 'run')
		self.assertIn('make_list', _called_names(node))
		self.assertIn('_set_sort_override', _called_names(node))
		# The imported file's own order is date_added ascending. A flip to descending would silently
		# reverse every list ExternalImport ever creates.
		spec = _call_dict_literal(node, '_set_sort_override', index=2)
		self.assertEqual({'field': 'date_added', 'direction': 'asc'}, spec)

	def test_the_personal_properties_dialog_writes_the_override_store(self):
		node = _function(PERSONAL_LISTS, 'adjust_personal_list_properties')
		called = _called_names(node)
		self.assertIn('_set_sort_override', called)
		self.assertIn('_current_sort_spec', called)
		self.assertIn('spec_label', called)

	def test_renaming_a_personal_list_carries_its_override_across(self):
		"""The scope key embeds the name and the author, so a rename orphans the row otherwise."""
		for name in ('adjust_personal_list_properties', 'import_trakt_list'):
			self.assertIn('_move_sort_override', _called_names(_function(PERSONAL_LISTS, name)))

	def test_every_personal_scope_literal_is_the_one_get_personal_list_resolves(self):
		self.assertEqual({'personal:%s|%s'}, _string_constants(_tree(PERSONAL_LISTS), 'personal:'))

	def test_current_sort_spec_passes_no_fallback(self):
		"""A 'default:asc' fallback here is the exact defect acceptance criterion 1 was about: every
		list without an override would show 'Provider Default' in the 'Currently ...' label instead
		of the title sort get_personal_list() actually falls back to. Covered behaviourally too, in
		tests/test_personal_lists_sort_overrides.py."""
		node = _function(PERSONAL_LISTS, '_current_sort_spec')
		positional, keywords = _call_args(node, 'resolve')
		self.assertEqual(1, len(positional))
		self.assertNotIn('fallback', keywords)


if __name__ == '__main__':
	unittest.main()
