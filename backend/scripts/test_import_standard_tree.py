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
    tree.create_node('谷物', 'GrainX', parent='Food', data={'type': False})
    tree.create_node('大米', 'RiceAA', parent='GrainX', data={'别称': ['米饭', '大米', '米饭']})
    tree.create_node('食材甲', 'HShenA', parent='GrainX', data={'别称': {'别称乙', '别称甲'}})
    tree.create_node('食材乙', 'HShenB', parent='GrainX')
    tree.create_node('食材丙', 'HShenC', parent='GrainX')
    return tree
'''

LIQUID_SOURCE = '''
def create_standard_tree():
    tree = Tree()
    tree.create_node('*食材相似度*', 'Food')
    tree.create_node('液体类', 'YeTiLe', parent='Food')
    tree.create_node('白色液体', 'BaiSYe', parent='YeTiLe')
    tree.create_node('豆浆', 'DJiang', parent='BaiSYe')
    tree.create_node('酸奶类', 'SNaiLe', parent='BaiSYe')
    tree.create_node('原味酸奶', 'YuanSN', parent='SNaiLe')
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

    def test_static_source_hierarchy_aliases_and_case_preserving_codes(self) -> None:
        self.assertEqual(self.nodes[0].code, 'ingredient')
        self.assertEqual(self.nodes[0].name, '食材')
        self.assertEqual(self.nodes[2].parent, 'GrainX')
        self.assertEqual(self.nodes[2].aliases, ('米饭',))
        self.assertEqual(self.nodes[1].code, 'ingredient.GrainX')
        self.assertEqual(self.nodes[2].code, 'ingredient.GrainX.RiceAA')
        self.assertEqual(importer.category_code('HongSu'), 'ingredient.HongSu')
        self.assertEqual(importer.category_code('RiceAA', 'ingredient.GrainX'), 'ingredient.GrainX.RiceAA')
        self.assertEqual(self.nodes[3].aliases, ('别称乙', '别称甲'))
        self.assertEqual(len({n.code for n in self.nodes}), len(self.nodes))
        for node in self.nodes:
            self.assertRegex(node.code, r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$')

    def test_unsupported_function_body_is_rejected_without_execution(self) -> None:
        self.path.write_text(SOURCE.replace('    return tree', '    dangerous_call()\n    return tree'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '仅解析静态'):
            importer.read_tree(self.path)

    def test_missing_parent_and_duplicate_identifier_rejected(self) -> None:
        for source in (SOURCE.replace("parent='GrainX'", "parent='Missing'"),
                       SOURCE.replace("'HShenC'", "'HShenA'")):
            with self.subTest(source=source):
                self.path.write_text(source, encoding='utf-8')
                with self.assertRaises(ValueError):
                    importer.read_tree(self.path)

    def test_all_lowercase_collision_groups_are_logged_with_source_locations(self) -> None:
        source = SOURCE.replace('HShenA', 'HSheng').replace('HShenB', 'HShenG').replace('HShenC', 'hSheng')
        source = source.replace('    return tree',
                                "    tree.create_node('苹果甲', 'PinGLi', parent='GrainX')\n"
                                "    tree.create_node('苹果乙', 'PingLi', parent='GrainX')\n    return tree")
        self.path.write_text(source, encoding='utf-8')
        with self.assertLogs(importer.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, '2 组编码问题'):
                importer.read_tree(self.path)
        output = '\n'.join(logs.output)
        for value in ('ingredient.grainx.hsheng', 'ingredient.grainx.pingli', 'HSheng', 'HShenG', 'hSheng',
                      'PinGLi', 'PingLi', '食材甲', '食材乙', '食材丙', str(self.path)):
            self.assertIn(value, output)
        self.assertRegex(output, r'standard_tree\.py:\d+')

    def test_invalid_identifiers_are_logged_without_silent_character_replacement(self) -> None:
        self.path.write_text(SOURCE.replace('HShenA', 'bad-id').replace('HShenB', 'bad id'), encoding='utf-8')
        with self.assertLogs(importer.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, '2 组编码问题'):
                importer.read_tree(self.path)
        output = '\n'.join(logs.output)
        self.assertIn('ingredient.GrainX.bad-id', output)
        self.assertIn('ingredient.GrainX.bad id', output)

    def test_non_root_identifiers_require_exactly_six_ascii_letters(self) -> None:
        for identifier in ('ShCHu', 'ShCHuaa', 'ShCHu1', 'ShCHu_', 'ShCHu中', 'ＳhCHua'):
            with self.subTest(identifier=identifier):
                self.path.write_text(SOURCE.replace('HShenA', identifier), encoding='utf-8')
                with self.assertLogs(importer.logger, level='ERROR') as logs:
                    with self.assertRaisesRegex(ValueError, '1 组编码问题'):
                        importer.read_tree(self.path)
                output = '\n'.join(logs.output)
                self.assertIn(repr(identifier), output)
                self.assertIn('6 个英文字母', output)
                self.assertIn(f'当前长度={len(identifier)}', output)
                self.assertIn(str(self.path), output)

    def test_six_letter_identifier_is_accepted_and_food_root_is_exempt(self) -> None:
        self.path.write_text(SOURCE.replace('HShenA', 'ShCHua'), encoding='utf-8')
        nodes = importer.read_tree(self.path)
        self.assertEqual((nodes[0].source_id, nodes[0].code), ('Food', 'ingredient'))
        self.assertEqual(nodes[3].code, 'ingredient.GrainX.ShCHua')

    def test_all_invalid_identifier_lengths_are_reported_in_one_pass(self) -> None:
        source = SOURCE.replace('HShenA', 'ShCHu').replace('HShenB', 'ShCHuaa').replace('HShenC', 'ShCHu1')
        self.path.write_text(source, encoding='utf-8')
        with self.assertLogs(importer.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, '3 组编码问题'):
                importer.read_tree(self.path)
        self.assertEqual(len(logs.records), 3)
        output = '\n'.join(logs.output)
        for identifier in ('ShCHu', 'ShCHuaa', 'ShCHu1'):
            self.assertIn(repr(identifier), output)

    def test_apply_with_invalid_identifier_never_contacts_backend(self) -> None:
        self.path.write_text(SOURCE.replace('HShenA', 'ShCHu'), encoding='utf-8')
        with patch.object(importer, 'CategoryAPI') as client, self.assertLogs(importer.logger, level='ERROR'), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(importer.main(['--source', str(self.path), '--apply']), 1)
        client.assert_not_called()

    def test_apply_with_collision_never_contacts_backend(self) -> None:
        self.path.write_text(SOURCE.replace('HShenB', 'hShenA'), encoding='utf-8')
        with patch.object(importer, 'CategoryAPI') as client, self.assertLogs(importer.logger, level='ERROR'), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(importer.main(['--source', str(self.path), '--apply']), 1)
        client.assert_not_called()

    def test_adds_parent_first_and_repeated_run_adds_nothing(self) -> None:
        api = FakeAPI()
        self.assertEqual(importer.import_tree(self.nodes, api), (6, 0))
        self.assertEqual(api.rows[0]['parent_id'], None)
        self.assertEqual(api.rows[2]['parent_id'], api.rows[1]['id'])
        self.assertEqual(api.rows[2]['code'], 'ingredient.GrainX.RiceAA')
        self.assertEqual(api.rows[2]['names'][1]['name'], '米饭')
        self.assertTrue(api.rows[1]['is_active'])  # recognition type=False is not an inactive category
        self.assertEqual(importer.import_tree(self.nodes, api), (0, 6))
        self.assertEqual(api.writes, 6)

    def test_existing_code_casing_is_ignored_without_renaming_or_duplicate_writes(self) -> None:
        api = FakeAPI()
        importer.import_tree(self.nodes, api)
        for row in api.rows:
            row['code'] = row['code'].swapcase()
        existing_codes = [row['code'] for row in api.rows]
        self.assertEqual(importer.import_tree(self.nodes, api), (0, 6))
        self.assertEqual(api.writes, 6)
        self.assertEqual([row['code'] for row in api.rows], existing_codes)

    def test_new_children_reuse_existing_parents_despite_code_casing(self) -> None:
        api = FakeAPI()
        importer.import_tree(self.nodes[:2], api)
        api.rows[0]['code'] = 'Ingredient'
        api.rows[1]['code'] = 'INGREDIENT.grainx'
        self.assertEqual(importer.import_tree(self.nodes, api), (4, 2))
        self.assertEqual(api.rows[2]['parent_id'], api.rows[1]['id'])
        self.assertEqual(api.rows[2]['code'], 'ingredient.GrainX.RiceAA')

    def test_liquids_keep_full_ancestor_codes_and_four_or_more_tree_levels(self) -> None:
        self.path.write_text(LIQUID_SOURCE, encoding='utf-8')
        nodes = importer.read_tree(self.path)
        by_source = {node.source_id: node for node in nodes}
        self.assertEqual(by_source['YeTiLe'].code, 'ingredient.YeTiLe')
        self.assertEqual(by_source['BaiSYe'].code, 'ingredient.YeTiLe.BaiSYe')
        self.assertEqual(by_source['DJiang'].code, 'ingredient.YeTiLe.BaiSYe.DJiang')
        self.assertEqual(by_source['DJiang'].depth, 3)
        self.assertEqual(by_source['YuanSN'].code, 'ingredient.YeTiLe.BaiSYe.SNaiLe.YuanSN')
        self.assertEqual(by_source['YuanSN'].depth, 4)
        api = FakeAPI()
        self.assertEqual(importer.import_tree(nodes, api), (6, 0))
        rows = {row['code']: row for row in api.rows}
        self.assertEqual(rows[by_source['DJiang'].code]['parent_id'], rows[by_source['BaiSYe'].code]['id'])
        self.assertEqual(rows[by_source['YuanSN'].code]['parent_id'], rows[by_source['SNaiLe'].code]['id'])
        for row in api.rows:
            if row['parent_id'] is not None:
                self.assertLess(row['parent_id'], row['id'])
        with patch.object(importer, 'CategoryAPI') as client, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(importer.main(['--source', str(self.path), '--show-tree']), 0)
        self.assertIn('豆浆 [ingredient.YeTiLe.BaiSYe.DJiang]', out.getvalue())
        client.assert_not_called()

    def test_case_equivalent_identifiers_under_different_parents_have_distinct_full_codes(self) -> None:
        source = SOURCE.replace('    return tree',
                                "    tree.create_node('薯类', 'ShuLei', parent='Food')\n"
                                "    tree.create_node('其他食材甲', 'hShenA', parent='ShuLei')\n    return tree")
        self.path.write_text(source, encoding='utf-8')
        nodes = importer.read_tree(self.path)
        by_source = {node.source_id: node for node in nodes}
        self.assertEqual(by_source['HShenA'].code, 'ingredient.GrainX.HShenA')
        self.assertEqual(by_source['hShenA'].code, 'ingredient.ShuLei.hShenA')
        self.assertEqual(len({node.code.lower() for node in nodes}), len(nodes))
        self.assertEqual(importer.import_tree(nodes, FakeAPI()), (8, 0))

    def test_existing_flat_codes_log_target_paths_and_stop_before_writing(self) -> None:
        api = FakeAPI()
        importer.import_tree(self.nodes[:2], api)
        api.rows.append({'id': 3, 'code': 'Ingredient.riceaa', 'name': '大米', 'parent_id': api.rows[1]['id']})
        api.writes = 0
        with self.assertLogs(importer.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, '旧版两段编码'):
                importer.import_tree(self.nodes, api)
        output = '\n'.join(logs.output)
        self.assertIn('Ingredient.riceaa', output)
        self.assertIn('ingredient.GrainX.RiceAA', output)
        self.assertIn('大米', output)
        self.assertEqual(api.writes, 0)
        self.assertEqual(len(api.rows), 3)

    def test_legitimate_top_level_flat_code_is_reused_not_reported_as_legacy(self) -> None:
        source = SOURCE.replace('    return tree',
                                "    tree.create_node('顶层食材甲', 'hShenA', parent='Food')\n    return tree")
        self.path.write_text(source, encoding='utf-8')
        nodes = importer.read_tree(self.path)
        api = FakeAPI()
        self.assertEqual(importer.import_tree([nodes[0], nodes[-1]], api), (2, 0))
        self.assertEqual(api.rows[-1]['code'], 'ingredient.hShenA')
        self.assertEqual(importer.import_tree(nodes, api), (5, 2))
        self.assertEqual(len(api.rows), 7)

    def test_case_insensitive_name_conflict_stops_before_any_writes(self) -> None:
        api = FakeAPI()
        importer.import_tree(self.nodes, api)
        api.rows[-1]['code'] = api.rows[-1]['code'].lower()
        api.rows[-1]['name'] = '不同类别'
        api.rows.pop(2)
        api.writes = 0
        with self.assertRaisesRegex(ValueError, '现有名称'):
            importer.import_tree(self.nodes, api)
        self.assertEqual(api.writes, 0)

    def test_existing_lowercase_code_collisions_are_all_logged_before_any_writes(self) -> None:
        api = FakeAPI()
        api.rows = [
            {'id': 1, 'code': 'Ingredient', 'name': '食材', 'parent_id': None},
            {'id': 2, 'code': 'ingredient', 'name': '重复根', 'parent_id': None},
            {'id': 3, 'code': 'ingredient.GrainX', 'name': '谷物', 'parent_id': 1},
            {'id': 4, 'code': 'Ingredient.grainx', 'name': '重复谷物', 'parent_id': 2},
        ]
        with self.assertLogs(importer.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, '转小写后重复'):
                importer.import_tree(self.nodes, api)
        output = '\n'.join(logs.output)
        for row in api.rows:
            self.assertIn(repr(row['code']), output)
            self.assertIn(f"id={row['id']}", output)
        self.assertEqual(api.writes, 0)

    def test_legacy_hashed_codes_stop_before_creating_a_duplicate_tree(self) -> None:
        api = FakeAPI()
        api.rows.append({'id': 7, 'code': 'ingredient.standard.n_rice_legacy', 'name': '大米', 'parent_id': 1})
        with self.assertLogs(importer.logger, level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, '旧编码'):
                importer.import_tree(self.nodes, api)
        self.assertIn('ingredient.standard.n_rice_legacy', '\n'.join(logs.output))
        self.assertEqual(api.writes, 0)
        self.assertEqual(len(api.rows), 1)

    def test_legacy_hashed_code_detection_is_case_insensitive(self) -> None:
        api = FakeAPI()
        api.rows.append({'id': 7, 'code': 'Ingredient.Standard.N_rice_legacy', 'name': '大米', 'parent_id': 1})
        with self.assertLogs(importer.logger, level='ERROR'):
            with self.assertRaisesRegex(ValueError, '旧编码'):
                importer.import_tree(self.nodes, api)
        self.assertEqual(api.writes, 0)

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
