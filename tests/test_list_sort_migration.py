import importlib.util
import sqlite3
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIST_SORT_PATH = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'modules' / 'list_sort.py'


def _install_stubs():
	caches = types.ModuleType('caches')
	caches.__path__ = []
	list_sort_cache = types.ModuleType('caches.list_sort_cache')
	list_sort_cache.scope_key = lambda list_key, media_type=None: list_key
	list_sort_cache.normalize_media_type = lambda m: ''
	list_sort_cache.get_override = lambda scope: ''
	list_sort_cache.set_override = lambda scope, spec: True
	settings_cache = types.ModuleType('caches.settings_cache')
	settings_cache.get_setting = lambda setting_id, fallback='': fallback
	sys.modules['caches'] = caches
	sys.modules['caches.list_sort_cache'] = list_sort_cache
	sys.modules['caches.settings_cache'] = settings_cache


def _load_list_sort_module():
	_install_stubs()
	spec = importlib.util.spec_from_file_location('list_sort_migration_under_test', LIST_SORT_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


list_sort = _load_list_sort_module()


class LegacyCodeTranslationTests(unittest.TestCase):
	def test_all_sync_codes(self):
		expected = {'0': 'title:asc', '1': 'date_added:desc', '2': 'release_date:desc', '3': 'date_added:asc', '4': 'release_date:asc'}
		for code, spec_string in expected.items():
			self.assertEqual(spec_string, list_sort.LEGACY_SYNC_CODES[code], 'code %s' % code)

	def test_all_tmdb_codes(self):
		expected = {'0': 'title:asc', '1': 'release_date:asc', '2': 'release_date:desc', '3': 'random:asc', '4': 'default:asc'}
		for code, spec_string in expected.items():
			self.assertEqual(spec_string, list_sort.LEGACY_TMDB_CODES[code], 'code %s' % code)

	def test_all_mdblist_codes(self):
		# apis/mdblist_api.py:574-577 and :585-588 are three-way: 2 -> release date/year desc,
		# 1 -> watchlist_at/collected_at desc, everything else (0, 3, 4) -> title.
		expected = {'0': 'title:asc', '1': 'date_added:desc', '2': 'release_date:desc', '3': 'title:asc', '4': 'title:asc'}
		for code, spec_string in expected.items():
			self.assertEqual(spec_string, list_sort.LEGACY_MDBLIST_CODES[code], 'code %s' % code)


class MigrationTests(unittest.TestCase):
	def test_watchlist_seeds_both_defaults(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '1'})
		self.assertEqual('date_added:desc', result['defaults']['sort.default.movies'])
		self.assertEqual('date_added:desc', result['defaults']['sort.default.shows'])

	def test_absent_settings_use_the_old_getters_own_fallbacks(self):
		# An absent row is not "no preference": lists_sort_order fell back to code 0 (title) and
		# tmdblists_sort_order to code 4 (provider order), and lists were ordered by exactly that.
		result = list_sort.migrate_legacy_sort_settings({})
		self.assertEqual('title:asc', result['defaults']['sort.default.movies'])
		self.assertEqual('title:asc', result['defaults']['sort.default.shows'])
		for scope in ('mdblist.watchlist:movies', 'mdblist.watchlist:shows',
				'mdblist.collection:movies', 'mdblist.collection:shows'):
			self.assertEqual('title:asc', result['overrides'][scope], scope)
		self.assertEqual('default:asc', result['overrides']['tmdb:watchlist'])
		self.assertEqual('default:asc', result['overrides']['tmdb:favorites'])
		# Trakt collection and Simkl matched the title baseline, so they need no override.
		self.assertNotIn('trakt.collection:movies', result['overrides'])
		self.assertNotIn('simkl:movies', result['overrides'])

	def test_blank_stored_value_is_treated_as_absent(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '', 'tmdbsort.favorites': ''})
		self.assertEqual('title:asc', result['defaults']['sort.default.movies'])
		self.assertEqual('default:asc', result['overrides']['tmdb:favorites'])

	def test_absent_collection_keeps_title_not_the_watchlist_default(self):
		# The regression: with only sort.watchlist stored, the collection scopes used to inherit the
		# new global default seeded from it, where the old code gave them title.
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '1'})
		self.assertEqual('date_added:desc', result['defaults']['sort.default.movies'])
		self.assertEqual('title:asc', result['overrides']['trakt.collection:movies'])
		self.assertEqual('title:asc', result['overrides']['trakt.collection:shows'])
		self.assertEqual('title:asc', result['overrides']['simkl:movies'])
		self.assertEqual('title:asc', result['overrides']['simkl:shows'])
		self.assertEqual('default:asc', result['overrides']['tmdb:watchlist'])

	def test_matching_collection_produces_no_trakt_override(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '2', 'sort.collection': '2'})
		self.assertNotIn('trakt.collection:movies', result['overrides'])
		self.assertNotIn('trakt.collection:shows', result['overrides'])

	def test_differing_collection_overrides_trakt_only(self):
		# MDBList is no longer written from the Trakt collection branch; it has its own table.
		# Code 3 is deliberate: LEGACY_SYNC_CODES['3'] is date_added:asc but LEGACY_MDBLIST_CODES['3']
		# is title:asc, so this pins the separation. A code where the two tables agree (e.g. '1')
		# would pass whichever branch produced the value.
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '0', 'sort.collection': '3'})
		self.assertEqual('date_added:asc', result['overrides']['trakt.collection:movies'])
		self.assertEqual('date_added:asc', result['overrides']['trakt.collection:shows'])
		self.assertEqual('title:asc', result['overrides']['mdblist.collection:movies'])
		self.assertEqual('title:asc', result['overrides']['mdblist.collection:shows'])

	def test_differing_simkl_overrides_both_media_types(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '0', 'sort.simkl': '4'})
		self.assertEqual('release_date:asc', result['overrides']['simkl:movies'])
		self.assertEqual('release_date:asc', result['overrides']['simkl:shows'])

	def test_mdblist_scopes_are_always_written_explicitly(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '1', 'sort.collection': '1'})
		for scope in ('mdblist.watchlist:movies', 'mdblist.watchlist:shows',
				'mdblist.collection:movies', 'mdblist.collection:shows'):
			self.assertEqual('date_added:desc', result['overrides'][scope], scope)

	def test_mdblist_pins_title_for_codes_it_never_honoured(self):
		# Code 3 is date-added-ascending on Trakt but title on MDBList. The global default is
		# seeded from sort.watchlist, so without an explicit override MDBList would flip.
		for code in ('0', '3', '4'):
			result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': code, 'sort.collection': code})
			for scope in ('mdblist.watchlist:movies', 'mdblist.watchlist:shows',
					'mdblist.collection:movies', 'mdblist.collection:shows'):
				self.assertEqual('title:asc', result['overrides'][scope], '%s code %s' % (scope, code))

	def test_mdblist_watchlist_and_collection_translate_independently(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '3', 'sort.collection': '2'})
		self.assertEqual('title:asc', result['overrides']['mdblist.watchlist:movies'])
		self.assertEqual('title:asc', result['overrides']['mdblist.watchlist:shows'])
		self.assertEqual('release_date:desc', result['overrides']['mdblist.collection:movies'])
		self.assertEqual('release_date:desc', result['overrides']['mdblist.collection:shows'])

	def test_mdblist_ordering_preserved_when_legacy_setting_absent(self):
		# An absent sort.collection meant title (the old getter's '0' fallback). Leaving the scope
		# unwritten would let it inherit the global default seeded from sort.watchlist instead.
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '1'})
		self.assertEqual('date_added:desc', result['overrides']['mdblist.watchlist:movies'])
		self.assertEqual('title:asc', result['overrides']['mdblist.collection:movies'])
		self.assertEqual('title:asc', result['overrides']['mdblist.collection:shows'])

	def test_unknown_mdblist_code_writes_no_override(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '99'})
		self.assertNotIn('mdblist.watchlist:movies', result['overrides'])

	def test_tmdb_settings_always_become_overrides(self):
		result = list_sort.migrate_legacy_sort_settings({'tmdbsort.watchlist': '4', 'tmdbsort.favorites': '2'})
		self.assertEqual('default:asc', result['overrides']['tmdb:watchlist'])
		self.assertEqual('release_date:desc', result['overrides']['tmdb:favorites'])

	def test_unknown_code_is_ignored(self):
		result = list_sort.migrate_legacy_sort_settings({'sort.watchlist': '99'})
		self.assertEqual({}, result['defaults'])


