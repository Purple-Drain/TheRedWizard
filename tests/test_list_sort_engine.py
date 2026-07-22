import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIST_SORT_PATH = ROOT / 'plugin.video.redlight' / 'resources' / 'lib' / 'modules' / 'list_sort.py'


def _load_list_sort_module():
	spec = importlib.util.spec_from_file_location('list_sort_under_test', LIST_SORT_PATH)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


list_sort = _load_list_sort_module()

ADAPTER = {
	'capabilities': ('title', 'date_added', 'release_date', 'rating', 'random', 'default'),
	'fields': {
		'title': lambda i: i.get('title'),
		'date_added': lambda i: i.get('added') or '',
		'release_date': lambda i: i.get('released') or '2050-01-01',
		'rating': lambda i: i.get('rating') or 0,
	},
}


class ParseSpecTests(unittest.TestCase):
	def test_parses_field_and_direction(self):
		self.assertEqual({'field': 'date_added', 'direction': 'desc'}, list_sort.parse_spec('date_added:desc'))

	def test_missing_direction_defaults_to_asc(self):
		self.assertEqual({'field': 'title', 'direction': 'asc'}, list_sort.parse_spec('title'))

	def test_unknown_field_falls_back(self):
		self.assertEqual(list_sort.DEFAULT_SPEC, list_sort.parse_spec('nonsense:desc'))

	def test_unknown_direction_falls_back_to_asc(self):
		self.assertEqual({'field': 'title', 'direction': 'asc'}, list_sort.parse_spec('title:sideways'))

	def test_empty_uses_supplied_fallback(self):
		fallback = {'field': 'rating', 'direction': 'desc'}
		self.assertEqual(fallback, list_sort.parse_spec('', fallback))

	def test_roundtrips_through_format(self):
		self.assertEqual('release_date:desc', list_sort.format_spec({'field': 'release_date', 'direction': 'desc'}))


class SpecLabelTests(unittest.TestCase):
	def test_labels_field_and_direction(self):
		self.assertEqual('Date Added (descending)', list_sort.spec_label({'field': 'date_added', 'direction': 'desc'}))

	def test_directionless_fields_omit_direction(self):
		self.assertEqual('Random', list_sort.spec_label({'field': 'random', 'direction': 'asc'}))
		self.assertEqual('Provider Default', list_sort.spec_label({'field': 'default', 'direction': 'asc'}))


class StripArticlesTests(unittest.TestCase):
	def test_strips_leading_article_when_enabled(self):
		self.assertEqual('matrix', list_sort.strip_articles('The Matrix', True))

	def test_keeps_article_when_disabled(self):
		self.assertEqual('the matrix', list_sort.strip_articles('The Matrix', False))

	def test_only_strips_whole_words(self):
		self.assertEqual('theodore rex', list_sort.strip_articles('Theodore Rex', True))

	def test_handles_none(self):
		self.assertEqual('', list_sort.strip_articles(None, True))


class ApplyTests(unittest.TestCase):
	def setUp(self):
		self.data = [
			{'title': 'Banana', 'added': '2024-01-02', 'released': '2001-01-01', 'rating': 5.0},
			{'title': 'The Apple', 'added': '2024-01-03', 'released': None, 'rating': 9.0},
			{'title': 'cherry', 'added': '2024-01-01', 'released': '1999-01-01', 'rating': 7.0},
		]

	def _titles(self, result):
		return [i['title'] for i in result]

	def test_title_ascending_is_case_insensitive(self):
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'asc'}, ADAPTER, ignore_articles=False)
		self.assertEqual(['Banana', 'cherry', 'The Apple'], self._titles(result))

	def test_title_ascending_ignoring_articles(self):
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'asc'}, ADAPTER, ignore_articles=True)
		self.assertEqual(['The Apple', 'Banana', 'cherry'], self._titles(result))

	def test_title_descending_reverses_article_stripped_order(self):
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'desc'}, ADAPTER, ignore_articles=True)
		self.assertEqual(['cherry', 'Banana', 'The Apple'], self._titles(result))

	def test_date_added_descending(self):
		result = list_sort.apply(self.data, {'field': 'date_added', 'direction': 'desc'}, ADAPTER)
		self.assertEqual(['The Apple', 'Banana', 'cherry'], self._titles(result))

	def test_release_date_missing_sorts_last_ascending(self):
		result = list_sort.apply(self.data, {'field': 'release_date', 'direction': 'asc'}, ADAPTER)
		self.assertEqual(['cherry', 'Banana', 'The Apple'], self._titles(result))

	def test_rating_descending(self):
		result = list_sort.apply(self.data, {'field': 'rating', 'direction': 'desc'}, ADAPTER)
		self.assertEqual(['The Apple', 'cherry', 'Banana'], self._titles(result))

	def test_default_field_preserves_input_order(self):
		result = list_sort.apply(self.data, {'field': 'default', 'direction': 'asc'}, ADAPTER)
		self.assertEqual(['Banana', 'The Apple', 'cherry'], self._titles(result))

	def test_field_unsupported_by_adapter_preserves_order(self):
		result = list_sort.apply(self.data, {'field': 'runtime', 'direction': 'asc'}, ADAPTER)
		self.assertEqual(['Banana', 'The Apple', 'cherry'], self._titles(result))

	def test_random_returns_same_membership(self):
		result = list_sort.apply(self.data, {'field': 'random', 'direction': 'asc'}, ADAPTER)
		self.assertEqual(sorted(self._titles(self.data)), sorted(self._titles(result)))

	def test_empty_input_returns_empty(self):
		self.assertEqual([], list_sort.apply([], {'field': 'title', 'direction': 'asc'}, ADAPTER))

	def test_extractor_raising_preserves_order(self):
		broken = {'capabilities': ('title',), 'fields': {'title': lambda i: 1 / 0}}
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'asc'}, broken)
		self.assertEqual(['Banana', 'The Apple', 'cherry'], self._titles(result))

	def test_default_field_returns_new_list_not_same_object(self):
		result = list_sort.apply(self.data, {'field': 'default', 'direction': 'asc'}, ADAPTER)
		self.assertIsNot(self.data, result)

	def test_sorting_path_returns_new_list_not_same_object(self):
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'asc'}, ADAPTER)
		self.assertIsNot(self.data, result)

	def test_mutating_result_does_not_affect_input_on_aliasing_path(self):
		original = list(self.data)
		result = list_sort.apply(self.data, {'field': 'default', 'direction': 'asc'}, ADAPTER)
		result.append({'title': 'Zebra', 'added': '2024-01-04', 'released': None, 'rating': 1.0})
		result.sort(key=lambda i: i['title'])
		self.assertEqual(original, self.data)

	def test_mutating_result_does_not_affect_input_on_sorting_path(self):
		original = list(self.data)
		result = list_sort.apply(self.data, {'field': 'title', 'direction': 'asc'}, ADAPTER)
		result.append({'title': 'Zebra', 'added': '2024-01-04', 'released': None, 'rating': 1.0})
		result.sort(key=lambda i: i['title'])
		self.assertEqual(original, self.data)


if __name__ == '__main__':
	unittest.main()
