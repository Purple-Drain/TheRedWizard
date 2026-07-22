import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIST_SORT_PATH = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'modules' / 'list_sort.py'

OVERRIDES = {}
SETTINGS = {}


def _install_stubs():
	caches = types.ModuleType('caches')
	caches.__path__ = []
	list_sort_cache = types.ModuleType('caches.list_sort_cache')
	_media = {'movie': 'movies', 'movies': 'movies', 'show': 'shows', 'shows': 'shows', 'tvshow': 'shows'}

	def scope_key(list_key, media_type=None):
		normalized = _media.get(str(media_type).lower(), '') if media_type else ''
		return '%s:%s' % (list_key, normalized) if normalized else list_key

	list_sort_cache.scope_key = scope_key
	list_sort_cache.normalize_media_type = lambda m: _media.get(str(m).lower(), '') if m else ''
	list_sort_cache.get_override = lambda scope: OVERRIDES.get(scope, '')
	list_sort_cache.set_override = lambda scope, spec: True
	settings_cache = types.ModuleType('caches.settings_cache')
	settings_cache.get_setting = lambda setting_id, fallback='': SETTINGS.get(setting_id, fallback)
	modules = types.ModuleType('modules')
	modules.__path__ = []
	settings = types.ModuleType('modules.settings')
	settings.ignore_articles = lambda: True
	sys.modules['caches'] = caches
	sys.modules['caches.list_sort_cache'] = list_sort_cache
	sys.modules['caches.settings_cache'] = settings_cache
	sys.modules['modules'] = modules
	sys.modules['modules.settings'] = settings


def _load_list_sort_module():
	_install_stubs()
	spec = importlib.util.spec_from_file_location('list_sort_source_under_test', LIST_SORT_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


list_sort = _load_list_sort_module()

# collected_at is deliberately NOT in title order: date_added:desc must produce the reverse of
# title:asc for this fixture, so a sort_source that ignores media_type and always falls back to
# DEFAULT_SPEC (title:asc) is caught by test_movies_and_shows_sort_independently instead of
# accidentally producing the same order either way.
MOVIES = [
	{'title': 'Banana', 'collected_at': '2024-01-03', 'released': '2001-01-01'},
	{'title': 'The Apple', 'collected_at': '2024-01-02', 'released': '1999-01-01'},
]
SHOWS = [
	{'title': 'Zulu', 'collected_at': '2024-01-03', 'released': '2001-01-01'},
	{'title': 'Alpha', 'collected_at': '2024-01-02', 'released': '1999-01-01'},
]


class SortSourceTests(unittest.TestCase):
	def setUp(self):
		# Other test modules in this suite install their own fake 'caches'/'modules' stubs into
		# sys.modules at import time with no cleanup, and this file is one of them. When the full
		# suite runs, collection order can clobber our stub before these tests execute, since
		# sort_source()/resolve() re-read sys.modules lazily on every call. Reinstall ours here so
		# each test sees the fakes this file installed, regardless of collection order. Mirrors
		# tests/test_list_sort_resolve.py, which carries the same save/restore pair for the same
		# reason.
		self._original_sys_modules = {}
		for key in ('caches', 'caches.list_sort_cache', 'caches.settings_cache', 'modules', 'modules.settings'):
			if key in sys.modules:
				self._original_sys_modules[key] = sys.modules[key]
		_install_stubs()
		OVERRIDES.clear()
		SETTINGS.clear()

	def tearDown(self):
		for key in ('caches', 'caches.list_sort_cache', 'caches.settings_cache', 'modules', 'modules.settings'):
			if key in self._original_sys_modules:
				sys.modules[key] = self._original_sys_modules[key]
			else:
				sys.modules.pop(key, None)
		OVERRIDES.clear()
		SETTINGS.clear()

	def test_movies_and_shows_sort_independently(self):
		SETTINGS['redlight.sort.default.movies'] = 'date_added:desc'
		SETTINGS['redlight.sort.default.shows'] = 'title:asc'
		movies = list_sort.sort_source(list(MOVIES), 'trakt.watchlist', 'movies', 'trakt_sync')
		shows = list_sort.sort_source(list(SHOWS), 'trakt.watchlist', 'shows', 'trakt_sync')
		self.assertEqual(['Banana', 'The Apple'], [i['title'] for i in movies])
		self.assertEqual(['Alpha', 'Zulu'], [i['title'] for i in shows])

	def test_per_list_override_applies_to_one_media_type_only(self):
		SETTINGS['redlight.sort.default.movies'] = 'title:asc'
		OVERRIDES['trakt.collection:movies'] = 'release_date:desc'
		collection = list_sort.sort_source(list(MOVIES), 'trakt.collection', 'movies', 'trakt_sync')
		watchlist = list_sort.sort_source(list(MOVIES), 'trakt.watchlist', 'movies', 'trakt_sync')
		self.assertEqual(['Banana', 'The Apple'], [i['title'] for i in collection])
		self.assertEqual(['The Apple', 'Banana'], [i['title'] for i in watchlist])

	def test_unknown_adapter_returns_input_unchanged(self):
		result = list_sort.sort_source(list(MOVIES), 'trakt.watchlist', 'movies', 'nope')
		self.assertEqual(['Banana', 'The Apple'], [i['title'] for i in result])

	def test_none_data_is_safe(self):
		self.assertEqual(None, list_sort.sort_source(None, 'trakt.watchlist', 'movies', 'trakt_sync'))

	def test_reads_ignore_articles_setting(self):
		SETTINGS['redlight.sort.default.movies'] = 'title:asc'
		result = list_sort.sort_source(list(MOVIES), 'trakt.watchlist', 'movies', 'trakt_sync')
		self.assertEqual(['The Apple', 'Banana'], [i['title'] for i in result])


if __name__ == '__main__':
	unittest.main()