class LegacyStoreTranslationTests(unittest.TestCase):
	def test_trakt_custom_sort_keys_remap(self):
		cases = {
			('added', 'desc'): 'date_added:desc',
			('popularity', 'asc'): 'votes:asc',
			('percentage', 'desc'): 'rating:desc',
			('released', 'asc'): 'release_date:asc',
			('rank', 'asc'): 'rank:asc',
			('runtime', 'desc'): 'runtime:desc',
			('title', 'desc'): 'title:desc',
			('random', 'asc'): 'random:asc',
			('default', 'asc'): 'default:asc',
		}
		for (sort_by, sort_how), expected in cases.items():
			self.assertEqual(expected, list_sort.translate_trakt_custom_sort(sort_by, sort_how), sort_by)

	def test_unknown_trakt_key_returns_empty(self):
		self.assertEqual('', list_sort.translate_trakt_custom_sort('nonsense', 'asc'))

	def test_personal_codes(self):
		expected = {'0': 'title:asc', '1': 'date_added:asc', '2': 'date_added:desc', '3': 'release_date:asc',
			'4': 'release_date:desc', '5': 'random:asc', 'None': 'default:asc', '': 'title:asc'}
		for code, spec_string in expected.items():
			self.assertEqual(spec_string, list_sort.LEGACY_PERSONAL_CODES[code], 'code %s' % code)


