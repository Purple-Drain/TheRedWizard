"""get_trakt_list_contents' cache row has one shape, whoever asks for it.

The disk cache key is built from list_type/user/slug/list_id only - it does not encode the `method`
the fetch was made with. get_trakt() returns a bare list for method=None and a
{'sort_by','sort_how','data'} dict for method='sort_by_headers', so two callers asking for the same
list with different methods write and read incompatible rows under one key. That is what happened
when the list builder still carried sort_by/sort_how in its folder URL while trakt_image_maker,
which has no URL to carry them in, called with the default: the builder's row was a dict, the
custom-sort branch skipped the unwrapping, and the enumerate() below iterated the dict's three keys
and did i['type'] on a string. Swallowed by the enclosing try - the list simply rendered empty.

The function is compiled straight out of apis/trakt_api.py against stub globals rather than
imported: the module pulls in the whole Kodi runtime, but this one function only touches
trakt_cache, get_trakt, list_sort and settings.
"""
import ast
import sys
import unittest

from test_trakt_sync_list_sort import ROOT, list_sort, _install_stubs, OVERRIDES, SETTINGS

_STUBBED_MODULES = ('caches', 'caches.list_sort_cache', 'caches.settings_cache', 'modules', 'modules.settings')

TRAKT_API = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'apis' / 'trakt_api.py'
TRAKT_LISTS = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'indexers' / 'trakt_lists.py'

# Ranks are deliberately out of payload order, so "sorted by rank" and "handed back untouched" are
# different sequences and a fallback that quietly stopped working would be visible here.
ROWS = [
	{'type': 'movie', 'rank': 2, 'movie': {'ids': {'trakt': 2}, 'title': 'Banana', 'released': '2001-01-01'}},
	{'type': 'movie', 'rank': 3, 'movie': {'ids': {'trakt': 3}, 'title': 'Alpha', 'released': '1999-01-01'}},
	{'type': 'movie', 'rank': 1, 'movie': {'ids': {'trakt': 1}, 'title': 'Cherry', 'released': '2010-01-01'}},
]

PAYLOAD_ORDER = ['Banana', 'Alpha', 'Cherry']
RANK_ASC = ['Cherry', 'Banana', 'Alpha']


def _tree(path):
	with open(str(path), 'r', encoding='utf-8') as f:
		return ast.parse(f.read())


def _function(path, name):
	found = [n for n in ast.walk(_tree(path)) if isinstance(n, ast.FunctionDef) and n.name == name]
	if len(found) != 1: raise AssertionError('expected exactly one def %s in %s' % (name, path.name))
	return found[0]


class _Harness:
	"""get_trakt_list_contents compiled against stubs, plus the cache params it asked for."""

	def __init__(self, cache_row):
		self.cache_row = cache_row
		self.requested = []
		node = _function(TRAKT_API, 'get_trakt_list_contents')
		module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))

		class _TraktCache:
			@staticmethod
			def cache_trakt_object(function, string, params):
				self.requested.append({'string': string, 'params': params})
				return self.cache_row

		class _Settings:
			@staticmethod
			def ignore_articles(): return False

		namespace = {'trakt_cache': _TraktCache, 'get_trakt': lambda *a, **k: None,
			'list_sort': list_sort, 'settings': _Settings}
		exec(compile(module, '<get_trakt_list_contents>', 'exec'), namespace)
		self.call = namespace['get_trakt_list_contents']

	def titles(self, *args, **kwargs):
		return [i['title'] for i in self.call(*args, **kwargs)]

	@property
	def method(self):
		return self.requested[-1]['params']['method']


def _dict_row(sort_by='rank', sort_how='asc'):
	return {'sort_by': sort_by, 'sort_how': sort_how, 'data': [dict(i) for i in ROWS]}


