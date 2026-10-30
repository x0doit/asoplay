"""
Постеры и названия в ленте активности профиля — без живой базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import ast
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

source = ast.parse((Path(__file__).resolve().parents[2] / 'core/functions/activity.py').read_text(encoding='utf-8'))
functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in ('_fetch_recent', '_resolve_kinds', '_activity_title')]
namespace = {'Any': Any, 'KIND_GROUPS': {'watch': ['watch_start', 'watch_continue', 'complete']}, 'WATCH_EVENT_HAS_PROGRESS_SQL': 'PROGRESS_EXISTS'}
exec(compile(ast.Module(body=functions, type_ignores=[]), '<activity functions>', 'exec'), namespace)


class Cursor:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))

    def fetchall(self):
        return next(self.batches)

    def cursor(self):
        return self


class PosterTests(unittest.TestCase):
    def test_preserves_watch_filter_order_and_existing_titles(self):
        cur = Cursor([
            [('watch_start', 1, 'ep=2', datetime(2026, 10, 26, 12)), ('rate', 2, '', None)],
            [(1, 'Saved title', None), (2, '', '/saved.jpg')],
            [(1, 'Catalog title', '', '/catalog.jpg'), (2, '', 'Other title', '/other.jpg')],
        ])
        rows = namespace['_fetch_recent'](cur, 123, offset=10, limit=11, group='watch', day='2026-10-26')
        self.assertEqual(rows[0]['title'], 'Saved title')
        self.assertEqual(rows[0]['poster_url'], '/catalog.jpg')
        self.assertEqual(rows[0]['episode_num'], 2)
        self.assertEqual(rows[1]['title'], 'Other title')
        self.assertEqual(rows[1]['poster_url'], '/saved.jpg')
        sql, params = cur.queries[0]
        self.assertIn('PROGRESS_EXISTS', sql)
        self.assertIn('ORDER BY a.at DESC', sql)
        self.assertEqual(params, (123, 'watch_start', 'watch_continue', 'complete', '2026-10-26', 11, 10))

    def test_missing_metadata_and_empty_history(self):
        rows = namespace['_fetch_recent'](Cursor([[('favorite', 1, '', None)], [], []]), 123)
        self.assertEqual(rows[0]['poster_url'], '')
        self.assertEqual(rows[0]['title'], '')
        cur = Cursor([[]])
        self.assertEqual(namespace['_fetch_recent'](cur, 123), [])
        self.assertEqual(len(cur.queries), 1)

    def test_corrupted_list_title_uses_catalog_without_changing_events(self):
        cur = Cursor([
            [('watch_continue', 52619, 'ep=2', datetime(2026, 4, 30, 20, 33, 24))],
            [(52619, '? ??????????? ????????', '/saved.jpg')],
            [(52619, '\u042f \u043f\u0435\u0440\u0435\u0440\u043e\u0434\u0438\u043b\u0441\u044f', 'Reborn', '/catalog.jpg')],
        ])
        row = namespace['_fetch_recent'](cur, 1)[0]
        self.assertEqual(row['title'], '\u042f \u043f\u0435\u0440\u0435\u0440\u043e\u0434\u0438\u043b\u0441\u044f')
        self.assertEqual(row['poster_url'], '/saved.jpg')
        self.assertEqual(row['episode_num'], 2)
        self.assertEqual(row['at'], '2026-04-30T20:33:24')
        self.assertEqual(row['kind'], 'watch_continue')
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql, _ in cur.queries))

    def test_damaged_russian_catalog_title_falls_back_to_english(self):
        cur = Cursor([[('list_add', 1, '', None)], [(1, '???', '')], [(1, '\ufffd???', 'Valid title', '')]])
        self.assertEqual(namespace['_fetch_recent'](cur, 1)[0]['title'], 'Valid title')

    def test_no_valid_title_leaves_existing_id_fallback(self):
        cur = Cursor([[('list_add', 1, '', None)], [(1, '??? ???', '/saved.jpg')], [(1, '???', None, '')]])
        self.assertEqual(namespace['_fetch_recent'](cur, 1)[0]['title'], '')

    def test_valid_titles_keep_question_marks_and_unicode(self):
        for title in ['Is This a Zombie?', 'Working!!!', '\u3051\u3044\u304a\u3093\uff01', '\u0427\u0442\u043e???', 'Why???', '86']:
            with self.subTest(title=title):
                self.assertEqual(namespace['_activity_title'](title), title)


if __name__ == '__main__':
    unittest.main()
