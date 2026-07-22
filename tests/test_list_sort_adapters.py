import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIST_SORT_PATH = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'modules' / 'list_sort.py'


def _load_list_sort_module():
	spec = importlib.util.spec_from_file_location('list_sort_adapters_under_test', LIST_SORT_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


list_sort = _load_list_sort_module()


class AdapterRegistryTests(unittest.TestCase):
	def test_all_adapters_registered(self):
		expected = {'trakt_sync', 'trakt_list', 'simkl', 'mdblist_watchlist', 'mdblist_collection', 'personal', 'tmdb'}
		self.assertEqual(expected, set(list_sort.ADAPTERS))

	def test_capabilities_are_valid_fields(self):
		for name, adapter in list_sort.ADAPTERS.items():
			for field in adapter['capabilities']:
				self.assertIn(field, list_sort.VALID_FIELDS, '%s declares unknown field %s' % (name, field))

	def test_every_capability_has_an_extractor_or_is_synthetic(self):
		synthetic = ('random', 'default')
		for name, adapter in list_sort.ADAPTERS.items():
			for field in adapter['capabilities']:
				if field in synthetic: continue
				self.assertIn(field, adapter['fields'], '%s declares %s with no extractor' % (name, field))

	def test_field_choices_returns_capabilities(self):
		self.assertEqual(list_sort.ADAPTERS['tmdb']['capabilities'], list_sort.field_choices('tmdb'))

	def test_field_choices_unknown_adapter_is_empty(self):
		self.assertEqual((), list_sort.field_choices('nope'))


class TraktSyncAdapterTests(unittest.TestCase):
	def setUp(self):
		self.data = [
			{'title': 'B', 'collected_at': '2024-02-01', 'released': '2001-01-01'},
			{'title': 'A', 'collected_at': '2024-03-01', 'released': '1999-01-01'},
		]

	def test_date_added_reads_collected_at(self):
		result = list_sort.apply(self.data, {'field': 'date_added', 'direction': 'desc'}, list_sort.TRAKT_SYNC)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_release_date_missing_uses_sentinel(self):
		data = [{'title': 'A', 'collected_at': '', 'released': None}, {'title': 'B', 'collected_at': '', 'released': '1999-01-01'}]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.TRAKT_SYNC)
		self.assertEqual(['B', 'A'], [i['title'] for i in result])


class SimklAdapterTests(unittest.TestCase):
	def test_date_added_reads_collected_at(self):
		data = [{'title': 'B', 'collected_at': '2024-02-01', 'released': '2001-01-01'},
			{'title': 'A', 'collected_at': '2024-03-01', 'released': '1999-01-01'}]
		result = list_sort.apply(data, {'field': 'date_added', 'direction': 'desc'}, list_sort.SIMKL)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_release_date_reads_released(self):
		data = [{'title': 'B', 'collected_at': '', 'released': '2001-01-01'},
			{'title': 'A', 'collected_at': '', 'released': '1999-01-01'}]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.SIMKL)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_release_date_missing_uses_sentinel(self):
		data = [{'title': 'A', 'collected_at': '', 'released': None}, {'title': 'B', 'collected_at': '', 'released': '1999-01-01'}]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.SIMKL)
		self.assertEqual(['B', 'A'], [i['title'] for i in result])


class TraktListAdapterTests(unittest.TestCase):
	def setUp(self):
		self.data = [
			{'type': 'movie', 'rank': 2, 'listed_at': '2024-01-01', 'movie': {'title': 'B', 'released': '2001-01-01', 'rating': 5.0, 'votes': 10, 'runtime': 100}},
			{'type': 'show', 'rank': 1, 'listed_at': '2024-02-01', 'show': {'title': 'A', 'first_aired': '1999-01-01', 'rating': 9.0, 'votes': 20, 'runtime': 40}},
		]

	def test_title_reads_nested_node(self):
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'asc'}, list_sort.TRAKT_LIST)
		self.assertEqual([2, 1], [i['rank'] for i in result][::-1])

	def test_rank_ascending(self):
		result = list_sort.apply(self.data, {'field': 'rank', 'direction': 'asc'}, list_sort.TRAKT_LIST)
		self.assertEqual([1, 2], [i['rank'] for i in result])

	def test_release_date_falls_back_to_first_aired(self):
		result = list_sort.apply(self.data, {'field': 'release_date', 'direction': 'asc'}, list_sort.TRAKT_LIST)
		self.assertEqual(['show', 'movie'], [i['type'] for i in result])

	def test_votes_descending(self):
		result = list_sort.apply(self.data, {'field': 'votes', 'direction': 'desc'}, list_sort.TRAKT_LIST)
		self.assertEqual(['show', 'movie'], [i['type'] for i in result])

	def test_missing_node_does_not_raise(self):
		data = [{'type': 'movie'}, {'type': 'show', 'show': {'title': 'A'}}]
		result = list_sort.apply(data, {'field': 'title', 'direction': 'asc'}, list_sort.TRAKT_LIST)
		self.assertEqual(2, len(result))


