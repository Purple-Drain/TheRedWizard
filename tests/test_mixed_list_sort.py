import ast
import sys
import types
import unittest

from test_trakt_sync_list_sort import ROOT, list_sort, _install_stubs, OVERRIDES, SETTINGS


_STUBBED_MODULES = ('caches', 'caches.list_sort_cache', 'caches.settings_cache', 'modules', 'modules.settings')

TRAKT_API = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'apis' / 'trakt_api.py'
TMDB_LISTS = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'indexers' / 'tmdb_lists.py'
PERSONAL_LISTS = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'indexers' / 'personal_lists.py'

# Every fixture below is laid out so that the input order is not equal to any of the orders the tests
# assert. A two-row fixture cannot do that - one of the two possible orders always coincides with the
# input - so an implementation that ignored the spec and returned the rows untouched would pass.
# Ranks, ratings and date_added are likewise out of title order, so title:asc and the spec under test
# never produce the same sequence.
TRAKT_LIST_ROWS = [
	{'type': 'movie', 'rank': 2, 'listed_at': '2024-01-01', 'movie': {'title': 'Banana', 'released': '2001-01-01', 'rating': 5.0, 'votes': 10, 'runtime': 100}},
	{'type': 'show', 'rank': 3, 'listed_at': '2024-02-01', 'show': {'title': 'Alpha', 'first_aired': '1999-01-01', 'rating': 9.0, 'votes': 20, 'runtime': 40}},
	{'type': 'movie', 'rank': 1, 'listed_at': '2024-03-01', 'movie': {'title': 'Cherry', 'released': '2010-01-01', 'rating': 7.0, 'votes': 30, 'runtime': 120}},
]

TRAKT_PAYLOAD_ORDER = ['Banana', 'Alpha', 'Cherry']
TRAKT_TITLE_ASC = ['Alpha', 'Banana', 'Cherry']
TRAKT_RANK_ASC = ['Cherry', 'Banana', 'Alpha']

# Damson carries a date_added shorter than the others on purpose: '90' sorts last numerically but
# first descending as a string, so an extractor that dropped the int() coercion is caught here.
PERSONAL_ROWS = [
	{'title': 'Banana', 'date_added': '200', 'release_date': '2001-01-01'},
	{'title': 'Alpha', 'date_added': '100', 'release_date': '1999-01-01'},
	{'title': 'Cherry', 'date_added': '150', 'release_date': '2010-01-01'},
	{'title': 'Damson', 'date_added': '90', 'release_date': '2005-01-01'},
]

# Damson has no release date, so release_date sorts pin the MISSING_DATE sentinel: without it the
# comparison of None against a string raises and apply() hands back the payload order unsorted.
#
# The rows are deliberately NOT in original_order. tmdblist_api fetches pages 2..N through a thread
# pool and extends the result list as each thread finishes, so the payload a TMDb list arrives in is
# page-interleaved; original_order is the only record of what TMDb actually served. A fixture already
# in provider order cannot tell "restored the provider order" apart from "handed the payload back
# untouched", which is exactly how the engine shipped without a TMDb 'default' extractor.
TMDB_ROWS = [
	{'title': 'Cherry', 'release_date': '2010-01-01', 'original_order': 1},
	{'title': 'Damson', 'release_date': None, 'original_order': 3},
	{'title': 'Banana', 'release_date': '2001-01-01', 'original_order': 0},
	{'title': 'Alpha', 'release_date': '1999-01-01', 'original_order': 2},
]

TMDB_PAYLOAD_ORDER = ['Cherry', 'Damson', 'Banana', 'Alpha']

TMDB_PROVIDER_ORDER = ['Banana', 'Cherry', 'Alpha', 'Damson']
TMDB_TITLE_ASC = ['Alpha', 'Banana', 'Cherry', 'Damson']
TMDB_RELEASE_DESC = ['Damson', 'Cherry', 'Banana', 'Alpha']


class _StubbedTestCase(unittest.TestCase):
	def setUp(self):
		# Other test modules install their own fake 'caches'/'modules' stubs into sys.modules at
		# import time, and resolve()/sort_source() re-read sys.modules lazily on every call, so
		# collection order could otherwise decide which stubs these tests see. Same idiom as
		# tests/test_list_sort_resolve.py and tests/test_trakt_sync_list_sort.py.
		self._original_sys_modules = {}
		for key in _STUBBED_MODULES:
			if key in sys.modules:
				self._original_sys_modules[key] = sys.modules[key]
		_install_stubs()
		OVERRIDES.clear()
		SETTINGS.clear()

	def tearDown(self):
		for key in _STUBBED_MODULES:
			if key in self._original_sys_modules:
				sys.modules[key] = self._original_sys_modules[key]
			else:
				sys.modules.pop(key, None)
		OVERRIDES.clear()
		SETTINGS.clear()