class RunMigrationTests(unittest.TestCase):
	# run_sort_migration() does a lazy `from caches.list_sort_cache import set_override`
	# inside the function body, so it resolves whatever is in sys.modules at call time —
	# not what was there when this file's module-level list_sort was built. Other test
	# files in this suite install their own competing 'caches.list_sort_cache' stub at
	# import time (e.g. test_list_sort_resolve.py's stub has no set_override), and
	# collection order can leave that stub in sys.modules by the time these tests run.
	# Reinstall our own stubs here so these tests are independent of collection order.
	def setUp(self):
		self._original_sys_modules = {}
		for key in ('caches', 'caches.list_sort_cache', 'caches.settings_cache'):
			if key in sys.modules:
				self._original_sys_modules[key] = sys.modules[key]
		_install_stubs()

	def tearDown(self):
		for key in ('caches', 'caches.list_sort_cache', 'caches.settings_cache'):
			if key in self._original_sys_modules:
				sys.modules[key] = self._original_sys_modules[key]
			else:
				sys.modules.pop(key, None)

	def test_writes_defaults_and_overrides(self):
		written_settings, written_overrides = {}, {}
		list_sort_cache = sys.modules['caches.list_sort_cache']
		list_sort_cache.set_override = lambda scope, spec: written_overrides.__setitem__(scope, spec) or True
		changed = list_sort.run_sort_migration({'sort.watchlist': '1', 'sort.collection': '2'},
			lambda setting_id, value: written_settings.__setitem__(setting_id, value))
		self.assertTrue(changed)
		self.assertEqual('date_added:desc', written_settings['sort.default.movies'])
		self.assertEqual('Date Added (descending)', written_settings['sort.default.movies_name'])
		self.assertEqual('release_date:desc', written_overrides['trakt.collection:movies'])

	def test_absent_legacy_settings_still_pin_the_documented_fallbacks(self):
		# There is no "nothing to migrate" case any more: a profile with no legacy rows was still
		# being ordered by the old getters' fallbacks, and those have to be written down.
		written_settings, written_overrides = {}, {}
		list_sort_cache = sys.modules['caches.list_sort_cache']
		list_sort_cache.set_override = lambda scope, spec: written_overrides.__setitem__(scope, spec) or True
		changed = list_sort.run_sort_migration({}, lambda setting_id, value: written_settings.__setitem__(setting_id, value))
		self.assertTrue(changed)
		self.assertEqual('title:asc', written_settings['sort.default.movies'])
		self.assertEqual('default:asc', written_overrides['tmdb:watchlist'])
		self.assertEqual('title:asc', written_overrides['mdblist.watchlist:movies'])

	def test_failed_override_raises_instead_of_reporting_success(self):
		# set_override() swallows its own exceptions and returns False, so an unwritable store
		# must not be reported as a clean migration.
		list_sort_cache = sys.modules['caches.list_sort_cache']
		list_sort_cache.set_override = lambda scope, spec: False
		with self.assertRaises(list_sort.SortMigrationError):
			list_sort.run_sort_migration({'sort.watchlist': '1'}, lambda setting_id, value: None)

	def test_every_override_is_attempted_before_raising(self):
		attempted = []
		list_sort_cache = sys.modules['caches.list_sort_cache']
		list_sort_cache.set_override = lambda scope, spec: attempted.append(scope) or (scope != 'simkl:movies')
		with self.assertRaises(list_sort.SortMigrationError):
			list_sort.run_sort_migration({'sort.watchlist': '1', 'sort.simkl': '2'}, lambda setting_id, value: None)
		self.assertIn('simkl:shows', attempted)
		self.assertIn('mdblist.watchlist:movies', attempted)


class SentinelTests(unittest.TestCase):
	def setUp(self):
		self._original_sys_modules = {}
		for key in ('modules', 'modules.kodi_utils', 'modules.settings', 'caches', 'caches.base_cache', 'caches.settings_cache'):
			if key in sys.modules:
				self._original_sys_modules[key] = sys.modules[key]

	def tearDown(self):
		for key in ('modules', 'modules.kodi_utils', 'modules.settings', 'caches', 'caches.base_cache', 'caches.settings_cache'):
			if key in self._original_sys_modules:
				sys.modules[key] = self._original_sys_modules[key]
			else:
				sys.modules.pop(key, None)

	def test_sentinel_declared_in_default_settings(self):
		from test_settings_cache_calendar_migration import _load_settings_cache_module
		module = _load_settings_cache_module()
		ids = [s['setting_id'] for s in module.default_settings()]
		self.assertIn('migration.unified_list_sort', ids)

	def test_new_sort_defaults_declared(self):
		from test_settings_cache_calendar_migration import _load_settings_cache_module
		module = _load_settings_cache_module()
		ids = [s['setting_id'] for s in module.default_settings()]
		for setting_id in ('sort.default.movies', 'sort.default.movies_name', 'sort.default.shows', 'sort.default.shows_name'):
			self.assertIn(setting_id, ids)

	def test_old_sort_ids_removed(self):
		from test_settings_cache_calendar_migration import _load_settings_cache_module
		module = _load_settings_cache_module()
		ids = [s['setting_id'] for s in module.default_settings()]
		for setting_id in ('sort.watchlist', 'sort.collection', 'sort.simkl', 'tmdbsort.watchlist', 'tmdbsort.favorites'):
			self.assertNotIn(setting_id, ids)

	def test_progress_and_watched_sorts_survive(self):
		from test_settings_cache_calendar_migration import _load_settings_cache_module
		module = _load_settings_cache_module()
		ids = [s['setting_id'] for s in module.default_settings()]
		self.assertIn('sort.progress', ids)
		self.assertIn('sort.watched', ids)


