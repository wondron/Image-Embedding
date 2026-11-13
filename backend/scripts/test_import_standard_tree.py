"""离线验证类别导入：不连接数据库或调用后端。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_standard_tree as importer


SOURCE = '''
raise RuntimeError("module must not be executed")
def create_standard_tree():
    tree = Tree()
    tree.create_node('*食材相似度*', 'Food', data={'type': False})
    tree.create_node('谷物', 'Grain', parent='Food', data={'type': False})
    tree.create_node('大米', 'Rice', parent='Grain', data={'别称': ['米饭', '大米', '米饭']})
    tree.create_node('食材甲', 'HSheng', parent='Grain', data={'别称': {'别称乙', '别称甲'}})
    tree.create_node('食材乙', 'HShenG', parent='Grain')
    tree.create_node('食材丙', 'hSheng', parent='Grain')
    return tree
'''


class FakeAPI:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.writes = 0
        self.fail_at: int | None = None

    def list_categories(self) -> list[dict]:
        return list(self.rows)

    def create_category(self, body: dict) -> dict:
        if self.fail_at == self.writes:
            raise RuntimeError('connection lost')
        self.writes += 1
        row = {**body, 'id': len(self.rows) + 1, 'name': body['names'][0]['name']}
        self.rows.append(row)
        return row


class ImportStandardTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'standard_tree.py'
        self.path.write_text(SOURCE, encoding='utf-8')
        self.nodes = importer.read_tree(self.path)

    def test_static_source_hierarchy_aliases_and_case_sensitive_ids(self) -> None:
        self.assertEqual(self.nodes[0].code, 'ingredient')
        self.assertEqual(self.nodes[0].name, '食材')
        self.assertEqual(self.nodes[2].parent, 'Grain')
        self.assertEqual(self.nodes[2].aliases, ('米饭',))
        self.assertEqual(self.nodes[3].aliases, ('别称乙', '别称甲'))
        self.assertEqual(len({n.code for n in self.nodes}), len(self.nodes))
        for node in self.nodes:
            self.assertRegex(node.code, r'^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$')

    def test_unsupported_function_body_is_rejected_without_execution(self) -> None:
        self.path.write_text(SOURCE.replace('    return tree', '    dangerous_call()\n    return tree'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '仅解析静态'):
            importer.read_tree(self.path)

    def test_missing_parent_and_duplicate_identifier_rejected(self) -> None:
        for source in (SOURCE.replace("parent='Grain'", "parent='Missing'"),
                       SOURCE.replace("'hSheng'", "'HSheng'")):
            with self.subTest(source=source):
                self.path.write_text(source, encoding='utf-8')
                with self.assertRaises(ValueError):
                    importer.read_tree(self.path)

    def test_adds_parent_first_and_repeated_run_adds_nothing(self) -> None:
        api = FakeAPI()
        self.assertEqual(importer.import_tree(self.nodes, api), (6, 0))
        self.assertEqual(api.rows[0]['parent_id'], None)
        self.assertEqual(api.rows[2]['parent_id'], api.rows[1]['id'])
        self.assertEqual(api.rows[2]['names'][1]['name'], '米饭')
        self.assertTrue(api.rows[1]['is_active'])  # recognition type=False is not an inactive category
        self.assertEqual(importer.import_tree(self.nodes, api), (0, 6))
        self.assertEqual(api.writes, 6)

    def test_existing_root_reused_and_late_conflict_prevents_all_writes(self) -> None:
        api = FakeAPI()
        api.rows.append({'id': 10, 'code': 'ingredient', 'name': '现有食材域', 'parent_id': None})
        self.assertEqual(importer.import_tree(self.nodes, api), (5, 1))
        self.assertEqual(api.rows[1]['parent_id'], 10)
        self.assertEqual(api.rows[0]['name'], '现有食材域')
        # Last planned node was moved by a user: validate all conflicts up front.
        api.rows[-1]['parent_id'] = 10
        api.rows.pop(2)
        api.writes = 0
        with self.assertRaisesRegex(ValueError, '现有父级不同'):
            importer.import_tree(self.nodes, api)
        self.assertEqual(api.writes, 0)

    def test_partial_failure_can_resume_without_duplicate_categories(self) -> None:
        api = FakeAPI()
        api.fail_at = 3
        with self.assertRaisesRegex(RuntimeError, '重跑同一命令可继续'):
            importer.import_tree(self.nodes, api)
        api.fail_at = None
        self.assertEqual(importer.import_tree(self.nodes, api), (3, 3))
        self.assertEqual(len(api.rows), 6)

    def test_default_preview_does_not_contact_backend(self) -> None:
        with patch.object(importer, 'CategoryAPI') as client, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(importer.main(['--source', str(self.path)]), 0)
        client.assert_not_called()
        self.assertIn('PREVIEW', out.getvalue())


if __name__ == '__main__':
    unittest.main()
