import ast
import importlib.util
import inspect
import sqlite3
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'caches' / 'list_sort_cache.py'

SCHEMA = 'CREATE TABLE IF NOT EXISTS list_sort (scope text unique, spec text)'


def _load_module(connection):
	caches = types.ModuleType('caches')
	caches.__path__ = []
	base_cache = types.ModuleType('caches.base_cache')
	base_cache.connect_database = lambda name: connection
	modules = types.ModuleType('modules')
	modules.__path__ = []
	kodi_utils = types.ModuleType('modules.kodi_utils')
	kodi_utils.logger = lambda *args, **kwargs: None
	sys.modules['caches'] = caches
	sys.modules['caches.base_cache'] = base_cache
	sys.modules['modules'] = modules
	sys.modules['modules.kodi_utils'] = kodi_utils
	spec = importlib.util.spec_from_file_location('list_sort_cache_under_test', CACHE_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class ScopeKeyTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.connection = sqlite3.connect(':memory:')
		cls.connection.execute(SCHEMA)
		cls.module = _load_module(cls.connection)

	def test_media_type_is_appended(self):
		self.assertEqual('trakt.watchlist:movies', self.module.scope_key('trakt.watchlist', 'movies'))

	def test_media_type_is_normalised(self):
		self.assertEqual('trakt.watchlist:shows', self.module.scope_key('trakt.watchlist', 'show'))
		self.assertEqual('trakt.watchlist:movies', self.module.scope_key('trakt.watchlist', 'movie'))

	def test_mixed_media_lists_have_no_suffix(self):
		self.assertEqual('trakt.list:12345', self.module.scope_key('trakt.list:12345'))

	def test_unknown_media_type_is_dropped(self):
		self.assertEqual('trakt.watchlist', self.module.scope_key('trakt.watchlist', 'anime'))


class OverrideStoreTests(unittest.TestCase):
	def setUp(self):
		self.connection = sqlite3.connect(':memory:')
		self.connection.execute(SCHEMA)
		self.module = _load_module(self.connection)

	def test_missing_override_is_empty_string(self):
		self.assertEqual('', self.module.get_override('trakt.watchlist:movies'))

	def test_set_then_get(self):
		self.assertTrue(self.module.set_override('trakt.watchlist:movies', 'date_added:desc'))
		self.assertEqual('date_added:desc', self.module.get_override('trakt.watchlist:movies'))

	def test_set_replaces_existing(self):
		self.module.set_override('trakt.watchlist:movies', 'date_added:desc')
		self.module.set_override('trakt.watchlist:movies', 'title:asc')
		self.assertEqual('title:asc', self.module.get_override('trakt.watchlist:movies'))

	def test_delete_removes_row(self):
		self.module.set_override('trakt.watchlist:movies', 'title:asc')
		self.assertTrue(self.module.delete_override('trakt.watchlist:movies'))
		self.assertEqual('', self.module.get_override('trakt.watchlist:movies'))

	def test_delete_missing_row_succeeds(self):
		self.assertTrue(self.module.delete_override('nothing.here'))

	def test_get_all_returns_every_row(self):
		self.module.set_override('trakt.watchlist:movies', 'title:asc')
		self.module.set_override('trakt.collection:shows', 'rating:desc')
		self.assertEqual({'trakt.watchlist:movies': 'title:asc', 'trakt.collection:shows': 'rating:desc'},
			self.module.get_all_overrides())

	def test_get_all_on_empty_table(self):
		self.assertEqual({}, self.module.get_all_overrides())

	def test_get_all_coerces_null_spec_to_empty_string(self):
		self.connection.execute('INSERT INTO list_sort (scope, spec) VALUES (?, ?)', ('trakt.watchlist:movies', None))
		self.assertEqual({'trakt.watchlist:movies': ''}, self.module.get_all_overrides())


class DatabaseRegistrationTests(unittest.TestCase):
	"""Guard against repeating the list_sort_db integrity_check omission for future database additions."""

	@classmethod
	def setUpClass(cls):
		modules = sys.modules.get('modules') or types.ModuleType('modules')
		modules.__path__ = []
		kodi_utils = types.ModuleType('modules.kodi_utils')
		kodi_utils.logger = lambda *args, **kwargs: None
		sys.modules['modules'] = modules
		sys.modules['modules.kodi_utils'] = kodi_utils
		base_cache_path = CACHE_PATH.parent / 'base_cache.py'
		spec = importlib.util.spec_from_file_location('base_cache_under_test', base_cache_path)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		cls.module = module

	def test_list_sort_db_registered_in_table_creators(self):
		self.assertIn('list_sort_db', self.module.table_creators())

	def test_list_sort_db_registered_in_locations(self):
		self.assertIn('list_sort_db', self.module.locations())

	def test_list_sort_db_registered_in_integrity_check(self):
		# integrity_check is a local dict inside check_databases_integrity, not returned by the
		# function, so parse the source to recover its keys rather than duplicating the dict here.
		source = inspect.getsource(self.module.check_databases_integrity)
		tree = ast.parse(source)
		function_def = tree.body[0]
		assign = next(
			node for node in ast.walk(function_def)
			if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'integrity_check' for t in node.targets))
		integrity_check = ast.literal_eval(assign.value)
		self.assertIn('list_sort_db', integrity_check)


if __name__ == '__main__':
	unittest.main()