class SettingsDbEmptinessTests(unittest.TestCase):
	"""is_empty_strict() is the only thing separating "fresh install" from "database unavailable".

	Run against real sqlite, because the distinction that matters is which sqlite outcomes count as
	empty: a missing file and a missing table are genuinely empty, a locked database is not, and
	get_all() renders all three as {}.
	"""
	_KEYS = ('modules', 'modules.kodi_utils', 'modules.settings', 'caches', 'caches.base_cache')

	def setUp(self):
		self._original_sys_modules = dict((k, sys.modules[k]) for k in self._KEYS if k in sys.modules)
		from test_settings_cache_calendar_migration import _load_settings_cache_module
		self.module = _load_settings_cache_module()

	def tearDown(self):
		for key in self._KEYS:
			if key in self._original_sys_modules: sys.modules[key] = self._original_sys_modules[key]
			else: sys.modules.pop(key, None)

	def _store(self, exists, connection):
		self.module.kodi_utils.path_exists = lambda path: exists
		self.module.connect_database = lambda name: connection
		return self.module.settings_cache

	def test_a_missing_database_file_is_empty(self):
		self.assertTrue(self._store(False, None).is_empty_strict())

	def test_a_missing_table_is_empty(self):
		connection = sqlite3.connect(':memory:')
		self.assertTrue(self._store(True, connection).is_empty_strict())

	def test_an_empty_table_is_empty(self):
		connection = sqlite3.connect(':memory:')
		connection.execute('CREATE TABLE settings (setting_id text, setting_type text, setting_default text, setting_value text)')
		self.assertTrue(self._store(True, connection).is_empty_strict())

	def test_a_populated_table_is_not_empty(self):
		connection = sqlite3.connect(':memory:')
		connection.execute('CREATE TABLE settings (setting_id text, setting_type text, setting_default text, setting_value text)')
		connection.execute("INSERT INTO settings VALUES ('sort.watchlist', 'action', '0', '3')")
		self.assertFalse(self._store(True, connection).is_empty_strict())

	def test_an_unreadable_database_raises_instead_of_reporting_empty(self):
		"""The whole point. get_all() answers {} here and cannot be told apart from a fresh install."""
		class _Locked:
			@staticmethod
			def execute(*args):
				raise sqlite3.OperationalError('database is locked')
		with self.assertRaises(sqlite3.OperationalError):
			self._store(True, _Locked).is_empty_strict()

	def test_a_corrupt_database_raises_instead_of_reporting_empty(self):
		class _Corrupt:
			@staticmethod
			def execute(*args):
				raise sqlite3.DatabaseError('database disk image is malformed')
		with self.assertRaises(sqlite3.DatabaseError):
			self._store(True, _Corrupt).is_empty_strict()


