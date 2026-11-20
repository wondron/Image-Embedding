"""GroundingDINO：NMS 之后去掉容器框（把几个小框都框在里面的大框）。不需要模型。

固定数据是 GroundingDINO 在 backend/config/images 里两张图上的真实输出（CPU，NMS 之后）。
"""

import unittest
from unittest.mock import patch

from app.clients.ground_dino import GdinoDetect, container_stats, drop_containers


def normalized(x1, y1, x2, y2, width=677, height=508):
    """像素坐标 x1, y1, x2, y2 换成 GroundingDINO 输出的归一化 cx, cy, w, h。"""
    return [(x1 + x2) / 2 / width, (y1 + y2) / 2 / height, (x2 - x1) / width, (y2 - y1) / height]


# banlinangua.jpg（677 x 508），caption "food ."、阈值 0.35 / 0.25：四块南瓜各一个框，
# 外加一个把它们都框在里面的大框（占整张图的 79%，四块合起来盖住它的 92%）
PUMPKINS = [
    ((60, 177, 427, 458), 0.6938),   # 前面切开的半个
    ((428, 242, 666, 432), 0.6329),  # 右边的一角
    ((19, 36, 339, 320), 0.6757),    # 后面左边的整个
    ((19, 37, 664, 457), 0.3942),    # 把四块都框在里面的大框
    ((332, 66, 655, 341), 0.6262),   # 后面右边的整个
]
BOXES = [normalized(*box) for box, _ in PUMPKINS]

# recipe_9.jpg（1280 x 720），caption "food . plate . bowl"、阈值 0.3 / 0.25：整张图的 plate 框，
# 主菜（排骨）一个 food 框，主菜上面的胡萝卜、芹菜六个小框。主菜框装着六个小框，但它们只盖住
# 它的 11%：主菜要留下；plate 框装着主菜和小框，盖住它的 73%：去掉
RECIPE = [
    (75, 104, 1159, 714),   # 0 主菜
    (2, 1, 1261, 718),      # 1 plate
    (514, 103, 724, 184),
    (421, 159, 592, 273),
    (589, 165, 714, 279),
    (658, 131, 835, 169),
    (789, 197, 925, 305),
    (648, 161, 857, 217),
]
RECIPE_BOXES = [normalized(*box, width=1280, height=720) for box in RECIPE]


class ContainerStatsTests(unittest.TestCase):
    def test_counts_the_smaller_boxes_inside_and_how_much_of_the_box_they_cover(self):
        counts, coverage = container_stats(BOXES)
        self.assertEqual(counts.tolist(), [0, 0, 0, 4, 0])
        self.assertAlmostEqual(coverage[3], 0.916, places=3)
        counts, coverage = container_stats(RECIPE_BOXES)
        self.assertEqual(counts.tolist(), [6, 7, 0, 0, 0, 0, 0, 0])
        self.assertAlmostEqual(coverage[0], 0.106, places=3)
        self.assertAlmostEqual(coverage[1], 0.733, places=3)

    def test_overlapping_boxes_inside_are_counted_once(self):
        boxes = [normalized(0, 0, 100, 100), normalized(0, 0, 60, 100), normalized(40, 0, 100, 100)]
        counts, coverage = container_stats(boxes)
        self.assertEqual(counts[0], 2)
        self.assertAlmostEqual(coverage[0], 1.0)

    def test_only_the_part_inside_covers(self):
        boxes = [normalized(0, 0, 100, 100), normalized(0, 0, 50, 100), normalized(50, 0, 105, 100)]
        counts, coverage = container_stats(boxes, contain_ratio=0.9)  # 第三个有 1/11 在外面
        self.assertEqual(counts[0], 2)
        self.assertAlmostEqual(coverage[0], 1.0)