class MixedListResolutionTests(_StubbedTestCase):
	def _titles(self, rows):
		return [i[i['type']]['title'] for i in rows]

	def test_no_override_uses_default_spec(self):
		# rating:asc would order Banana, Cherry, Alpha. A mixed list must ignore the mediatype
		# default and land on DEFAULT_SPEC (title:asc) instead.
		SETTINGS['redlight.sort.default.movies'] = 'rating:asc'
		SETTINGS['redlight.sort.default.shows'] = 'rating:asc'
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list')
		self.assertEqual(TRAKT_TITLE_ASC, self._titles(result))

	def test_override_drives_trakt_list(self):
		OVERRIDES['trakt.list:99'] = 'rank:desc'
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list')
		self.assertEqual([3, 2, 1], [i['rank'] for i in result])

	def test_override_is_scoped_to_its_own_list(self):
		OVERRIDES['trakt.list:99'] = 'rank:desc'
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:100', None, 'trakt_list')
		self.assertEqual(TRAKT_TITLE_ASC, self._titles(result))

	def test_trakt_list_without_override_keeps_the_payload_sort(self):
		# The Trakt API declares the list's own ordering, and the folder URL carries it. A user who
		# never opened "Set Custom Sort" has no override row and nothing to migrate, so the payload
		# sort - not DEFAULT_SPEC - is the ordering that must survive the upgrade.
		fallback = list_sort.trakt_list_fallback('rank', 'asc')
		self.assertEqual('rank:asc', fallback)
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list', fallback=fallback)
		self.assertEqual([1, 2, 3], [i['rank'] for i in result])
		self.assertEqual(TRAKT_RANK_ASC, self._titles(result))

	def test_trakt_payload_sort_direction_is_honoured(self):
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list',
			fallback=list_sort.trakt_list_fallback('rank', 'desc'))
		self.assertEqual([3, 2, 1], [i['rank'] for i in result])

	def test_stored_override_beats_the_payload_sort(self):
		OVERRIDES['trakt.list:99'] = 'title:asc'
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list',
			fallback=list_sort.trakt_list_fallback('rank', 'asc'))
		self.assertEqual(TRAKT_TITLE_ASC, self._titles(result))

	def test_unmappable_payload_sort_leaves_the_order_untouched(self):
		# 'my_rating', 'watched' and 'collected' have no canonical field. The old code left such a
		# payload alone rather than retitling it, so they must resolve to the provider order.
		for sort_by in ('my_rating', 'watched', 'collected'):
			fallback = list_sort.trakt_list_fallback(sort_by, 'desc')
			self.assertEqual('default:asc', fallback, sort_by)
			result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list', fallback=fallback)
			self.assertEqual(TRAKT_PAYLOAD_ORDER, self._titles(result), sort_by)

	def test_personal_list_override(self):
		OVERRIDES['personal:Faves|jo'] = 'date_added:desc'
		result = list_sort.sort_source(list(PERSONAL_ROWS), 'personal:Faves|jo', None, 'personal')
		self.assertEqual(['Banana', 'Cherry', 'Alpha', 'Damson'], [i['title'] for i in result])

	def test_personal_list_without_override_sorts_by_title(self):
		result = list_sort.sort_source(list(PERSONAL_ROWS), 'personal:Faves|jo', None, 'personal')
		self.assertEqual(['Alpha', 'Banana', 'Cherry', 'Damson'], [i['title'] for i in result])

	def test_the_tmdb_fixture_is_not_already_in_provider_order(self):
		# Guards the guards below: re-sort TMDB_ROWS into original_order and every provider-order
		# assertion in this file stops distinguishing "restored" from "returned unchanged".
		self.assertEqual(TMDB_PAYLOAD_ORDER, [i['title'] for i in TMDB_ROWS])
		self.assertNotEqual(TMDB_PROVIDER_ORDER, TMDB_PAYLOAD_ORDER)
		self.assertEqual(TMDB_PROVIDER_ORDER, [i['title'] for i in sorted(TMDB_ROWS, key=lambda i: i['original_order'])])

	def test_tmdb_default_field_keeps_provider_order(self):
		# 'default' for TMDb is a restore, not a no-op: the payload is page-interleaved by the
		# thread pool that fetched it, and original_order is what TMDb served.
		OVERRIDES['tmdb:watchlist'] = 'default:asc'
		result = list_sort.sort_source(list(TMDB_ROWS), 'tmdb:watchlist', None, 'tmdb')
		self.assertEqual(TMDB_PROVIDER_ORDER, [i['title'] for i in result])

	def test_tmdb_rows_with_no_original_order_sort_last_without_raising(self):
		# The old code's key was (k['original_order'] is None, k['original_order']); a row that never
		# got stamped must not raise int-vs-None and drop the whole list back to payload order.
		rows = [{'title': 'Nostamp', 'release_date': None}] + list(TMDB_ROWS)
		result = list_sort.sort_source(rows, 'tmdb:watchlist', None, 'tmdb', fallback='default:asc')
		self.assertEqual(TMDB_PROVIDER_ORDER + ['Nostamp'], [i['title'] for i in result])

	def test_tmdb_release_date_override(self):
		OVERRIDES['tmdb:8675309'] = 'release_date:desc'
		result = list_sort.sort_source(list(TMDB_ROWS), 'tmdb:8675309', None, 'tmdb')
		self.assertEqual(TMDB_RELEASE_DESC, [i['title'] for i in result])

	def test_tmdb_list_without_override_keeps_provider_order(self):
		# A TMDb user list nobody ever sorted has no store row and no override row. Its ordering is
		# TMDb's own, and DEFAULT_SPEC would silently retitle it on upgrade.
		result = list_sort.sort_source(list(TMDB_ROWS), 'tmdb:8675309', None, 'tmdb', fallback='default:asc')
		self.assertEqual(TMDB_PROVIDER_ORDER, [i['title'] for i in result])

	def test_tmdb_stored_override_beats_the_provider_order_fallback(self):
		OVERRIDES['tmdb:8675309'] = 'title:asc'
		result = list_sort.sort_source(list(TMDB_ROWS), 'tmdb:8675309', None, 'tmdb', fallback='default:asc')
		self.assertEqual(TMDB_TITLE_ASC, [i['title'] for i in result])

	def test_fallback_survives_a_failing_override_lookup(self):
		# sort_source() catches resolve() raising - an unreadable override store, a broken import -
		# and must land on the caller's fallback, not on DEFAULT_SPEC. Falling back to title:asc
		# there is precisely the pre-fix behaviour, applied to every mixed list at once.
		def _raise(scope): raise RuntimeError('override store unavailable')
		sys.modules['caches.list_sort_cache'].get_override = _raise

		result = list_sort.sort_source(list(TMDB_ROWS), 'tmdb:8675309', None, 'tmdb', fallback='default:asc')
		self.assertEqual(TMDB_PROVIDER_ORDER, [i['title'] for i in result])

		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list',
			fallback=list_sort.trakt_list_fallback('rank', 'asc'))
		self.assertEqual([1, 2, 3], [i['rank'] for i in result])