class CacheShapeTests(unittest.TestCase):
	# Several modules in this suite install their own 'caches'/'modules' stubs at import time, and
	# sort_source() re-reads sys.modules on every call, so collection order would otherwise decide
	# whose override store these tests see. Same idiom as tests/test_mixed_list_sort.py.
	def setUp(self):
		self._original_sys_modules = dict((k, sys.modules[k]) for k in _STUBBED_MODULES if k in sys.modules)
		_install_stubs()
		OVERRIDES.clear()
		SETTINGS.clear()

	def tearDown(self):
		for key in _STUBBED_MODULES:
			if key in self._original_sys_modules: sys.modules[key] = self._original_sys_modules[key]
			else: sys.modules.pop(key, None)
		OVERRIDES.clear()
		SETTINGS.clear()

	def test_every_caller_requests_the_same_method(self):
		"""The builder, the artwork maker and the random builders all share one cache key, so they
		must all write the same shape into it."""
		harness = _Harness(_dict_row())
		harness.call('my_lists', 'jo', 'faves', True, '42')                # the list builder
		builder_method = harness.method
		harness.call('my_lists', 'jo', 'faves', True, '42')                # trakt_image_maker
		self.assertEqual(builder_method, harness.method)
		harness.call('my_lists', 'jo', 'faves', True, '42', skip_sort=True)  # the random builders
		self.assertEqual(builder_method, harness.method)
		self.assertEqual('sort_by_headers', builder_method)

	def test_all_three_callers_share_one_cache_key(self):
		# If they ever stop sharing it, the shape agreement above stops mattering - and so does this
		# whole file. Pinned so that change is a deliberate one.
		harness = _Harness(_dict_row())
		harness.call('my_lists', 'jo', 'faves', True, '42')
		harness.call('my_lists', 'jo', 'faves', True, '42', skip_sort=True)
		self.assertEqual(1, len(set(i['string'] for i in harness.requested)))

	def test_a_dict_row_is_unwrapped_for_the_skip_caller_too(self):
		"""'skip' used to bypass the unwrapping entirely: the dict reached enumerate(), i['type'] ran
		against the string 'sort_by', and every row was swallowed by the try - an empty list."""
		harness = _Harness(_dict_row())
		self.assertEqual(PAYLOAD_ORDER, harness.titles('my_lists', 'jo', 'faves', True, '42', skip_sort=True))

	def test_the_skip_flag_does_not_change_the_requested_method(self):
		"""The one remaining caller-visible knob must not be able to change the cache row's shape."""
		harness = _Harness(_dict_row())
		harness.call('my_lists', 'jo', 'faves', True, '42', skip_sort=False)
		unsorted_method = harness.method
		harness.call('my_lists', 'jo', 'faves', True, '42', skip_sort=True)
		self.assertEqual(unsorted_method, harness.method)
		self.assertEqual('sort_by_headers', unsorted_method)

	def test_the_skip_flag_only_skips_the_sort(self):
		"""skip_sort must still hand back every row, in payload order - not an empty list, and not a
		re-ordered one."""
		harness = _Harness(_dict_row('rank', 'asc'))
		self.assertEqual(PAYLOAD_ORDER, harness.titles('my_lists', 'jo', 'faves', True, '42', skip_sort=True))
		self.assertEqual(RANK_ASC, harness.titles('my_lists', 'jo', 'faves', True, '42'))

	def test_a_legacy_bare_list_row_still_reads(self):
		"""Rows written by the previous build are plain lists. They must survive the upgrade."""
		harness = _Harness([dict(i) for i in ROWS])
		self.assertEqual(PAYLOAD_ORDER, harness.titles('my_lists', 'jo', 'faves', True, '42'))

	def test_the_payload_headers_drive_the_fallback_ordering(self):
		"""Nothing carries sort_by/sort_how in from a URL any more, so the headers in the cached row
		are the only remaining record of the order Trakt declares for the list."""
		harness = _Harness(_dict_row('rank', 'asc'))
		self.assertEqual(RANK_ASC, harness.titles('my_lists', 'jo', 'faves', True, '42'))

	def test_the_builder_and_the_artwork_maker_produce_the_same_order(self):
		"""trakt_image_maker builds the poster from the first four items the user sees on screen."""
		harness = _Harness(_dict_row('rank', 'asc'))
		builder = harness.titles('my_lists', 'jo', 'faves', True, '42')
		artwork = harness.titles('my_lists', 'jo', 'faves', True, '42')
		self.assertEqual(builder, artwork)
		self.assertEqual(RANK_ASC, artwork)

	def test_one_malformed_row_does_not_abort_the_whole_list(self):
		"""The retitling loop used to dereference i['show'] unguarded, outside any try. A season row
		with no 'show' - which the extraction loop below it already tolerates by dropping the row -
		raised KeyError there instead and took every other item on the list down with it."""
		rows = [dict(ROWS[0]),
			{'type': 'season', 'rank': 4, 'season': {'number': 1, 'title': 'Season 1'}},  # no 'show'
			dict(ROWS[1]), dict(ROWS[2])]
		harness = _Harness({'sort_by': 'rank', 'sort_how': 'asc', 'data': rows})
		self.assertEqual(RANK_ASC, harness.titles('my_lists', 'jo', 'faves', True, '42'))

	def test_a_malformed_row_does_not_abort_the_skip_sort_path_either(self):
		rows = [dict(ROWS[0]),
			{'type': 'episode', 'rank': 4, 'episode': {'season': 1, 'number': 1, 'title': 'Pilot'}},  # no 'show'
			dict(ROWS[1]), dict(ROWS[2])]
		harness = _Harness({'sort_by': 'rank', 'sort_how': 'asc', 'data': rows})
		self.assertEqual(PAYLOAD_ORDER, harness.titles('my_lists', 'jo', 'faves', True, '42', skip_sort=True))

	def test_a_well_formed_season_row_is_still_retitled(self):
		"""Wrapping the loop in a try must not stop it doing its job."""
		row = {'type': 'season', 'rank': 4, 'show': {'ids': {'tmdb': 9}, 'title': 'Delta'},
			'season': {'number': 1, 'title': 'Season 1'}}
		harness = _Harness({'sort_by': 'rank', 'sort_how': 'asc', 'data': [row]})
		harness.call('my_lists', 'jo', 'faves', True, '42')
		self.assertEqual('Delta - Season 1', row['season']['title'])

	def test_headerless_rows_fall_back_to_the_provider_order(self):
		harness = _Harness({'sort_by': None, 'sort_how': None, 'data': [dict(i) for i in ROWS]})
		self.assertEqual(PAYLOAD_ORDER, harness.titles('my_lists', 'jo', 'faves', True, '42'))


