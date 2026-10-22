import os

def get_leaf_dirs(root_path):
    leaf_dirs = []
    
    for current, subdirs, files in os.walk(root_path):
        # 没有子文件夹 → 是leaf目录
        if not subdirs:
            leaf_dirs.append(os.path.basename(current))
    
    return leaf_dirs


food_list = get_leaf_dirs(r'D:\00-dataset\一体机设备\9277图像')
foods = get_leaf_dirs(r'D:\00-dataset\一体机设备\928图像')

aaa = food_list + foods

aaa = set(aaa)

print(len(aaa))

print(aaa)