class LegacyStoreMigrationTests(unittest.TestCase):
	def test_trakt_rows_translate(self):
		result = list_sort.migrate_legacy_stores(
			trakt_rows={'12345': {'sort_by': 'added', 'sort_how': 'desc'}}, personal_rows={}, tmdb_rows={})
		self.assertEqual('date_added:desc', result['trakt.list:12345'])

	def test_trakt_sort_how_is_honoured(self):
		result = list_sort.migrate_legacy_stores(
			trakt_rows={'12345': {'sort_by': 'added', 'sort_how': 'asc'}}, personal_rows={}, tmdb_rows={})
		self.assertEqual('date_added:asc', result['trakt.list:12345'])

	def test_personal_rows_translate(self):
		result = list_sort.migrate_legacy_stores(
			trakt_rows={}, personal_rows={('Faves', 'jo'): '2'}, tmdb_rows={})
		self.assertEqual('date_added:desc', result['personal:Faves|jo'])

	def test_personal_code_one_is_ascending(self):
		result = list_sort.migrate_legacy_stores(
			trakt_rows={}, personal_rows={('Faves', 'jo'): '1'}, tmdb_rows={})
		self.assertEqual('date_added:asc', result['personal:Faves|jo'])

	def test_tmdb_rows_translate(self):
		result = list_sort.migrate_legacy_stores(trakt_rows={}, personal_rows={}, tmdb_rows={8675309: '2'})
		self.assertEqual('release_date:desc', result['tmdb:8675309'])

	def test_tmdb_code_one_is_ascending(self):
		result = list_sort.migrate_legacy_stores(trakt_rows={}, personal_rows={}, tmdb_rows={8675309: '1'})
		self.assertEqual('release_date:asc', result['tmdb:8675309'])

	def test_tmdb_non_string_sort_order_still_translates(self):
		# get_sort_orders() hands back whatever the row holds; an int must not fall through the table.
		result = list_sort.migrate_legacy_stores(trakt_rows={}, personal_rows={}, tmdb_rows={8675309: 2})
		self.assertEqual('release_date:desc', result['tmdb:8675309'])

	def test_tmdb_explicit_provider_default_choice_is_preserved(self):
		# sort_order_tmdb_list() stores the literal string 'None' for "Default From TMDb (None)".
		# Dropping the row would turn that explicit choice into title:asc, unrecoverably.
		result = list_sort.migrate_legacy_stores(trakt_rows={}, personal_rows={}, tmdb_rows={8675309: 'None'})
		self.assertEqual('default:asc', result['tmdb:8675309'])

	def test_tmdb_null_and_blank_sort_orders_keep_provider_order(self):
		result = list_sort.migrate_legacy_stores(trakt_rows={}, personal_rows={}, tmdb_rows={1: None, 2: ''})
		self.assertEqual({'tmdb:1': 'default:asc', 'tmdb:2': 'default:asc'}, result)

	def test_unmappable_rows_are_skipped(self):
		result = list_sort.migrate_legacy_stores(
			trakt_rows={'1': {'sort_by': 'nonsense', 'sort_how': 'asc'}}, personal_rows={('X', 'y'): '99'}, tmdb_rows={1: '99'})
		self.assertEqual({}, result)

	def test_null_personal_sort_order_becomes_provider_default(self):
		result = list_sort.migrate_legacy_stores(trakt_rows={}, personal_rows={('Faves', 'jo'): None}, tmdb_rows={})
		self.assertEqual('default:asc', result['personal:Faves|jo'])

	def test_all_three_stores_migrate_together(self):
		result = list_sort.migrate_legacy_stores(
			trakt_rows={'12345': {'sort_by': 'title', 'sort_how': 'asc'}},
			personal_rows={('Faves', 'jo'): '5'}, tmdb_rows={8675309: '4'})
		self.assertEqual({'trakt.list:12345': 'title:asc', 'personal:Faves|jo': 'random:asc',
			'tmdb:8675309': 'default:asc'}, result)

	def test_empty_stores_translate_to_nothing(self):
		self.assertEqual({}, list_sort.migrate_legacy_stores(None, None, None))

	def test_settings_migration_cannot_reach_the_new_blank_codes(self):
		# LEGACY_TMDB_CODES now answers 'None' and '', which _legacy_code() must never look up:
		# it coerces a missing or blank setting to the '4' fallback first. Pinned because the two
		# tables share LEGACY_TMDB_CODES and only the store path may see those keys.
		for stored in (None, '', 'None'):
			overrides = list_sort.migrate_legacy_sort_settings({'tmdbsort.watchlist': stored})['overrides']
			self.assertEqual('default:asc', overrides['tmdb:watchlist'])
		self.assertEqual('4', list_sort._legacy_code({'tmdbsort.watchlist': None}, 'tmdbsort.watchlist'))
		self.assertEqual('4', list_sort._legacy_code({'tmdbsort.watchlist': ''}, 'tmdbsort.watchlist'))
		self.assertEqual('4', list_sort._legacy_code({}, 'tmdbsort.watchlist'))