class SyncSettingsMigrationTests(unittest.TestCase):
	"""End-to-end: run the real sync_settings() over a fake profile holding legacy sort settings.

	The primary guard here is the obsolete purge deferral: sync_settings() would otherwise delete
	sort.watchlist and friends as obsolete ids before the migration block runs, so a failed run
	would destroy the only copy of the user's per-list orderings. These tests pin that deferral.

	The pre-purge legacy_sort_settings snapshot is secondary and deliberately unpinned - with the
	deferral in place, passing currentsettings directly leaves every test here green. It is kept as
	belt-and-braces for the day the deferral is removed, not because anything covers it.
	"""
	_KEYS = ('modules', 'modules.kodi_utils', 'modules.settings', 'modules.list_sort',
		'caches', 'caches.base_cache', 'caches.settings_cache', 'caches.list_sort_cache',
		'caches.trakt_cache', 'caches.tmdb_lists', 'caches.personal_lists_cache')

	def setUp(self):
		self._original_sys_modules = {}
		for key in self._KEYS:
			if key in sys.modules:
				self._original_sys_modules[key] = sys.modules[key]
		from test_settings_cache_calendar_migration import _load_settings_cache_module
		self.module = _load_settings_cache_module()
		# _load_settings_cache_module() replaces sys.modules['caches'], so install the modules the
		# lazy imports inside the migration resolve at call time afterwards, not before.
		self.overrides = {}
		self.override_result = True
		self.failing_scopes = ()
		self.trakt_rows, self.personal_rows, self.tmdb_rows = {}, {}, {}
		self.stores_raise = False
		self.failing_stores = set()
		list_sort_cache = types.ModuleType('caches.list_sort_cache')
		list_sort_cache.set_override = self._set_override
		sys.modules['caches.list_sort_cache'] = list_sort_cache
		sys.modules['modules.list_sort'] = list_sort
		# The three legacy per-list stores. Without them the store migration would fail on an
		# ImportError on every run here, which is exactly the condition these tests pin.
		#
		# Each store is stubbed twice, exactly as production has it: a _strict getter that raises when
		# the store is unreadable, and - for Trakt and TMDb - the swallowing sibling the UI calls,
		# which answers {} no matter what. The swallowing stub deliberately never raises, so a
		# migration that reached for it would see "nothing stored" and set the sentinel; that is what
		# test_unreadable_legacy_store_leaves_the_sentinel_unset detects.
		trakt_cache = types.ModuleType('caches.trakt_cache')
		trakt_cache.get_all_lists_custom_sort_strict = lambda: self._store_rows('trakt', self.trakt_rows)
		trakt_cache.get_all_lists_custom_sort = lambda: self._swallowed_rows('trakt', self.trakt_rows)
		sys.modules['caches.trakt_cache'] = trakt_cache
		tmdb_lists = types.ModuleType('caches.tmdb_lists')
		tmdb_lists.tmdb_lists_cache = types.SimpleNamespace(
			get_sort_orders_strict=lambda: self._store_rows('tmdb', self.tmdb_rows),
			get_sort_orders=lambda: self._swallowed_rows('tmdb', self.tmdb_rows))
		sys.modules['caches.tmdb_lists'] = tmdb_lists
		personal_lists_cache = types.ModuleType('caches.personal_lists_cache')
		personal_lists_cache.personal_lists_cache = types.SimpleNamespace(
			get_all_sort_orders=lambda: self._store_rows('personal', self.personal_rows))
		sys.modules['caches.personal_lists_cache'] = personal_lists_cache

	def _unreadable(self, name):
		return self.stores_raise or name in self.failing_stores

	def _store_rows(self, name, rows):
		if self._unreadable(name): raise RuntimeError('legacy store unavailable')
		return dict(rows)

	def _swallowed_rows(self, name, rows):
		"""What the UI-facing getters do: an unreadable store is indistinguishable from an empty one."""
		if self._unreadable(name): return {}
		return dict(rows)

	def tearDown(self):
		for key in self._KEYS:
			if key in self._original_sys_modules:
				sys.modules[key] = self._original_sys_modules[key]
			else:
				sys.modules.pop(key, None)

	def _set_override(self, scope, spec_string):
		if not self.override_result or scope in self.failing_scopes: return False
		self.overrides[scope] = spec_string
		return True

	def _sync(self, initial, db_readable=True):
		from test_settings_cache_calendar_migration import FakeSettingsCache
		cache = FakeSettingsCache(initial, db_readable=db_readable)
		self.module.settings_cache = cache
		return self._resync(cache)

	def _resync(self, cache):
		self.module.settings_cache = cache
		result = self.module.sync_settings({'silent': 'true', 'load_properties': 'false', 'force': 'true'})
		self.assertEqual('synced', result)
		return cache

	def test_an_unreadable_settings_db_does_not_seed_the_sentinel(self):
		"""get_all() swallows a locked database and answers {}, which reads as a fresh install. Seeding
		the sentinel there is permanent: the next healthy sync sees 'true', never runs the migration,
		and the obsolete purge then deletes the five legacy ids and the user's only stored orderings
		with them. The three legacy per-list stores would never be read by anything again."""
		cache = self._sync({'sort.watchlist': '3', 'tmdbsort.watchlist': '2'}, db_readable=False)
		self.assertNotEqual('true', cache.data.get('migration.unified_list_sort'))
		self.assertEqual({}, self.overrides)

		# The very next healthy sync must still migrate, with the legacy ids intact.
		cache.db_readable = True
		self._resync(cache)
		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual('date_added:asc', cache.data['sort.default.movies'])
		self.assertEqual('release_date:desc', self.overrides['tmdb:watchlist'])

	def test_an_unreadable_settings_db_on_an_empty_profile_still_defers(self):
		"""Nothing distinguishes this from the case above at the moment sync_settings runs - which is
		the whole point. Deferring costs one extra sync; seeding costs the user's preferences."""
		cache = self._sync({}, db_readable=False)
		self.assertNotEqual('true', cache.data.get('migration.unified_list_sort'))

	def test_a_genuinely_empty_settings_db_seeds_the_sentinel(self):
		"""The other half: is_empty_strict() answering True without raising is a real fresh install,
		and the sentinel has to be seeded or the migration writes six overrides nobody asked for."""
		cache = self._sync({})
		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual({}, self.overrides)

	def test_legacy_values_migrate_then_purge_on_the_following_sync(self):
		cache = self._sync({
			'sort.watchlist': '3',
			'sort.collection': '2',
			'sort.simkl': '4',
			'tmdbsort.watchlist': '4',
		})

		# Fails if the purge deferral is removed: sort.watchlist would be deleted as an obsolete id
		# before the migration block runs, so the defaults would never be written. The pre-purge
		# snapshot is not what is being pinned here - it is redundant while the deferral holds.
		self.assertEqual('date_added:asc', cache.data['sort.default.movies'])
		self.assertEqual('date_added:asc', cache.data['sort.default.shows'])
		self.assertEqual('Date Added (ascending)', cache.data['sort.default.movies_name'])
		self.assertEqual('Date Added (ascending)', cache.data['sort.default.shows_name'])
		self.assertEqual('release_date:desc', self.overrides['trakt.collection:movies'])
		self.assertEqual('release_date:desc', self.overrides['trakt.collection:shows'])
		self.assertEqual('release_date:asc', self.overrides['simkl:movies'])
		self.assertEqual('release_date:asc', self.overrides['simkl:shows'])
		self.assertEqual('default:asc', self.overrides['tmdb:watchlist'])
		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		# The legacy ids are held back from the obsolete purge until the sentinel is set, so they
		# are still present on the run that migrates them and only disappear on the next sync.
		self.assertIn('sort.watchlist', cache.data)
		self.assertIn('tmdbsort.watchlist', cache.data)
		self.module.settings_cache = cache
		self.assertEqual('synced', self.module.sync_settings({'silent': 'true', 'load_properties': 'false', 'force': 'true'}))
		self.assertNotIn('sort.watchlist', cache.data)
		self.assertNotIn('tmdbsort.watchlist', cache.data)

	def test_mdblist_ordering_is_pinned_not_inherited(self):
		# Old code 3 is date-added-ascending on Trakt but title on MDBList.
		cache = self._sync({'sort.watchlist': '3', 'sort.collection': '3'})

		self.assertEqual('date_added:asc', cache.data['sort.default.movies'])
		for scope in ('mdblist.watchlist:movies', 'mdblist.watchlist:shows',
				'mdblist.collection:movies', 'mdblist.collection:shows'):
			self.assertEqual('title:asc', self.overrides[scope], scope)

	def test_sentinel_not_set_when_overrides_cannot_persist(self):
		self.override_result = False
		cache = self._sync({'sort.watchlist': '1', 'sort.collection': '2'})

		self.assertNotEqual('true', cache.data.get('migration.unified_list_sort'))
		self.assertEqual('false', cache.data['migration.unified_list_sort'])

	def test_failed_migration_is_retried_for_real_on_the_next_sync(self):
		# The retry is only real if the legacy ids survive the failed pass. Without the purge
		# deferral they are deleted on the first sync, the second sync migrates an empty snapshot
		# and flips the sentinel - the door closes instead of reopening, and every per-list
		# override is lost permanently.
		self.override_result = False
		cache = self._sync({'sort.watchlist': '1', 'sort.collection': '2', 'tmdbsort.watchlist': '3'})
		self.assertEqual('false', cache.data['migration.unified_list_sort'])
		self.assertEqual({}, self.overrides)
		self.assertIn('sort.collection', cache.data)

		self.override_result = True
		self.module.settings_cache = cache
		self.assertEqual('synced', self.module.sync_settings({'silent': 'true', 'load_properties': 'false', 'force': 'true'}))

		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual('release_date:desc', self.overrides['trakt.collection:movies'])
		self.assertEqual('release_date:desc', self.overrides['trakt.collection:shows'])
		self.assertEqual('release_date:desc', self.overrides['mdblist.collection:movies'])
		self.assertEqual('date_added:desc', self.overrides['mdblist.watchlist:movies'])
		self.assertEqual('random:asc', self.overrides['tmdb:watchlist'])
		self.assertEqual('date_added:desc', cache.data['sort.default.movies'])

	def test_profile_with_no_legacy_rows_pins_the_old_fallbacks(self):
		cache = self._sync({'general.check_settings_file': 'true'})

		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual('title:asc', cache.data['sort.default.movies'])
		self.assertEqual('title:asc', cache.data['sort.default.shows'])
		self.assertEqual('title:asc', self.overrides['mdblist.watchlist:movies'])
		self.assertEqual('default:asc', self.overrides['tmdb:watchlist'])
		self.assertEqual('default:asc', self.overrides['tmdb:favorites'])
		self.assertNotIn('trakt.collection:movies', self.overrides)
		self.assertNotIn('simkl:movies', self.overrides)

	def test_fresh_install_writes_no_overrides_at_all(self):
		# A brand-new profile has nothing to migrate, but the migration reads absent legacy ids as
		# their old fallbacks and would pin six overrides for a user who never touched a sort setting.
		# The sentinel is seeded 'true' on the fresh-install pass so the block never runs here.
		cache = self._sync({})
		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual({}, self.overrides)

		self.module.settings_cache = cache
		self.assertEqual('synced', self.module.sync_settings({'silent': 'true', 'load_properties': 'false', 'force': 'true'}))
		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual({}, self.overrides)

	def test_legacy_store_rows_migrate_alongside_the_settings(self):
		self.trakt_rows = {'12345': {'sort_by': 'rank', 'sort_how': 'asc'}}
		self.personal_rows = {('Faves', 'jo'): '2'}
		self.tmdb_rows = {8675309: 'None'}
		cache = self._sync({'sort.watchlist': '1'})

		self.assertEqual('rank:asc', self.overrides['trakt.list:12345'])
		self.assertEqual('date_added:desc', self.overrides['personal:Faves|jo'])
		self.assertEqual('default:asc', self.overrides['tmdb:8675309'])
		self.assertEqual('true', cache.data['migration.unified_list_sort'])

	def test_unreadable_legacy_store_leaves_the_sentinel_unset(self):
		# The sentinel is the one-way door: writing it on a run that saved nothing discards every
		# per-list Trakt, personal and TMDb preference, because the legacy ids are purged next sync.
		# Only the _strict getters raise here; the swallowing siblings answer {} exactly as they do
		# in production, so this also fails if the migration reads the store through those.
		self.stores_raise = True
		cache = self._sync({'sort.watchlist': '1'})

		self.assertEqual('false', cache.data['migration.unified_list_sort'])

	def test_any_single_unreadable_store_leaves_the_sentinel_unset(self):
		# One locked database out of three is enough: the rows in it are unrecoverable once the
		# sentinel is set. Pinned per store because each is read through a different getter and a
		# swallowing one would leave that store's preferences silently dropped.
		for store in ('trakt', 'personal', 'tmdb'):
			with self.subTest(store=store):
				self.overrides = {}
				self.failing_stores = {store}
				self.trakt_rows = {'12345': {'sort_by': 'rank', 'sort_how': 'asc'}}
				self.personal_rows = {('Faves', 'jo'): '2'}
				self.tmdb_rows = {8675309: '2'}
				cache = self._sync({'sort.watchlist': '1'})
				self.assertEqual('false', cache.data['migration.unified_list_sort'])

	def test_unreadable_store_is_not_mistaken_for_an_empty_one(self):
		# The regression this guards: the store getters used to end in `except: return {}`, so a
		# locked database produced an empty translation, no failure, and a permanent sentinel.
		self.stores_raise = True
		cache = self._sync({'sort.watchlist': '1'})
		self.assertEqual('false', cache.data['migration.unified_list_sort'])

		# Same profile, same empty result - but the stores really are empty this time.
		self.stores_raise = False
		self.module.settings_cache = cache
		self.assertEqual('synced', self.module.sync_settings({'silent': 'true', 'load_properties': 'false', 'force': 'true'}))
		self.assertEqual('true', cache.data['migration.unified_list_sort'])

	def test_unpersisted_store_override_leaves_the_sentinel_unset(self):
		# set_override() swallows its own exceptions and reports False, so an ignored return value
		# looks exactly like a clean success.
		self.trakt_rows = {'12345': {'sort_by': 'rank', 'sort_how': 'asc'}}
		self.failing_scopes = ('trakt.list:12345',)
		cache = self._sync({'sort.watchlist': '1'})

		self.assertEqual('false', cache.data['migration.unified_list_sort'])
		self.assertNotIn('trakt.list:12345', self.overrides)

	def test_failed_store_migration_is_retried_for_real_on_the_next_sync(self):
		self.stores_raise = True
		self.trakt_rows = {'12345': {'sort_by': 'rank', 'sort_how': 'asc'}}
		cache = self._sync({'sort.watchlist': '1', 'sort.collection': '2'})
		self.assertEqual('false', cache.data['migration.unified_list_sort'])
		self.assertNotIn('trakt.list:12345', self.overrides)
		self.assertIn('sort.collection', cache.data)

		self.stores_raise = False
		self.module.settings_cache = cache
		self.assertEqual('synced', self.module.sync_settings({'silent': 'true', 'load_properties': 'false', 'force': 'true'}))

		self.assertEqual('true', cache.data['migration.unified_list_sort'])
		self.assertEqual('rank:asc', self.overrides['trakt.list:12345'])
		self.assertEqual('release_date:desc', self.overrides['trakt.collection:movies'])

	def test_migration_does_not_rerun_once_the_sentinel_is_set(self):
		cache = self._sync({'sort.watchlist': '1', 'migration.unified_list_sort': 'true'})

		self.assertEqual({}, self.overrides)
		self.assertEqual('title:asc', cache.data['sort.default.movies'])


