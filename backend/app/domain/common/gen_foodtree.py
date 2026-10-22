import os, shutil, re
from treelib import Tree
import logging


logger = logging.getLogger("gen_foodtree")


# 菜谱类型映射
RECIPE_TYPE_MAP = {
    "P": "P档菜谱",
    "Y": "云端菜谱",
    "A": "AI菜谱",
    "D": "待开发菜谱",
}


class IngredientTreeBuilder:
    def __init__(self, filepath: str, src2tgt: dict={}):
        """
        filepath: 文件路径，例如 ingredients.txt
        """
        self.filepath = filepath
        self.tree = Tree()
        self.tree.create_node(
            tag="食材识别",
            identifier="root",
            data={
                "kind": "category",
                "recipes": [],
                "level": 0,
                "index": 0,
            }
        )
        self.tag2ids = {}
        self.build(src2tgt)
        # self.print_all_ingredients()


    def parse_food_names(self, s: str):
        """
        规则：
        1) 格式： 名称1(名称2X, 名称3X...)X
        2) 如果括号内每个子名称都带字母，则拆分：
            蒸包子(青菜包Y,鲜肉包Y)Y
            → 蒸包子Y, 青菜包Y, 鲜肉包Y
        3) 如果括号内没有字母，则不拆分：
            二米饭(大米,糙米)P
            → 二米饭P
        """

        s = s.strip()
        flag = s[-1]
        if flag not in "PYAD":
            logger.warning(f"未知的菜谱类型：{s}")
            return []

        # 没括号直接返回
        if "(" not in s:
            return [s]

        # 提取三部分：主名、括号内容、主名后缀字母
        m = re.match(r"^(.*?)\((.*?)\)([A-Za-z]?)$", s)
        if not m:
            return [s]

        main_name = m.group(1).strip()     # e.g. 二米饭
        inner = m.group(2).strip()         # e.g. 大米,糙米
        suffix = m.group(3).strip()        # e.g. P

        # 判断括号内是否含字母（叶子标签）
        inner_items = [x.strip() for x in inner.split(",") if x.strip()]
        inner_has_suffix = any(
            re.search(r"[A-Za-z]$", item) for item in inner_items
        )

        # 情况 A：括号内没有字母 → 不拆分
        if not inner_has_suffix:
            if suffix:
                return [main_name + suffix]
            else:
                return [main_name]

        # 情况 B：括号内有字母 → 主名 + 子名称全展开
        results = []

        # 主名带后缀
        if suffix:
            results.append(main_name + suffix)
        else:
            results.append(main_name)

        # 子项（本身带字母）
        results.extend(inner_items)

        return results

    # ------------------ 工具：解析菜谱 ------------------
    def parse_recipe(self, raw: str) -> dict | None:
        """
        输入: '蒜蓉烤茄子Y'
        输出: {'name': '蒜蓉烤茄子', 'code': 'Y', 'type': '云端菜谱'}
        """
        if not raw or len(raw) < 2:
            return None
        raw = raw.strip()
        code = raw[-1]            # 最后一位字母
        name = raw[:-1].strip()   # 前面部分是菜名
        type_name = RECIPE_TYPE_MAP.get(code, "未知菜谱类型")

        return {
            "name": name,
            "code": code,
            "type": type_name,
        }

    # ------------------ 工具：创建层级节点 ------------------
    def ensure_node(self, parent_id: str, name: str, is_leaf: bool, isMerge: bool = False) -> str:
        """
        生成唯一 node_id，并确保节点存在。
        返回 node_id。
        """
        node_id = f"{parent_id}/{name}" if parent_id != "root" else name
        if not self.tree.contains(node_id):
            node_idx = len(self.tree.children(parent_id))
            self.tag2ids[name] = node_id
            self.tree.create_node(
                tag=name,
                identifier=node_id,
                parent=parent_id,
                data={
                    "kind": "ingredient" if is_leaf else "category",
                    "recipes": [],
                    'index': node_idx,
                    'level': len(node_id.split("/")),
                    'isMerge': isMerge,
                    'sub_recipes': []
                }
            )
        return node_id

    # ------------------ 主流程：读取文件，构建树 ------------------
    def build(self, src2tgt: dict = {}):
        keys = src2tgt.keys()
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                for key in keys:
                    if (key in line) or (key == line):
                        line = line.replace(key, src2tgt[key])

                # 分号分隔：第一部分是 path，其余是菜谱
                parts = [p.strip() for p in line.split(";") if p.strip()]
                if not parts:
                    continue

                if '手工包子' in parts[0]:
                    pass

                path_str = parts[0]
                recipes_raw = parts[1:]

                # 按 / 拆分层级
                segments = [s for s in path_str.split("/") if s]
                parent_id = "root"

                # 逐级创建节点
                for i, name in enumerate(segments):
                    is_leaf = (i == len(segments) - 1)
                    isMerge = name.endswith("T")
                    inputName = name.replace('T','')
                    node_id = self.ensure_node(parent_id, inputName, is_leaf, isMerge)
                    parent_id = node_id

                # 给叶子节点挂菜谱
                for r in recipes_raw:
                    food_list = self.parse_food_names(r)
                    main_recipe = None
                    for i in range(len(food_list)):
                        if i == 0:
                            main_recipe = self.parse_recipe(food_list[i])
                            if main_recipe:
                                node = self.tree.get_node(parent_id)
                                node.data["recipes"].append(main_recipe)
                        else:
                            sub_recipe = self.parse_recipe(food_list[i])
                            if sub_recipe and main_recipe:
                                node = self.tree.get_node(parent_id)
                                sub_recipe['main_recipe'] = main_recipe['name']
                                node.data['sub_recipes'].append(sub_recipe)
                        

    # ------------------ 工具：打印所有食材及其菜谱 ------------------
    def print_all_ingredients(self):
        logger.info("=== 所有食材及菜谱 ===")
        for node in self.tree.all_nodes():
            if node.data["kind"] == "ingredient":
                logger.info(f"{node.identifier}:")
                for r in node.data["recipes"]:
                    logger.info(f"  - {r['name']}[{r['code']}]")
                

    def generate_dirs(self, root_path: str, root_tag_folder: str = None):
        """
        在 root_path 下根据树结构生成文件夹层级，并打印每个创建的目录路径。
        对于食材节点(kind == "ingredient")，在其目录下按菜谱名称再创建子文件夹。
        目录结构示例：
            root_path/root_tag_folder/蔬菜/青菜/上海青/[Y]蒜蓉烤茄子
        """
        def sanitize_name(name: str) -> str:
            """
            简单清洗文件夹名称，避免 Windows 非法字符问题。
            """
            invalid_chars = r'\/:*?"<>|'
            for ch in invalid_chars:
                name = name.replace(ch, "_")
            return name.strip()

        if os.path.exists(root_path):
            # print(f"[DIR] 删除已存在的目录: {root_path}")
            # shutil.rmtree(root_path)
            logger.info('根目录已存在，不进行另外添加。')
        else:
            os.makedirs(root_path, exist_ok=False)

        # ① 确定根目录名称
        if root_tag_folder is None:
            root_tag_folder = self.tree.get_node("root").tag  # 默认 "食材识别"

        # ② 构建最终根路径
        final_root = os.path.join(root_path, sanitize_name(root_tag_folder))
        os.makedirs(final_root, exist_ok=True)
        logger.info(f"[DIR] {final_root}")

        # ③ 遍历整棵树创建目录
        for node in self.tree.all_nodes():
            if node.identifier == "root":
                continue
            if not node.data:
                continue

            # 从 root → 当前节点的路径
            path_nodes = list(self.tree.rsearch(node.identifier))[::-1]

            # 用 tag 构建目录层级（跳过 root）
            parts = []
            for nid in path_nodes:
                if nid == "root":
                    continue
                tag = self.tree.get_node(nid).tag
                parts.append(sanitize_name(tag))

            # 当前节点对应的目录
            dir_path = os.path.join(final_root, *parts)

            # 创建并打印
            if not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
                logger.info(f"[DIR] {dir_path}")

            # ④ 如果是食材节点，再为其菜谱创建子目录
            if node.data.get("kind") == "ingredient":
                recipes = node.data.get("recipes", [])
                for r in recipes:
                    recipe_name = r.get("name", "").strip()
                    code = r.get("code", "").strip()

                    # 组合成子目录名，例如 "[Y] 蒜蓉烤茄子"
                    if code and recipe_name:
                        sub_name = f"[{code}]{recipe_name}"
                    elif recipe_name:
                        sub_name = recipe_name
                    else:
                        continue

                    sub_dir = os.path.join(dir_path, sanitize_name(sub_name))
                    if not os.path.exists(sub_dir):
                        os.makedirs(sub_dir, exist_ok=True)
                        logger.info(f"[DIR][RECIPE] {sub_dir}")
                    
                food_dir = os.path.join(dir_path, '[Z]食材图')
                if not os.path.exists(food_dir):
                    os.makedirs(food_dir, exist_ok=True)
                    logger.info(f"[DIR][FOOD] {food_dir}")
            else:
                food_dir = os.path.join(dir_path, '[Z]食材图')
                if not os.path.exists(food_dir):
                    os.makedirs(food_dir, exist_ok=True)
                    logger.info(f"[DIR][FOOD] {food_dir}")


    def _collect_leaf_tags(self, start_id: str) -> set[str]:
        """
        从给定节点 id 开始，向下遍历，收集所有叶子节点的 tag。
        """
        if not start_id:
            return set()

        leaf_tags: set[str] = set()
        stack = [start_id]

        while stack:
            nid = stack.pop()
            node = self.tree.get_node(nid)
            if node is None:
                continue

            if node.is_leaf():
                leaf_tags.add(node.tag)
            else:
                # children 返回的是 Node 对象
                for child in self.tree.children(nid):
                    stack.append(child.identifier)

        return leaf_tags


    def get_detect_range(self):
        merge_node_tags = []
        for node in self.tree.all_nodes():
            if node.identifier == "root":
                continue
            if not node.data:
                continue
            if node.data.get("isMerge") is True:
                merge_node_tags.append(node.tag)

        logger.info(f"merge_node: {merge_node_tags}")

        leaf_set: set[str] = set()
        for tag in merge_node_tags:
            # 利用 self.tag2ids 映射：tag -> [id1, id2, ...]
            id_list = self.tag2ids.get(tag, None)
            if not id_list:
                continue
            leaf_set |= self._collect_leaf_tags(id_list)

        logger.info(f"merge_detect_range: {leaf_set}")

        total_dtct = []
        for item in self.tree.leaves():
            if item.tag not in leaf_set:
                total_dtct.append(item.tag)

        logger.info(f"detect_range: {total_dtct}")
        return total_dtct

                    
    def stats(self):
        """
        直接打印：
        - 节点数量（除 root）
        - 去重菜谱数量
        - 去重食材名数量
        """
        node_count = 0
        recipe_set = set()
        ingredient_name_set = set()
        ingre_has_recipe_set = set()
        sub_recipe_name = set()

        ANum = 0
        DNum = 0
        PNum = 0
        YNun = 0
        for node in self.tree.all_nodes():
            if node.identifier == "root":
                continue
            if not node.data:
                continue
            if node.data['level'] < 1: 
                continue
            
            recipes_list = node.data.get("recipes", [])
            sub_recipes_list = node.data.get("sub_recipes", [])
            if not recipes_list and node.is_leaf():
                ingre_has_recipe_set.add(node.tag)
            
            for r in sub_recipes_list:
                key = (r["name"]+r["code"])
                sub_recipe_name.add(key)
                if r["code"] == "A":
                    ANum += 1
                elif r["code"] == "D":
                    DNum += 1
                elif r["code"] == "P":
                    PNum += 1
                elif r["code"] == "Y":
                    YNun += 1


            for r in recipes_list:
                key = (r["name"]+r["code"])
                recipe_set.add(key)   
                if r["code"] == "A":
                    ANum += 1
                elif r["code"] == "D":
                    DNum += 1
                elif r["code"] == "P":
                    PNum += 1
                elif r["code"] == "Y":
                    YNun += 1
            
            node_count += 1
            ingredient_name_set.add(node.tag)

        # ---------- 打印输出 ----------
        # logger.info("================================")
        # logger.info(ingredient_name_set)
        # logger.info("================================")
        # logger.info(recipe_set)
        logger.info("========== 统计结果 ==========")
        logger.info(f"菜谱名的总和数量    : {len(recipe_set)}")
        logger.info(f"食材名的总和数量    : {len(ingredient_name_set)}")
        logger.info(f"叶子未添加菜谱数    : {len(ingre_has_recipe_set)}")
        logger.info(f"       A            : {ANum}")
        logger.info(f"       D            : {DNum}")
        logger.info(f"       P            : {PNum}")
        logger.info(f"       Y            : {YNun}")
        logger.info(f"子菜谱数量          : {len(sub_recipe_name)}")


# ------------------ 使用示例 ------------------
if __name__ == "__main__":
    from app.core.observability.logging import setup_logger
    setup_logger()

    builder = IngredientTreeBuilder("config/param/totalTreePam.txt")
    builder.stats()    
    detect_range = builder.get_detect_range()
    with open('detect_range.txt', 'w') as f:
        for item in detect_range:
            f.write(item + '\n')
    # builder.generate_dirs(r'E:\99-clip')
    # builder.stats()
    # builder.print_all_ingredients()