def _sort_source_calls(path, function=None):
	"""Every list_sort.sort_source(...) call in a module, as dicts of unparsed argument source.

	Parsed from source with ast rather than imported, because both modules pull in the Kodi runtime.
	Mirrors tests/test_simkl_mdblist_sort.py. Pass function to restrict the search to one def, which
	is how a call site is identified without reading the very arguments under test.
	"""
	with open(str(path), 'r', encoding='utf-8') as f:
		tree = ast.parse(f.read())
	scope = tree
	if function is not None:
		found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function]
		if len(found) != 1: raise AssertionError('expected exactly one def %s in %s' % (function, path.name))
		scope = found[0]
	calls = []
	for node in ast.walk(scope):
		if not isinstance(node, ast.Call): continue
		func = node.func
		if not isinstance(func, ast.Attribute) or func.attr != 'sort_source': continue
		if not isinstance(func.value, ast.Name) or func.value.id != 'list_sort': continue
		keywords = dict((k.arg, ast.unparse(k.value)) for k in node.keywords if k.arg)
		args = [ast.unparse(a) for a in node.args]
		calls.append({'tree': tree, 'args': args, 'list_key': args[1], 'adapter': args[3] if len(args) > 3 else None,
			'fallback': keywords.get('fallback')})
	return calls


