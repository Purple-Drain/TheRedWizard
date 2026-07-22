"""remove_old_databases() must never delete a database the addon still uses.

The allowlist it checks against used to be hand-maintained, and it had drifted: four entries were
database *keys* ('personal_lists_db', 'random_widgets_db', 'episode_groups_db') rather than the
filenames list_dirs() actually returns, so personal_lists.db and random_widgets.db fell outside the
allowlist and would have been deleted as stale. The invariant that matters is not "these twenty
names are right" but "every file locations() names is current", which is what these tests pin.

base_cache is compiled straight out of caches/base_cache.py against stub globals rather than
imported: the module pulls in modules.kodi_utils and therefore the whole Kodi runtime, while the two
functions under test only touch kodi_utils.addon_profile / list_dirs / delete_file.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_CACHE = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'caches' / 'base_cache.py'

_WANTED = ('locations', 'remove_old_databases')


class _KodiUtils:
	"""The slice of modules.kodi_utils the two functions touch."""

	def __init__(self, files):
		self.files = list(files)
		self.deleted = []

	@staticmethod
	def addon_profile(): return 'profile/'

	def list_dirs(self, _path): return ([], list(self.files))

	def delete_file(self, filepath): self.deleted.append(filepath)


def _load(kodi_utils):
	with open(str(BASE_CACHE), 'r', encoding='utf-8') as f:
		tree = ast.parse(f.read())
	body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in _WANTED]
	if len(body) != len(_WANTED): raise AssertionError('expected exactly one def of each of %s' % (_WANTED,))
	module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
	namespace = {'kodi_utils': kodi_utils, 'path': __import__('os').path}
	exec(compile(module, '<base_cache>', 'exec'), namespace)
	return namespace


class RemoveOldDatabasesTests(unittest.TestCase):
	def test_every_database_locations_names_is_treated_as_current(self):
		"""The invariant. Add a database to locations() and it is protected for free."""
		namespace = _load(_KodiUtils([]))
		filenames = list(namespace['locations']().values())
		kodi_utils = _KodiUtils(filenames)
		namespace = _load(kodi_utils)
		namespace['remove_old_databases']()
		self.assertEqual([], kodi_utils.deleted)

	def test_the_two_databases_the_stale_allowlist_missed_survive(self):
		"""The regression itself: both files were named by key, not filename, and were deleted."""
		kodi_utils = _KodiUtils(['personal_lists.db', 'random_widgets.db', 'episode_groups.db', 'list_sort.db'])
		_load(kodi_utils)['remove_old_databases']()
		self.assertEqual([], kodi_utils.deleted)

	def test_no_allowlist_entry_is_a_database_key(self):
		"""list_dirs() yields filenames, so a key in the allowlist protects nothing and hides a file."""
		namespace = _load(_KodiUtils([]))
		locations = namespace['locations']()
		kodi_utils = _KodiUtils(list(locations.keys()))
		_load(kodi_utils)['remove_old_databases']()
		self.assertEqual(sorted('profile/databases/' + k for k in locations),
			sorted(kodi_utils.deleted))

	def test_a_genuinely_stale_file_is_still_deleted(self):
		"""Widening the allowlist must not turn the function into a no-op."""
		namespace = _load(_KodiUtils([]))
		filenames = list(namespace['locations']().values())
		kodi_utils = _KodiUtils(filenames + ['retired.db'])
		_load(kodi_utils)['remove_old_databases']()
		self.assertEqual(['profile/databases/retired.db'], kodi_utils.deleted)

	def test_every_location_is_a_db_filename(self):
		"""A key that looks like a filename would slip past the test above."""
		locations = _load(_KodiUtils([]))['locations']()
		for key, filename in locations.items():
			self.assertTrue(filename.endswith('.db'), '%s -> %s' % (key, filename))
			self.assertNotIn(filename, locations, '%s -> %s is a key, not a filename' % (key, filename))
		self.assertEqual(len(locations), len(set(locations.values())))


if __name__ == '__main__':
	unittest.main()
