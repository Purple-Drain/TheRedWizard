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
	settings_cache = types.ModuleType('caches.settings_cache')
	settings_cache.get_setting = lambda setting_id, fallback='': SETTINGS.get(setting_id, fallback)
	sys.modules['caches'] = caches
	sys.modules['caches.list_sort_cache'] = list_sort_cache
	sys.modules['caches.settings_cache'] = settings_cache


def _load_list_sort_module():
	_install_stubs()
	spec = importlib.util.spec_from_file_location('list_sort_resolve_under_test', LIST_SORT_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


list_sort = _load_list_sort_module()


class ResolveTests(unittest.TestCase):
	def setUp(self):
		# Other test modules in this suite install their own fake 'caches.settings_cache'
		# into sys.modules at import time with no cleanup (e.g. test_source_utils_audio_lang.py).
		# When the full suite runs, collection order can clobber our stub before these tests
		# execute, since resolve() re-reads sys.modules lazily on every call. Reinstall ours
		# here so each test sees the fakes this file installed, regardless of collection order.
		self._original_sys_modules = {}
		for key in ('caches', 'caches.list_sort_cache', 'caches.settings_cache'):
			if key in sys.modules:
				self._original_sys_modules[key] = sys.modules[key]
		_install_stubs()
		OVERRIDES.clear()
		SETTINGS.clear()

	def tearDown(self):
		for key in ('caches', 'caches.list_sort_cache', 'caches.settings_cache'):
			if key in self._original_sys_modules:
				sys.modules[key] = self._original_sys_modules[key]
			else:
				sys.modules.pop(key, None)
		OVERRIDES.clear()
		SETTINGS.clear()

	def test_falls_back_to_default_spec(self):
		self.assertEqual(list_sort.DEFAULT_SPEC, list_sort.resolve('trakt.watchlist', 'movies'))

	def test_uses_movies_default(self):
		SETTINGS['redlight.sort.default.movies'] = 'release_date:desc'
		self.assertEqual({'field': 'release_date', 'direction': 'desc'}, list_sort.resolve('trakt.watchlist', 'movies'))

	def test_uses_shows_default(self):
		SETTINGS['redlight.sort.default.movies'] = 'release_date:desc'
		SETTINGS['redlight.sort.default.shows'] = 'title:asc'
		self.assertEqual({'field': 'title', 'direction': 'asc'}, list_sort.resolve('trakt.watchlist', 'shows'))

	def test_override_beats_default(self):
		SETTINGS['redlight.sort.default.movies'] = 'release_date:desc'
		OVERRIDES['trakt.watchlist:movies'] = 'rating:desc'
		self.assertEqual({'field': 'rating', 'direction': 'desc'}, list_sort.resolve('trakt.watchlist', 'movies'))

	def test_override_is_mediatype_specific(self):
		OVERRIDES['trakt.watchlist:movies'] = 'rating:desc'
		self.assertEqual(list_sort.DEFAULT_SPEC, list_sort.resolve('trakt.watchlist', 'shows'))

	def test_mixed_list_uses_override_only(self):
		OVERRIDES['trakt.list:12345'] = 'rank:asc'
		self.assertEqual({'field': 'rank', 'direction': 'asc'}, list_sort.resolve('trakt.list:12345'))

	def test_mixed_list_ignores_mediatype_defaults(self):
		SETTINGS['redlight.sort.default.movies'] = 'release_date:desc'
		self.assertEqual(list_sort.DEFAULT_SPEC, list_sort.resolve('trakt.list:12345'))

	def test_corrupt_override_falls_through_to_default(self):
		SETTINGS['redlight.sort.default.movies'] = 'rating:desc'
		OVERRIDES['trakt.watchlist:movies'] = 'garbage'
		self.assertEqual({'field': 'rating', 'direction': 'desc'}, list_sort.resolve('trakt.watchlist', 'movies'))


if __name__ == '__main__':
	unittest.main()