def _assigned_source(tree, name):
	"""The source of the expression a module-level-visible name was last assigned, else name itself."""
	found = name
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign) and any(ast.unparse(t) == name for t in node.targets):
			found = ast.unparse(node.value)
	return found


class CallSiteFallbackTests(_StubbedTestCase):
	"""Pin that the two mixed-list call sites actually pass the provider-order fallback.

	The engine-level tests above prove the fallback works; nothing else would notice a call site
	that stopped passing one, and the result is every un-customised Trakt user list and TMDb list
	silently reordering to title:asc on upgrade, with no legacy row left to recover from.
	"""

	def _single_call(self, path, adapter):
		calls = [c for c in _sort_source_calls(path) if c['adapter'] == adapter]
		self.assertEqual(1, len(calls), 'expected exactly one %s sort_source call in %s' % (adapter, path.name))
		return calls[0]

	def test_trakt_user_list_call_site_passes_the_payload_sort_as_fallback(self):
		call = self._single_call(TRAKT_API, "'trakt_list'")
		self.assertEqual("'trakt.list:%s' % list_id", call['list_key'])
		self.assertIsNotNone(call['fallback'], 'the Trakt user list call site must pass a fallback')
		self.assertEqual('list_sort.trakt_list_fallback(sort_by, sort_how)',
			_assigned_source(call['tree'], call['fallback']))

	def test_every_tmdb_list_leaves_get_tmdb_list_through_the_engine(self):
		# 'recommendations' used to return early. That was equivalent to falling through - with the
		# provider-order fallback and no override, sort_source hands the payload straight back - but
		# it also made tmdb:recommendations the one list that could never honour an override.
		with open(str(TMDB_LISTS), 'r', encoding='utf-8') as f:
			tree = ast.parse(f.read())
		found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'get_tmdb_list']
		self.assertEqual(1, len(found))
		returns = [ast.unparse(n.value) for n in ast.walk(found[0]) if isinstance(n, ast.Return)]
		self.assertTrue(returns, 'get_tmdb_list must return something')
		for expression in returns:
			self.assertTrue(expression.startswith('list_sort.sort_source('),
				'every get_tmdb_list exit must go through sort_source, not %s' % expression)

	def test_tmdb_call_site_fallback_literal_preserves_provider_order(self):
		call = self._single_call(TMDB_LISTS, "'tmdb'")
		self.assertEqual("'tmdb:%s' % list_id", call['list_key'])
		self.assertIsNotNone(call['fallback'], 'the TMDb list call site must pass a fallback')
		result = list_sort.sort_source(list(TMDB_ROWS), 'tmdb:8675309', None, 'tmdb',
			fallback=ast.literal_eval(call['fallback']))
		self.assertEqual(TMDB_PROVIDER_ORDER, [i['title'] for i in result])

	def test_trakt_call_site_fallback_expression_preserves_the_payload_sort(self):
		call = self._single_call(TRAKT_API, "'trakt_list'")
		self.assertIsNotNone(call['fallback'], 'the Trakt user list call site must pass a fallback')
		source = _assigned_source(call['tree'], call['fallback'])
		fallback = eval(source, {'list_sort': list_sort, 'sort_by': 'rank', 'sort_how': 'asc'})
		result = list_sort.sort_source(list(TRAKT_LIST_ROWS), 'trakt.list:99', None, 'trakt_list', fallback=fallback)
		self.assertEqual([1, 2, 3], [i['rank'] for i in result])


def _conditional_sort_source(path, adapter):
	"""The source of the conditional expression whose true-branch is that adapter's sort_source call."""
	with open(str(path), 'r', encoding='utf-8') as f:
		tree = ast.parse(f.read())
	for node in ast.walk(tree):
		if not isinstance(node, ast.IfExp): continue
		for inner in ast.walk(node.body):
			if not isinstance(inner, ast.Call): continue
			func = inner.func
			if not isinstance(func, ast.Attribute) or func.attr != 'sort_source': continue
			if len(inner.args) > 3 and ast.unparse(inner.args[3]) == adapter: return ast.unparse(node)
	return None


