"""Selection checks for the sentence-focused review; no browser or device access."""
import importlib.util
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('copy_review_build', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CatalogReviewTests(unittest.TestCase):
    def test_published_page_contains_matching_controls_script_and_catalog(self):
        class PageContract(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = set()
                self.scripts = []
                self.current_script = None
                self.external_scripts = []

            def handle_starttag(self, tag, attributes):
                attrs = dict(attributes)
                if 'id' in attrs:
                    if attrs['id'] in self.ids:
                        raise AssertionError('Duplicate page control: ' + attrs['id'])
                    self.ids.add(attrs['id'])
                if tag == 'script':
                    if 'src' in attrs:
                        self.external_scripts.append(attrs['src'])
                    self.current_script = {'attributes': attrs, 'text': ''}
                    self.scripts.append(self.current_script)

            def handle_data(self, text):
                if self.current_script is not None:
                    self.current_script['text'] += text

            def handle_endtag(self, tag):
                if tag == 'script':
                    self.current_script = None

        page = PageContract()
        page.feed((HERE / 'site/index.html').read_text())
        self.assertEqual(page.external_scripts, [], 'Published pages must not reuse an older external script.')
        catalog_script = next(script for script in page.scripts if script['attributes'].get('id') == 'copy-catalog')
        self.assertEqual(json.loads(catalog_script['text']), json.loads((HERE / 'site/catalog.json').read_text()))
        app_script = next(script for script in page.scripts if script['attributes'].get('type') != 'application/json')
        source = (HERE / 'site/app.js').read_text()
        self.assertEqual(app_script['text'].strip(), source.strip())
        selectors = re.findall(r"\$\('([^']+)'\)", source)
        for selector in selectors:
            self.assertIn(selector, page.ids, 'Script references a missing page control: ' + selector)
        self.assertIn('copy-catalog', page.ids)
        self.assertNotIn("fetch('catalog.json')", source, 'The page must use its own embedded catalog.')

    def test_six_word_boundary_counts_values_and_addresses_as_one_word(self):
        self.assertEqual(builder.word_count('Contact tinytouch@alpacaengineer.ing if this continues.'), 5)
        self.assertEqual(builder.word_count('Touch the same finger again now.'), 6)
        self.assertEqual(builder.word_count('The current value is {current.get(name, "not reported")!r}.'), 5)

    def test_repeated_menu_and_help_wording_is_one_entry_with_all_contexts(self):
        previous = builder.ENTRIES[:]
        try:
            builder.ENTRIES.clear()
            builder.add('1. Preview a color and effect without saving [preview]', 'Interactive menus', 'lighting menu')
            builder.add('Preview a color and effect without saving.', 'Command help', 'led help')
            builder.add('Back', 'Interactive menus', 'back action')
            entries, stats = builder.sentence_catalog({})
            self.assertEqual(len(entries), 1)
            self.assertEqual(len(entries[0]['occurrences']), 2)
            self.assertEqual(stats['duplicates'], 1)
            self.assertEqual(stats['omitted'], 1)
            self.assertEqual(len(builder.ENTRIES), 3)  # Selection keeps original occurrences.
        finally:
            builder.ENTRIES[:] = previous

    def test_published_catalog_has_unique_prose_with_preserved_template_values(self):
        catalog = json.loads((HERE / 'site/catalog.json').read_text())
        seen = set()
        for entry in catalog['entries']:
            self.assertGreaterEqual(builder.word_count(entry['original']), 6, entry['original'])
            self.assertGreaterEqual(builder.word_count(entry['proposed']), 6, entry['proposed'])
            key = builder.canonical(entry['original']).casefold().rstrip('.: ')
            self.assertNotIn(key, seen)
            seen.add(key)
            # Compare Python f-string template fields without interpreting them.
            import re
            fields = lambda value: re.findall(r'\{[^{}]*\}', value)
            self.assertCountEqual(fields(entry['original']), fields(entry['proposed']), entry['original'])
            self.assertNotIn(entry['group'], {'Technical appendix', 'Device diagnostics', 'Argument errors'})
        self.assertTrue(any(len(entry['occurrences']) > 20 for entry in catalog['entries']))
        original = json.loads((HERE / 'site/original-catalog.json').read_text())
        self.assertGreater(len(original['entries']), len(catalog['entries']))
        self.assertTrue(any(entry['original'] == 'Back' for entry in original['entries']))


if __name__ == '__main__':
    unittest.main()