class DropContainersTests(unittest.TestCase):
    def test_the_box_around_the_pumpkins_goes_and_the_pumpkins_stay(self):
        self.assertEqual(drop_containers(BOXES).tolist(), [0, 1, 2, 4])

    def test_a_dish_with_small_boxes_on_it_stays_and_the_plate_around_everything_goes(self):
        self.assertEqual(drop_containers(RECIPE_BOXES).tolist(), [0, 2, 3, 4, 5, 6, 7])
        # 不看覆盖比例时主菜也会被当成容器去掉，只剩上面的配菜
        self.assertEqual(drop_containers(RECIPE_BOXES, min_coverage=0).tolist(), [2, 3, 4, 5, 6, 7])

    def test_a_big_box_with_fewer_boxes_inside_than_min_children_stays(self):
        boxes = [normalized(0, 0, 200, 200), normalized(0, 0, 180, 180)]
        self.assertEqual(drop_containers(boxes).tolist(), [0, 1])
        self.assertEqual(drop_containers(boxes, min_children=1).tolist(), [1])

    def test_a_box_only_partly_inside_does_not_count(self):
        big = normalized(0, 0, 200, 200)
        inside = normalized(0, 0, 150, 200)
        half_out = normalized(150, 20, 250, 80)  # 一半在大框外
        self.assertEqual(drop_containers([big, inside, half_out]).tolist(), [0, 1, 2])

    def test_contain_ratio_sets_how_much_of_a_box_must_be_inside(self):
        big = normalized(0, 0, 200, 200)
        inside = normalized(20, 20, 80, 80)
        mostly = normalized(130, 20, 230, 80)  # 70% 在大框里
        self.assertEqual(drop_containers([big, inside, mostly], contain_ratio=0.9, min_coverage=0).tolist(), [0, 1, 2])
        self.assertEqual(drop_containers([big, inside, mostly], contain_ratio=0.6, min_coverage=0).tolist(), [1, 2])

    def test_min_coverage_sets_how_much_the_boxes_inside_must_cover(self):
        self.assertEqual(drop_containers(BOXES, min_coverage=0.95).tolist(), [0, 1, 2, 3, 4])  # 只盖住 92%
        self.assertEqual(drop_containers(BOXES, min_coverage=0.9).tolist(), [0, 1, 2, 4])

    def test_nested_containers_all_go(self):
        outer = normalized(0, 0, 100, 100)
        middle = normalized(0, 0, 100, 90)
        left = normalized(0, 0, 50, 90)
        right = normalized(50, 0, 100, 90)
        self.assertEqual(drop_containers([outer, middle, left, right]).tolist(), [2, 3])

    def test_boxes_of_the_same_size_do_not_contain_each_other(self):
        box = normalized(10, 10, 100, 100)
        self.assertEqual(drop_containers([box, box, normalized(10, 10, 90, 90)]).tolist(), [0, 1, 2])

    def test_zero_or_too_few_boxes_keep_everything(self):
        self.assertEqual(drop_containers(BOXES, min_children=0).tolist(), [0, 1, 2, 3, 4])
        self.assertEqual(drop_containers(BOXES[:2]).tolist(), [0, 1])
        self.assertEqual(drop_containers([]).tolist(), [])


class CannedDetector(GdinoDetect):
    """真的 GdinoDetect（NMS、去容器框都是真的），只把模型推理换成固定结果。"""

    def __init__(self, result, **config):
        settings = {"model_path": "gdino.onnx", "tokenizer_path": "bert-base-uncased", "caption": "food .", **config}
        with patch.object(GdinoDetect, "load_model"):
            super().__init__({"GROUNDINGDINO_CONFIG": settings})
        self.sess = object()  # detect() 只检查它不是 None
        self.result = result

    def predict_with_metadata(self, image, caption, box_threshold, text_threshold, *, validate_thresholds=True):
        return {key: list(value) for key, value in self.result.items()}, {}


RESULT = {"boxes": BOXES, "logits": [score for _, score in PUMPKINS], "phrases": ["food"] * len(PUMPKINS)}


class DetectTests(unittest.TestCase):
    def test_detect_drops_the_container_after_nms_and_keeps_scores_and_labels_aligned(self):
        with self.assertLogs("g-dino", level="INFO") as logs:
            boxes, scores, labels = CannedDetector(RESULT).detect(None)
        self.assertEqual(len(boxes), 4)
        self.assertEqual([round(float(score), 4) for score in scores], [0.6938, 0.6329, 0.6757, 0.6262])
        self.assertEqual(labels, ["food"] * 4)
        self.assertIn("去掉容器框 | dropped=1 | kept=4", "\n".join(logs.output))

    def test_the_configuration_can_turn_it_off_or_make_it_stricter(self):
        for config, count in (
            ({"container_min_children": 0}, 5),
            ({"container_min_children": 5}, 5),  # 大框里只有 4 个
            ({"container_min_children": 4}, 4),
            ({"container_min_coverage": 0.95}, 5),  # 四块只盖住它的 92%
        ):
            with self.subTest(config=config):
                self.assertEqual(len(CannedDetector(RESULT, **config).detect(None)[0]), count)

    def test_invalid_settings_fail_the_load(self):
        for config in (
            {"container_min_children": -1}, {"container_contain_ratio": 0}, {"container_contain_ratio": 1.5},
            {"container_min_coverage": -0.1}, {"container_min_coverage": 1.5},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                CannedDetector(RESULT, **config)


if __name__ == "__main__":
    unittest.main()