def _positional_arg_count(node, called_name):
	counts = []
	for child in ast.walk(node):
		if not isinstance(child, ast.Call): continue
		func = child.func
		name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
		if name != called_name: continue
		counts.append((len(child.args), sorted(k.arg for k in child.keywords if k.arg)))
	return counts


RANDOM_LISTS = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'indexers' / 'random_lists.py'


def _sort_argument_offences(source, label):
	"""(offences, call_count) for every get_trakt_list_contents call in `source`.

	Only the sort arguments are forbidden. Other keywords - list_id in particular - are ordinary
	parameters and say nothing about how the contents get sorted, so they are allowed through. A
	sixth positional argument is a different matter: it lands past skip_sort, which means the
	signature grew a slot, and historically that slot was sort_by.
	"""
	offences, seen = [], 0
	for node in ast.walk(ast.parse(source)):
		if not isinstance(node, ast.Call): continue
		func = node.func
		name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
		if name != 'get_trakt_list_contents': continue
		seen += 1
		unparsed = '%s: %s' % (label, ast.unparse(node))
		if len(node.args) > 5: offences.append('sixth positional argument - %s' % unparsed)
		for kw in node.keywords:
			if kw.arg in ('sort_by', 'sort_how'): offences.append('%s= keyword - %s' % (kw.arg, unparsed))
	return offences, seen