class MdblistAdapterTests(unittest.TestCase):
	def test_watchlist_date_added_reads_watchlist_at(self):
		data = [{'title': 'B', 'watchlist_at': '2024-01-01', 'release_date': '2001-01-01'},
			{'title': 'A', 'watchlist_at': '2024-02-01', 'release_date': '1999-01-01'}]
		result = list_sort.apply(data, {'field': 'date_added', 'direction': 'desc'}, list_sort.MDBLIST_WATCHLIST)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_collection_release_date_reads_year(self):
		data = [{'title': 'B', 'collected_at': '', 'year': 2001}, {'title': 'A', 'collected_at': '', 'year': 1999}]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.MDBLIST_COLLECTION)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_collection_missing_year_sorts_last(self):
		data = [{'title': 'B', 'collected_at': '', 'year': None}, {'title': 'A', 'collected_at': '', 'year': 1999}]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.MDBLIST_COLLECTION)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_collection_malformed_year_sorts_last_without_unsorting_others(self):
		data = [
			{'title': 'C', 'collected_at': '', 'year': 'TBA'},
			{'title': 'B', 'collected_at': '', 'year': 2001},
			{'title': 'A', 'collected_at': '', 'year': 1999},
		]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.MDBLIST_COLLECTION)
		self.assertEqual(['A', 'B', 'C'], [i['title'] for i in result])


class PersonalAdapterTests(unittest.TestCase):
	def test_date_added_compares_numerically(self):
		data = [{'title': 'B', 'date_added': '100', 'release_date': None}, {'title': 'A', 'date_added': '20', 'release_date': None}]
		result = list_sort.apply(data, {'field': 'date_added', 'direction': 'asc'}, list_sort.PERSONAL)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_missing_release_date_sorts_last(self):
		data = [{'title': 'B', 'date_added': '1', 'release_date': None}, {'title': 'A', 'date_added': '2', 'release_date': '1999-01-01'}]
		result = list_sort.apply(data, {'field': 'release_date', 'direction': 'asc'}, list_sort.PERSONAL)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_malformed_date_added_sorts_last_without_unsorting_others(self):
		data = [
			{'title': 'C', 'date_added': 'corrupt', 'release_date': None},
			{'title': 'B', 'date_added': '100', 'release_date': None},
			{'title': 'A', 'date_added': '20', 'release_date': None},
		]
		result = list_sort.apply(data, {'field': 'date_added', 'direction': 'asc'}, list_sort.PERSONAL)
		self.assertEqual(['C', 'A', 'B'], [i['title'] for i in result])


class TmdbAdapterTests(unittest.TestCase):
	def test_default_field_restores_provider_order(self):
		# The rows arrive out of original_order on purpose: tmdblist_api appends pages 2..N in thread
		# completion order, so the payload order is not TMDb's. A fixture already in original_order
		# would pass against an engine that simply returned the payload untouched.
		data = [{'title': 'B', 'original_order': 1, 'release_date': None},
			{'title': 'C', 'original_order': 2, 'release_date': None},
			{'title': 'A', 'original_order': 0, 'release_date': None}]
		result = list_sort.apply(data, {'field': 'default', 'direction': 'asc'}, list_sort.TMDB)
		self.assertEqual(['A', 'B', 'C'], [i['title'] for i in result])

	def test_default_field_ignores_direction(self):
		# 'default' is in DIRECTIONLESS_FIELDS; a stored 'default:desc' must not reverse the list.
		data = [{'title': 'B', 'original_order': 1}, {'title': 'A', 'original_order': 0}]
		result = list_sort.apply(data, {'field': 'default', 'direction': 'desc'}, list_sort.TMDB)
		self.assertEqual(['A', 'B'], [i['title'] for i in result])

	def test_default_field_missing_original_order_sorts_last(self):
		data = [{'title': 'X'}, {'title': 'B', 'original_order': 1}, {'title': 'A', 'original_order': 0}]
		result = list_sort.apply(data, {'field': 'default', 'direction': 'asc'}, list_sort.TMDB)
		self.assertEqual(['A', 'B', 'X'], [i['title'] for i in result])

	def test_adapters_without_a_default_extractor_hand_the_payload_back(self):
		# Only TMDb declares one. Every other adapter's 'default' must stay the pass-through it was,
		# or their provider ordering changes on upgrade too.
		data = [{'title': 'B'}, {'title': 'A'}]
		for name in ('trakt_list', 'simkl', 'personal'):
			adapter = list_sort.ADAPTERS[name]
			self.assertNotIn('default', adapter['fields'], name)
			result = list_sort.apply(data, {'field': 'default', 'direction': 'asc'}, adapter)
			self.assertEqual(['B', 'A'], [i['title'] for i in result], name)

	def test_tmdb_has_no_date_added(self):
		self.assertNotIn('date_added', list_sort.TMDB['capabilities'])


if __name__ == '__main__':
	unittest.main()
