"""Behavioural tests for the personal-list sort override helpers that talk to the store directly.

_move_sort_override is the fix that keeps a personal list's sort override from being orphaned when
the list or its author is renamed - the scope key embeds name and author, so a rename without it
carries the row to a scope nothing will ever look up again. _delete_sort_override is the same fix
applied to deletion: without it, deleting a list and recreating it with the same name and author
silently inherits the deleted list's sort order. _current_sort_spec is what the "Currently ..." label
reads, and _legacy_sort_code is the inverse lookup that keeps the legacy sort_order column in step.

All four are pure apart from the store calls (and, for delete_personal_list, a couple of kodi_utils
calls), so indexers/personal_lists.py is loaded the same way indexers/dialogs.py is loaded in
tests/test_sort_ui.py: against stub caches/modules and the real modules/list_sort.py already loaded
by tests/test_trakt_sync_list_sort.py.
"""
import importlib.util
import sys
import types
import unittest

from test_trakt_sync_list_sort import ROOT, list_sort

LIB = ROOT / 'plugin.video.redlight' / 'resources' / 'lib'
PERSONAL_LISTS = LIB / 'indexers' / 'personal_lists.py'

_STUB_KEYS = ('caches', 'caches.settings_cache', 'caches.personal_lists_cache', 'caches.list_sort_cache',
	'indexers', 'indexers.movies', 'indexers.tvshows',
	'modules', 'modules.kodi_utils', 'modules.settings', 'modules.metadata', 'modules.utils', 'modules.list_sort')


class _Store:
	"""The list_sort_cache state the loaded module's lazy imports read and write, plus a record of
	every call so a mutant that reorders or drops a call is visible even when the end state matches."""

	def __init__(self):
		self.overrides = {}
		self.writable = True
		self.get_calls = []
		self.set_calls = []
		self.delete_calls = []


def _load_personal_lists(store):
	caches = types.ModuleType('caches')
	caches.__path__ = []
	settings_cache = types.ModuleType('caches.settings_cache')
	settings_cache.get_setting = lambda setting_id, fallback='': fallback

	personal_lists_cache_module = types.ModuleType('caches.personal_lists_cache')
	personal_lists_cache_module.personal_lists_cache = types.SimpleNamespace(
		delete_list=lambda *a, **k: True, delete_list_contents=lambda *a, **k: True,
		update_single_detail=lambda *a, **k: None)

	list_sort_cache = types.ModuleType('caches.list_sort_cache')
	list_sort_cache.scope_key = lambda list_key, media_type=None: list_key
	list_sort_cache.normalize_media_type = lambda m: str(m).lower() if m else ''

	def get_override(scope):
		store.get_calls.append(scope)
		return store.overrides.get(scope, '')

	def set_override(scope, spec_string):
		store.set_calls.append((scope, spec_string))
		if not store.writable: return False
		store.overrides[scope] = spec_string
		return True

	def delete_override(scope):
		store.delete_calls.append(scope)
		if not store.writable: return False
		store.overrides.pop(scope, None)
		return True

	list_sort_cache.get_override = get_override
	list_sort_cache.set_override = set_override
	list_sort_cache.delete_override = delete_override

	indexers = types.ModuleType('indexers')
	indexers.__path__ = []
	movies_module = types.ModuleType('indexers.movies')
	movies_module.Movies = object
	tvshows_module = types.ModuleType('indexers.tvshows')
	tvshows_module.TVShows = object

	modules = types.ModuleType('modules')
	modules.__path__ = []
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.confirm_dialog = lambda **kwargs: True
	kodi_utils.kodi_refresh = lambda *a, **k: None
	kodi_utils.notification = lambda *a, **k: None
	kodi_utils.sleep = lambda *a, **k: None
	kodi_utils.path_exists = lambda *a, **k: False
	settings = types.ModuleType('modules.settings')
	settings.ignore_articles = lambda: False
	metadata = types.ModuleType('modules.metadata')
	metadata.movie_meta = lambda *a, **k: {}
	metadata.tvshow_meta = lambda *a, **k: {}
	utils = types.ModuleType('modules.utils')
	utils.TaskPool = object
	utils.paginate_list = lambda *a, **k: None
	utils.sort_for_article = lambda *a, **k: None
	utils.get_datetime = lambda: None
	utils.get_current_timestamp = lambda: 0
	utils.make_image = lambda *a, **k: None
	utils.download_image = lambda *a, **k: None
	modules.kodi_utils = kodi_utils
	modules.settings = settings
	modules.metadata = metadata
	modules.utils = utils
	modules.list_sort = list_sort

	sys.modules['caches'] = caches
	sys.modules['caches.settings_cache'] = settings_cache
	sys.modules['caches.personal_lists_cache'] = personal_lists_cache_module
	sys.modules['caches.list_sort_cache'] = list_sort_cache
	sys.modules['indexers'] = indexers
	sys.modules['indexers.movies'] = movies_module
	sys.modules['indexers.tvshows'] = tvshows_module
	sys.modules['modules'] = modules
	sys.modules['modules.kodi_utils'] = kodi_utils
	sys.modules['modules.settings'] = settings
	sys.modules['modules.metadata'] = metadata
	sys.modules['modules.utils'] = utils
	sys.modules['modules.list_sort'] = list_sort

	spec = importlib.util.spec_from_file_location('personal_lists_source_under_test', PERSONAL_LISTS)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class _PersonalListsTestCase(unittest.TestCase):
	"""Other modules in this suite install their own 'caches'/'modules' stubs and never clean up, and
	the run order is randomised, so save and restore everything this file replaces. Mirrors
	tests/test_sort_ui.py's _DialogTestCase."""

	def setUp(self):
		self._saved = dict((k, sys.modules[k]) for k in _STUB_KEYS if k in sys.modules)
		self.store = _Store()
		self.personal_lists = _load_personal_lists(self.store)

	def tearDown(self):
		for key in _STUB_KEYS:
			if key in self._saved: sys.modules[key] = self._saved[key]
			else: sys.modules.pop(key, None)