class LegacyStoreGetterTests(unittest.TestCase):
	"""The migration's three data sources, loaded for real, against a real sqlite3(':memory:')
	database seeded from the production schema - not a fake that discards the SQL and hands back
	fixture rows unconditionally. That would leave the SELECT column list, its column order, and
	the LIKE filters completely unexercised, which is exactly the kind of bug this class exists to
	catch: see e.g. test_personal_sort_order_getter_keeps_name_and_author_in_order below, which
	fails if `SELECT name, author, sort_order` silently became `SELECT author, name, sort_order`.

	The whole retry story rests on these getters distinguishing "nothing stored" from "could not
	read". Every one of them used to end in `except: return {}`, which makes a locked database or a
	corrupt row look exactly like an empty store - the migration then translates nothing, reports
	success, and the sentinel is written over preferences that were never read.
	"""
	_KEYS = ('modules', 'modules.kodi_utils', 'caches', 'caches.base_cache')

	def setUp(self):
		self._original_sys_modules = {}
		for key in self._KEYS:
			if key in sys.modules: self._original_sys_modules[key] = sys.modules[key]
		self.locked = False
		self.conn = sqlite3.connect(':memory:')
		modules = types.ModuleType('modules')
		modules.__path__ = []
		kodi_utils = types.ModuleType('modules.kodi_utils')
		for name in ('sleep', 'confirm_dialog', 'close_all_dialog', 'notification', 'logger'):
			setattr(kodi_utils, name, lambda *a, **k: None)
		modules.kodi_utils = kodi_utils
		sys.modules['modules'] = modules
		sys.modules['modules.kodi_utils'] = kodi_utils

		# Pull the real CREATE TABLE statements from the real base_cache module rather than
		# hand-copying them, so the seeded schema cannot drift from production. Loading it here
		# needs only the modules.kodi_utils stub just installed above.
		real_base_cache = self._load('base_cache')
		self._table_creators = real_base_cache.table_creators()

		caches = types.ModuleType('caches')
		caches.__path__ = []
		base_cache = types.ModuleType('caches.base_cache')
		base_cache.connect_database = lambda *a, **k: self._connect()
		base_cache.get_timestamp = lambda *a, **k: 0
		test_case = self

		class _BaseCache(object):
			def __init__(self, *args, **kwargs): pass
			def manual_connect(self, *args, **kwargs): return test_case._connect()

		base_cache.BaseCache = _BaseCache
		sys.modules['caches'] = caches
		sys.modules['caches.base_cache'] = base_cache

	def tearDown(self):
		self.conn.close()
		for key in self._KEYS:
			if key in self._original_sys_modules: sys.modules[key] = self._original_sys_modules[key]
			else: sys.modules.pop(key, None)

	def _connect(self):
		if self.locked: raise RuntimeError('database is locked')
		return self.conn

	def _load(self, name):
		path = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'caches' / ('%s.py' % name)
		spec = importlib.util.spec_from_file_location('legacy_store_%s_under_test' % name, str(path))
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		return module

	def _create_table(self, db_name):
		for statement in self._table_creators[db_name]: self.conn.execute(statement)

	def _seed_trakt(self, rows):
		"""rows: (id, data) tuples inserted with a real INSERT, exactly as trakt_cache.py writes them."""
		self._create_table('trakt_db')
		self.conn.executemany('INSERT INTO trakt_data (id, data) VALUES (?, ?)', rows)
		self.conn.commit()

	def _seed_tmdb(self, rows):
		"""rows: (id, data) tuples inserted with a real INSERT, exactly as TMDbListsCache.set_sort_order writes them."""
		self._create_table('tmdb_lists_db')
		self.conn.executemany('INSERT INTO tmdb_lists (id, data, expires) VALUES (?, ?, 0)', rows)
		self.conn.commit()

	def _seed_personal(self, rows):
		"""rows: (name, author, sort_order, description) tuples. sort_order is passed as the string
		PersonalListsCache.make_list() would actually receive from the legacy sort-order URL
		parameter; the personal_lists table declares that column `integer`, so SQLite's column
		affinity converts the stored '2' to the int 2 on insert - real production behaviour that
		migrate_legacy_stores()'s str() coercion depends on. Inserting an int here directly would
		paper over that instead of exercising it."""
		self._create_table('personal_lists_db')
		for name, author, sort_order, description in rows:
			self.conn.execute(
				'INSERT INTO personal_lists (name, contents, total, created, sort_order, description, seen, poster, fanart, author, updated) '
				'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
				(name, '[]', 0, '0', sort_order, description, 'false', '', '', author, '0'))
		self.conn.commit()

	def test_trakt_strict_getter_raises_on_a_locked_database(self):
		module = self._load('trakt_cache')
		self.locked = True
		with self.assertRaises(Exception):
			module.get_all_lists_custom_sort_strict()
		# The UI-facing sibling keeps swallowing - that is why the migration may not use it.
		self.assertEqual({}, module.get_all_lists_custom_sort())

	def test_trakt_strict_getter_raises_on_a_corrupt_row(self):
		# get_all_lists_custom_sort_strict() eval()s each row. One unparseable row must not read as
		# an empty store: the remaining lists' sorts are still in there and are still recoverable.
		self._seed_trakt([
			('trakt_list_custom_sort_1', "{'list_id': '1', 'sort_by': 'rank', 'sort_how': 'asc'}"),
			('trakt_list_custom_sort_2', 'not a dict {'),
		])
		module = self._load('trakt_cache')
		with self.assertRaises(Exception):
			module.get_all_lists_custom_sort_strict()
		self.assertEqual({}, module.get_all_lists_custom_sort())

	def test_trakt_getters_agree_on_a_readable_store(self):
		self._seed_trakt([
			('trakt_list_custom_sort_1', "{'list_id': '1', 'sort_by': 'rank', 'sort_how': 'asc'}"),
		])
		module = self._load('trakt_cache')
		expected = {'1': {'sort_by': 'rank', 'sort_how': 'asc'}}
		self.assertEqual(expected, module.get_all_lists_custom_sort_strict())
		self.assertEqual(expected, module.get_all_lists_custom_sort())

	def test_trakt_getter_filters_out_rows_that_are_not_custom_sorts(self):
		# Kills the mutant that drops "WHERE id LIKE 'trakt_list_custom_sort_%'": without the
		# filter, every row cached under trakt_data - including unrelated ones that merely happen
		# to eval() to a dict - would be folded into the migrated result.
		self._seed_trakt([
			('trakt_list_custom_sort_1', "{'list_id': '1', 'sort_by': 'rank', 'sort_how': 'asc'}"),
			('trakt_get_activity', "{'list_id': 'decoy', 'sort_by': 'title', 'sort_how': 'asc'}"),
		])
		module = self._load('trakt_cache')
		expected = {'1': {'sort_by': 'rank', 'sort_how': 'asc'}}
		self.assertEqual(expected, module.get_all_lists_custom_sort_strict())

	def test_tmdb_strict_getter_raises_on_a_locked_database(self):
		module = self._load('tmdb_lists')
		cache = module.TMDbListsCache()
		self.locked = True
		with self.assertRaises(Exception):
			cache.get_sort_orders_strict()
		self.assertEqual({}, cache.get_sort_orders())

	def test_tmdb_strict_getter_raises_on_a_malformed_row(self):
		self._seed_tmdb([('sort_order_notanumber', '2')])
		module = self._load('tmdb_lists')
		cache = module.TMDbListsCache()
		with self.assertRaises(Exception):
			cache.get_sort_orders_strict()
		self.assertEqual({}, cache.get_sort_orders())

	def test_tmdb_getters_agree_on_a_readable_store(self):
		self._seed_tmdb([('sort_order_8675309', '2')])
		module = self._load('tmdb_lists')
		cache = module.TMDbListsCache()
		self.assertEqual({8675309: '2'}, cache.get_sort_orders_strict())
		self.assertEqual({8675309: '2'}, cache.get_sort_orders())

	def test_tmdb_getter_filters_out_rows_that_are_not_sort_orders(self):
		# Kills the mutant that drops "WHERE id LIKE 'sort_order_%'": a stray tmdb_lists row (e.g.
		# the cached user-lists payload) has an id with no 'sort_order_' prefix, so once the filter
		# is gone int(id.replace('sort_order_', '')) raises on it - with the strict getter, that
		# means the migration is stuck raising forever instead of reading the row that matters.
		self._seed_tmdb([
			('sort_order_8675309', '2'),
			('get_user_lists', '[]'),
		])
		module = self._load('tmdb_lists')
		cache = module.TMDbListsCache()
		self.assertEqual({8675309: '2'}, cache.get_sort_orders_strict())

	def test_personal_sort_order_getter_raises_on_a_locked_database(self):
		# This one has no swallowing sibling: its only caller is the migration.
		module = self._load('personal_lists_cache')
		self.locked = True
		with self.assertRaises(Exception):
			module.personal_lists_cache.get_all_sort_orders()

	def test_personal_sort_order_getter_reads_the_store(self):
		self._seed_personal([('Faves', 'jo', '2', 'a description, not a sort order')])
		module = self._load('personal_lists_cache')
		result = module.personal_lists_cache.get_all_sort_orders()
		# sort_order comes back as the int 2, not the string '2' that was inserted - SQLite integer
		# affinity on the column, not something this fake should paper over.
		self.assertEqual({('Faves', 'jo'): 2}, result)

	def test_personal_sort_order_getter_keeps_name_and_author_in_order(self):
		# Kills the mutant "SELECT name, author, sort_order" -> "SELECT author, name, sort_order":
		# with name != author, a column swap silently inverts every migrated scope key, e.g.
		# 'personal:jo|Faves' instead of 'personal:Faves|jo', so no migrated override is ever found.
		self._seed_personal([('Faves', 'jo', '2', 'irrelevant')])
		module = self._load('personal_lists_cache')
		result = module.personal_lists_cache.get_all_sort_orders()
		self.assertIn(('Faves', 'jo'), result)
		self.assertNotIn(('jo', 'Faves'), result)

	def test_personal_sort_order_getter_reads_sort_order_not_description(self):
		# Kills the mutant "SELECT name, author, sort_order" -> "SELECT name, author, description":
		# every personal list would migrate under a garbage code and be dropped as unmappable.
		self._seed_personal([('Faves', 'jo', '2', 'this text must never be read as a sort order')])
		module = self._load('personal_lists_cache')
		result = module.personal_lists_cache.get_all_sort_orders()
		self.assertEqual(2, result[('Faves', 'jo')])


if __name__ == '__main__':
	unittest.main()