class SignatureTests(unittest.TestCase):
	"""The divergence has to be unexpressible, not merely unexercised.

	While the signature carried sort_by/sort_how, `method` could be made to depend on them again -
	the exact cache-shape divergence this file exists to prevent - and no caller passed anything that
	would reveal it. Collapsing them into one boolean removes the vocabulary for saying it.
	"""

	def test_the_signature_offers_no_sort_at_all(self):
		node = _function(TRAKT_API, 'get_trakt_list_contents')
		names = [a.arg for a in node.args.args]
		self.assertEqual(['list_type', 'user', 'slug', 'with_auth', 'list_id', 'skip_sort'], names)
		self.assertEqual([], list(node.args.kwonlyargs))
		self.assertIsNone(node.args.vararg)
		self.assertIsNone(node.args.kwarg)

	def test_no_sort_name_is_bound_before_the_method_is_chosen(self):
		"""`method = ... if sort_by ... else ...` must not even be a writable line: at the point the
		method is chosen, no sort name exists in the function's scope yet."""
		node = _function(TRAKT_API, 'get_trakt_list_contents')
		method_line = min(t.lineno for s in ast.walk(node) if isinstance(s, ast.Assign)
			for t in s.targets if isinstance(t, ast.Name) and t.id == 'method')
		bound_earlier = [t.id for s in ast.walk(node) if isinstance(s, ast.Assign) and s.lineno <= method_line
			for t in ast.walk(s) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)]
		self.assertNotIn('sort_by', bound_earlier)
		self.assertNotIn('sort_how', bound_earlier)
		for arg in node.args.args:
			self.assertNotIn(arg.arg, ('sort_by', 'sort_how', 'sort', 'method', 'mode'))
		# ...and the flag that does exist is a boolean, so it cannot carry a field name either.
		self.assertIs(False, node.args.defaults[-1].value)

	def test_the_only_flag_a_caller_can_pass_is_the_skip_flag(self):
		"""random_lists is the only module that passes a sixth argument, and it passes the boolean."""
		for node in ast.walk(_tree(RANDOM_LISTS)):
			if not isinstance(node, ast.Call): continue
			func = node.func
			name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
			if name != 'get_trakt_list_contents': continue
			self.assertEqual(5, len(node.args), ast.unparse(node))
			self.assertEqual(['skip_sort'], [k.arg for k in node.keywords], ast.unparse(node))
			self.assertIs(True, node.keywords[0].value.value, ast.unparse(node))

	def test_no_module_passes_a_sort_string_to_the_contents_reader(self):
		lib = ROOT / 'plugin.video.redlight' / 'resources' / 'lib'
		offences, seen = [], 0
		for path in lib.rglob('*.py'):
			with open(str(path), 'r', encoding='utf-8') as f:
				source = f.read()
			if 'get_trakt_list_contents' not in source: continue
			found, count = _sort_argument_offences(source, path.name)
			offences += found
			seen += count
		self.assertEqual([], offences)
		self.assertTrue(seen >= 6, 'expected to find every call site, found %d' % seen)

	def test_the_scan_catches_a_sort_argument_but_not_an_ordinary_keyword(self):
		"""The scan above only proves something while it is capable of failing. Requiring every
		keyword to be skip_sort was too strict - it would reject a plain list_id= - so pin what it
		does and does not object to."""
		allowed = ("get_trakt_list_contents('my_lists', u, s, True, lid)",
			"get_trakt_list_contents('my_lists', u, s, True, lid, skip_sort=True)",
			"get_trakt_list_contents('my_lists', u, s, True, list_id=lid)",
			"get_trakt_list_contents('my_lists', u, s, True, list_id=lid, skip_sort=True)")
		for source in allowed:
			self.assertEqual(([], 1), _sort_argument_offences(source, 'x.py'), source)
		forbidden = ("get_trakt_list_contents('my_lists', u, s, True, lid, 'rank')",
			"get_trakt_list_contents('my_lists', u, s, True, lid, sort_by='rank')",
			"get_trakt_list_contents('my_lists', u, s, True, lid, sort_how='asc')",
			"get_trakt_list_contents('my_lists', u, s, True, list_id=lid, sort_by='rank')")
		for source in forbidden:
			found, seen = _sort_argument_offences(source, 'x.py')
			self.assertEqual(1, seen, source)
			self.assertEqual(1, len(found), source)


class CallSiteTests(unittest.TestCase):
	def test_no_caller_asks_for_a_client_side_sort(self):
		"""Five positional arguments: list_type, user, slug, with_auth, list_id. A sixth is a sort_by,
		which is exactly what split the two callers apart."""
		for name in ('build_trakt_list', 'trakt_image_maker'):
			calls = _positional_arg_count(_function(TRAKT_LISTS, name), 'get_trakt_list_contents')
			self.assertEqual([(5, [])], calls, name)

	def test_the_legacy_per_list_sort_store_is_no_longer_read(self):
		with open(str(TRAKT_LISTS), 'r', encoding='utf-8') as f:
			source = f.read()
		self.assertNotIn('get_all_lists_custom_sort', source)
		self.assertNotIn('all_custom_sorts', source)

	def test_no_trakt_list_folder_url_carries_a_sort(self):
		"""A sort_by in the folder URL is what build_trakt_list read back out and passed on. Every
		url_params dict that names a build_trakt_list-ish mode must be free of both keys."""
		checked = 0
		for node in ast.walk(_tree(TRAKT_LISTS)):
			if not isinstance(node, ast.Dict): continue
			keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
			if 'mode' not in keys: continue
			source = ast.unparse(node)
			if 'build_trakt_list' not in source: continue
			checked += 1
			self.assertNotIn("'sort_by'", source, source)
			self.assertNotIn("'sort_how'", source, source)
		self.assertTrue(checked >= 4, 'expected to find the list folder URL dicts, found %d' % checked)

	def test_the_set_custom_sort_menu_still_carries_trakts_declared_order(self):
		"""It is not a folder URL: those two values are the fallback the "Use Default" choice resolves
		to, and they now come from the Trakt list metadata rather than from the legacy store."""
		checked = 0
		for node in ast.walk(_tree(TRAKT_LISTS)):
			if not isinstance(node, ast.Dict): continue
			if 'set_list_custom_sort' not in ast.unparse(node): continue
			keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
			checked += 1
			self.assertIn('sort_by', keys)
			self.assertIn('sort_how', keys)
		self.assertEqual(2, checked, 'expected both Set Custom Sort context menu entries')


if __name__ == '__main__':
	unittest.main()