class MoveSortOverrideTests(_PersonalListsTestCase):
	def test_a_successful_move_writes_the_new_scope_and_removes_the_old_one(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.personal_lists._move_sort_override('Faves', 'jo', 'Favourites', 'jo')
		self.assertEqual({'personal:Favourites|jo': 'date_added:desc'}, self.store.overrides)

	def test_a_failed_write_leaves_the_old_row_intact(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.store.writable = False
		self.personal_lists._move_sort_override('Faves', 'jo', 'Favourites', 'jo')
		# Not delete-then-set: the old row must still be there, not lost entirely.
		self.assertEqual({'personal:Faves|jo': 'date_added:desc'}, self.store.overrides)
		self.assertEqual([], self.store.delete_calls)

	def test_equal_scopes_are_a_noop(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.personal_lists._move_sort_override('Faves', 'jo', 'Faves', 'jo')
		self.assertEqual({'personal:Faves|jo': 'date_added:desc'}, self.store.overrides)
		self.assertEqual([], self.store.get_calls)
		self.assertEqual([], self.store.set_calls)
		self.assertEqual([], self.store.delete_calls)

	def test_an_absent_row_is_a_noop(self):
		self.personal_lists._move_sort_override('Faves', 'jo', 'Favourites', 'jo')
		self.assertEqual({}, self.store.overrides)
		self.assertEqual([], self.store.set_calls)
		self.assertEqual([], self.store.delete_calls)


class DeleteSortOverrideTests(_PersonalListsTestCase):
	"""_delete_sort_override, called directly - the same defect class as the rename orphan, applied
	to deletion."""

	def test_deletes_the_rows_own_scope(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.personal_lists._delete_sort_override('Faves', 'jo')
		self.assertEqual({}, self.store.overrides)

	def test_an_absent_row_is_a_noop(self):
		self.personal_lists._delete_sort_override('Faves', 'jo')
		self.assertEqual([], self.store.set_calls)


class DeletePersonalListTests(_PersonalListsTestCase):
	"""delete_personal_list() end to end: confirming the dialog deletes both the list row and its
	sort override, so a list recreated later under the same name and author starts clean."""

	def test_deleting_a_list_also_deletes_its_sort_override(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.personal_lists.delete_personal_list({'list_name': 'Faves', 'author': 'jo'})
		self.assertEqual({}, self.store.overrides)

	def test_a_failed_list_delete_leaves_the_override_alone(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.personal_lists.personal_lists_cache.delete_list = lambda *a, **k: False
		self.personal_lists.delete_personal_list({'list_name': 'Faves', 'author': 'jo'})
		self.assertEqual({'personal:Faves|jo': 'date_added:desc'}, self.store.overrides)

	def test_cancelling_the_confirmation_leaves_everything_alone(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		self.personal_lists.kodi_utils.confirm_dialog = lambda **kwargs: False
		self.personal_lists.delete_personal_list({'list_name': 'Faves', 'author': 'jo'})
		self.assertEqual({'personal:Faves|jo': 'date_added:desc'}, self.store.overrides)
		self.assertEqual([], self.store.delete_calls)


class CurrentSortSpecTests(_PersonalListsTestCase):
	"""What the 'Currently ...' label in adjust_personal_list_properties actually reads."""

	def test_with_no_override_the_spec_is_the_engine_default_not_the_provider_default(self):
		"""The exact defect acceptance criterion 1 was about: a 'default:asc' fallback here would make
		the label say 'Provider Default' for every list nobody has overridden, instead of the title
		sort get_personal_list() actually falls back to."""
		spec = self.personal_lists._current_sort_spec('Faves', 'jo')
		self.assertEqual({'field': 'title', 'direction': 'asc'}, spec)

	def test_a_stored_override_wins(self):
		self.store.overrides['personal:Faves|jo'] = 'date_added:desc'
		spec = self.personal_lists._current_sort_spec('Faves', 'jo')
		self.assertEqual({'field': 'date_added', 'direction': 'desc'}, spec)


class LegacySortCodeTests(_PersonalListsTestCase):
	"""The legacy sort_order column value _legacy_sort_code writes back for a chosen spec."""

	def test_every_legacy_code_with_a_unique_spec_round_trips(self):
		# '' is excluded: it shares 'title:asc' with '0', and the inverse lookup prefers whichever
		# comes first in LEGACY_PERSONAL_CODES, which is '0'.
		for code, spec_string in list_sort.LEGACY_PERSONAL_CODES.items():
			if code == '': continue
			spec = list_sort.parse_spec(spec_string)
			self.assertEqual(code, self.personal_lists._legacy_sort_code(spec), code)

	def test_a_spec_with_no_legacy_equivalent_falls_back_to_code_zero(self):
		self.assertEqual('0', self.personal_lists._legacy_sort_code({'field': 'title', 'direction': 'desc'}))
		self.assertEqual('0', self.personal_lists._legacy_sort_code({'field': 'rank', 'direction': 'asc'}))


if __name__ == '__main__':
	unittest.main()