class TraktCallSiteBranchTests(_StubbedTestCase):
	"""Pin which of get_trakt_list_contents' two branches each kind of list takes.

	The fallback tests above read the sort_source call's arguments but never the condition choosing
	between it and the ad-hoc apply(). Invert that condition and every stored user list silently
	ignores its own override while every ad-hoc list gains a scope that can never have one.
	"""

	def _evaluate(self, list_id):
		source = _conditional_sort_source(TRAKT_API, "'trakt_list'")
		self.assertIsNotNone(source, 'the Trakt user list sort must stay a two-branch conditional')
		calls = []

		def _record(name, value):
			def _call(*args, **kwargs):
				calls.append((name, args, kwargs))
				return value
			return _call

		fake = types.SimpleNamespace(sort_source=_record('sort_source', 'stored'),
			apply=_record('apply', 'ad_hoc'), parse_spec=lambda raw, **kwargs: raw, TRAKT_LIST='TRAKT_LIST')
		namespace = {'list_sort': fake, 'data': ['row'], 'list_id': list_id, 'payload_spec': 'rank:asc',
			'settings': types.SimpleNamespace(ignore_articles=lambda: False)}
		result = eval(source, namespace)
		self.assertEqual(1, len(calls))
		return result, calls[0]

	def test_stored_user_list_is_sorted_through_its_override_scope(self):
		result, call = self._evaluate('8675309')
		self.assertEqual('stored', result, 'a list with an id must go through sort_source, not apply')
		self.assertEqual('sort_source', call[0])
		self.assertEqual('trakt.list:8675309', call[1][1])
		self.assertEqual('rank:asc', call[2].get('fallback'))

	def test_ad_hoc_list_without_an_id_is_sorted_by_its_payload_alone(self):
		# No list_id means no scope an override could ever be stored under, so routing it through
		# sort_source would resolve 'trakt.list:None' and drop the payload sort for every such list.
		result, call = self._evaluate(None)
		self.assertEqual('ad_hoc', result, 'a list with no id must go through apply, not sort_source')
		self.assertEqual('apply', call[0])
		self.assertEqual('rank:asc', call[1][1])


class PersonalCallSiteTests(_StubbedTestCase):
	"""Pin the personal call site's scope key and adapter.

	Everything preserving a personal list's stored ordering rests on the key this call site builds
	being byte-identical to the one migrate_legacy_stores() wrote the override under. Nothing else
	compares the two, and a mismatch is invisible: the list simply comes back title-sorted.
	"""

	def _call(self):
		calls = _sort_source_calls(PERSONAL_LISTS, function='get_personal_list')
		self.assertEqual(1, len(calls), 'expected exactly one sort_source call in get_personal_list')
		return calls[0]

	def test_call_site_scope_is_the_key_the_migration_writes(self):
		scope = eval(self._call()['list_key'], {'list_name': 'Faves', 'author': 'jo'})
		migrated = list_sort.migrate_legacy_stores({}, {('Faves', 'jo'): '2'}, {})
		self.assertEqual(['personal:Faves|jo'], list(migrated))
		self.assertEqual('personal:Faves|jo', scope)

		# And behaviourally: the migrated override must actually drive this call site's lookup.
		OVERRIDES.update(migrated)
		result = list_sort.sort_source(list(PERSONAL_ROWS), scope, None,
			ast.literal_eval(self._call()['adapter']))
		self.assertEqual(['Banana', 'Cherry', 'Alpha', 'Damson'], [i['title'] for i in result])

	def test_call_site_uses_the_personal_adapter(self):
		# The tmdb adapter has no date_added extractor, so swapping it silently degrades every
		# date-added-sorted personal list to the payload order.
		adapter = ast.literal_eval(self._call()['adapter'])
		self.assertEqual('personal', adapter)
		OVERRIDES['personal:Faves|jo'] = 'date_added:asc'
		result = list_sort.sort_source(list(PERSONAL_ROWS), 'personal:Faves|jo', None, adapter)
		self.assertEqual(['Damson', 'Alpha', 'Cherry', 'Banana'], [i['title'] for i in result])

	def test_call_site_passes_no_fallback(self):
		# Deliberate asymmetry with the Trakt and TMDb call sites: sort_order is a column on the
		# personal_lists row itself, so every list always has a stored value and the migration always
		# writes an override for it. A 'default:asc' fallback would mean DB insertion order.
		self.assertIsNone(self._call()['fallback'])


if __name__ == '__main__':
	unittest.main()
