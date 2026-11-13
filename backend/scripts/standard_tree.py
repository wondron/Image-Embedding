   
from numpy import true_divide
from treelib import Tree 
import pickle
import unicodedata

def visual_width(s):
    """计算字符串在终端中的可视宽度"""
    width = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            width += 2  # 中文或全角字符
        else:
            width += 1  # 英文或半角字符
    return width


def pad_display(s, target_width):
    """将字符串填充至指定可视宽度"""
    pad_len = target_width - visual_width(s)
    return s + ' ' * pad_len


def create_pingbi_word():
    food_List = ['饺子皮', '蛋糕', '橙红色', '橙色', '魔芋豆腐', '魔芋烧鸭', '风扇', '电扇', 
                 '蛋白', '樱桃色', '形状像冬瓜', '米色', '蛋白质', '橘红色', '蒲扇', 
                 '玉米黄素', '橘色', '番茄酱', '辣椒酱', '柠檬汁', '中国菜肴', '银杏叶',
                 '蔬菜', '草莓熊', '中国菜', '蔬菜', '淡水鱼', '葫芦科', '南瓜属',
                 '酥脆', '酥麻', '淡水', '肉食性', '大蒜的', '炒鸡蛋等', '天门冬科', '天门冬属', '虾片', '鸡尾酒']
    food_List = sorted(food_List, key=len, reverse=True)
    return food_List

def create_name_change_word():
    transName = {}
    transName['鸡排'] = '半成品鸡排'
    transName['金鲳鱼'] = '金鲳'
    return transName

def get_food_card_tree():
    with open('./models/food_biecheng_tree.pkl', 'rb') as f:
        food_data = pickle.load(f)
    return food_data

def create_standard_tree():
    tree = Tree()
    
    tree.create_node('*食材相似度*', 'Food', data={'type': False})  # 根节点
    
    # 谷物
    tree.create_node('谷物',        'GuWuuu', parent='Food', data={'type': False})
    ## 米类
    tree.create_node('米类',        'MiLeii', parent='GuWuuu', data={'type': False, '细分' : True})
    tree.create_node('大米',        'DaMiii', parent='MiLeii', data={'别称': ['米粒', '米饭']})
    tree.create_node('糯米',        'NuoMii', parent='MiLeii')
    tree.create_node('糙米',        'CaoMii', parent='MiLeii')
    tree.create_node('小米',        'XiaoMi', parent='MiLeii')
    tree.create_node('黄米',        'HuangM', parent='MiLeii')
    tree.create_node('玉米糁',      'YuMiSn', parent='MiLeii')
    tree.create_node('血糯米',      'XueNMi', parent='MiLeii')
    
    tree.create_node('玉米',        'YuMiii', parent='GuWuuu')
    tree.create_node('甜玉米',      'TianYM', parent='YuMiii')
    tree.create_node('糯玉米',      'NuoYMi', parent='YuMiii')
    tree.create_node('核桃类',      'HeTaoL', parent='GuWuuu')
    tree.create_node('核桃',        'HeTaoo', parent='HeTaoL')
    tree.create_node('山核桃',      'ShanHT', parent='HeTaoL')
    tree.create_node('面粉',        'MianFn', parent='GuWuuu')
    tree.create_node('小麦淀粉',    'XiaMDF', parent='MianFn', data={'type': False})
    tree.create_node('小麦胚粉',    'XiaMPF', parent='MianFn', data={'type': False})
    tree.create_node('高筋面粉',    'GJMFen', parent='MianFn', data={'type': False})
    tree.create_node('低筋面粉',    'DJMFen', parent='MianFn', data={'type': False})
    tree.create_node('粉丝',        'FenSii', parent='GuWuuu', data={'别称': ['米线', '米粉', '粉条', '细粉']})
    tree.create_node('龙口粉丝',    'LongFS', parent='FenSii')
    tree.create_node('红薯粉丝',    'HngSFS', parent='FenSii')
    tree.create_node('山药粉丝',    'SYaoFS', parent='FenSii')
    tree.create_node('蕨根粉丝',    'JueGFS', parent='FenSii')
    tree.create_node('豆腐类',      'DouFuL', parent='GuWuuu')
    tree.create_node('豆腐',        'DouFuu', parent='DouFuL', data={'goto': 'DouFuL'})
    tree.create_node('内酯豆腐',    'NeiZDF', parent='DouFuL', data={'goto': 'DouFuL'})
    tree.create_node('老豆腐',      'LaoDFu', parent='DouFuL', data={'goto': 'DouFuL'})
    tree.create_node('豆腐皮',      'DouFuP', parent='DouFuL', data={'别称': ['千张', '豆皮'], 'goto': 'DouFuL'})
    tree.create_node('板栗',        'BanLii', parent='GuWuuu', data={'别称': ['栗子']})
    
    # 薯类
    tree.create_node('薯类',        'ShuLei', parent='Food', data={'type': False})
    tree.create_node('芋头',        'YuuNai', parent='ShuLei', data={'别称': ['芋艿', '土苹果', '魔芋'], 'compare':['天麻']})
    tree.create_node('山药类',      'ShanYL', parent='ShuLei')
    tree.create_node('山药',        'ShanYo', parent='ShanYL', data={'compare':['天麻']})
    tree.create_node('铁棍山药',    'TGSYao', parent='ShanYL', data={'goto': 'ShanYL'})
    tree.create_node('淮山药',      'HuaiSY', parent='ShanYL', data={'goto': 'ShanYL'})
    tree.create_node('红薯',        'HongSu', parent='ShuLei', data={'别称': ['番薯', '地瓜']})
    tree.create_node('紫薯',        'ZiShuu', parent='ShuLei')
    tree.create_node('土豆',        'TuDouu', parent='ShuLei')
    tree.create_node('薯条',        'ShTiao', parent='ShuLei')
    
    # 面点
    tree.create_node('面点',        'MDiann', parent='Food', data={'type': False})
    tree.create_node('披萨',        'PiSaaa', parent='MDiann')
    tree.create_node('花卷',        'HhJuan', parent='MDiann', data={'别称': ['葱花卷', '葱花面团']})
    tree.create_node('蛋挞',        'DanTaa', parent='MDiann')
    tree.create_node('饺子',        'JiaoZi', parent='MDiann')
    tree.create_node('面条',        'MnTiao', parent='MDiann', data={'别称': ['意大利面']})
    tree.create_node('通心粉',      'TongXF', parent='MDiann')
    
    ## 包子
    tree.create_node('包子类',      'BaoZiL', parent='MDiann')
    tree.create_node('包子',        'BaoZii', parent='BaoZiL', data={'goto': 'BaoZiL'})
    tree.create_node('速冻包子',    'SDongB', parent='BaoZiL')
    tree.create_node('黑米包',      'HeiMiB', parent='BaoZiL')
    tree.create_node('速冻汤包',    'TangBo', parent='BaoZiL')
    tree.create_node('速冻小笼包',  'XiaoLB', parent='BaoZiL')
    
    ## 馒头
    tree.create_node('馒头类',      'ManTou', parent='MDiann')
    tree.create_node('白馒头',      'BaiMTo', parent='ManTou', data={'别称': ['面团', '馒头'], 'goto': 'ManTou'})
    tree.create_node('红糖馒头',    'HongMT', parent='ManTou')
    tree.create_node('荞麦馒头',    'QiaoMM', parent='ManTou')
    tree.create_node('紫薯馒头',    'ZShuMT', parent='ManTou')
    tree.create_node('黑米馒头',    'HeiMMT', parent='ManTou')
    tree.create_node('黄馒头',      'HuagMT', parent='ManTou')
    
    tree.create_node('面包',        'MainBo', parent='MDiann')
    tree.create_node('蛋糕',        'DanGoo', parent='MDiann')
    tree.create_node('吐司片',      'TuSiPi', parent='MDiann', data={'别称': ['吐司']})
    tree.create_node('油条',        'YoTiao', parent='MDiann')
    tree.create_node('蛋黄酥',      'DanHSu', parent='MDiann')
    tree.create_node('烧麦',        'ShaoMi', parent='MDiann', data={'别称': ['烧卖']})
    tree.create_node('糯米烧麦',    'NuoMSM', parent='ShaoMi')
    tree.create_node('纸皮烧麦',    'ZhiPSM', parent='ShaoMi')
    tree.create_node('黄皮烧麦',    'HPSMai', parent='ShaoMi')
    tree.create_node('粽子',        'ZongZi', parent='MDiann', data={'别称': ['肉粽', '糯米肉粽']})
    tree.create_node('年糕',        'NianGo', parent='MDiann')
    
    # 预制菜
    tree.create_node('预制菜',      'YuZhiC', parent='Food', data={'type': False})
    tree.create_node('烤肠',        'KChang', parent='YuZhiC', data={'别称': ['香肠', '淀粉肠']})
    tree.create_node('腊肠',        'LChang', parent='YuZhiC')
    tree.create_node('鸡排',        'JiiPai', parent='YuZhiC', data={'别称': ['炸鸡', '炸鸡排']})
    tree.create_node('芝士热狗棒',  'XSRGBg', parent='YuZhiC', data={'别称': ['玉米热狗', '热狗棒']})
    tree.create_node('藕盒',        'ouHeee', parent='YuZhiC')
    tree.create_node('原味鸡块',    'YWJiKi', parent='YuZhiC', data={'别称': ['鸡块']})
    tree.create_node('小酥肉',      'XiaoSR', parent='YuZhiC', data={'别称': ['酥肉', '炸酥肉']})
    tree.create_node('鸡米花',      'JMHuaa', parent='YuZhiC')
    tree.create_node('蟹柳',        'XieLiu', parent='YuZhiC', data={'别称': ['蟹棒']})
    
    # 水产
    tree.create_node('水产',        'ShChan', parent='Food', data={'type': False})
    tree.create_node('沙虫',        'ShaChg', parent='ShChan')
    tree.create_node('鱼头',        'YuTouu', parent='ShChan')
    tree.create_node('鲢鳙鱼头',    'LYYTou', parent='YuTouu')
    tree.create_node('草鱼鱼头',    'CYYTou', parent='YuTouu')
    
    tree.create_node('鱼',          'Yuuuuu', parent='ShChan', data={'别称': ['鱼类', '魚類', '魚', '炸鱼', '生鱼肉', '鱼肉', '生鱼', '烤鱼', '鱼肉块'], 'review': 'fish', 'clip_val': 19})
    tree.create_node('鮟鱇鱼',      'AnKngY', parent='Yuuuuu', data={'别称': ['蛤蟆鱼', '琵琶鱼', '老头鱼', '丑婆鱼', '海鬼鱼', '灯笼鱼', '鮟鱇鱼'],'review': 'fish', 'clip_val': 19})
    tree.create_node('巴浪鱼',      'BaLang', parent='Yuuuuu', data={'别称': ['刺巴鱼', '边鱼', '蓝圆鲹', '鲹鱼'],'review': 'fish', 'clip_val': 19})
    tree.create_node('鲅鱼',        'BaYiuu', parent='Yuuuuu', data={'review': 'fish', 'clip_val': 19})
    tree.create_node('背点棘赤刀鱼', 'HDaiYu', parent='Yuuuuu', data={'别称': ['红带鱼', '红娘鱼'], 'review': 'fish', 'clip_val': 19})
    tree.create_node('鳊鱼',        'BianYu', parent='Yuuuuu', data={'别称': ['扁鱼', '武昌鱼'], 'review': 'fish', 'clip_val': 19})
    tree.create_node('草鱼',        'CaoYuu', parent='Yuuuuu', data={'review': 'fish', 'clip_val': 19})
    tree.create_node('带鱼',        'DaiYuu', parent='Yuuuuu', data={'别称': ['刀鱼'], 'review': 'fish', 'clip_val': 18})
    tree.create_node('鳜鱼',        'GuiYuu', parent='Yuuuuu', data={'别称': ['桂花鱼', '季花鱼'], 'review': 'fish', 'clip_val': 18})
    tree.create_node('黑鱼',        'HeiYuu', parent='Yuuuuu', data={'review': 'fish', 'clip_val': 19})
    tree.create_node('黄辣丁',      'HugGYu', parent='Yuuuuu', data={'别称': ['黄骨鱼', '黄颡鱼', '黄姑子', '黄鲶鱼', '黄桑古', '黄鸭鱼', '黄角丁', '黄刺骨', '昂刺鱼', '昂公鱼', '嘎鱼', '咯鱼'], 'review': 'fish', 'clip_val': 16})
    tree.create_node('鲫鱼',        'JiYuuu', parent='Yuuuuu', data={'review': 'fish', 'clip_val': 19})
    tree.create_node('蓝鳃太阳鱼',  'LSTYYu', parent='Yuuuuu', data={'别称':{'太阳鱼'}, 'review': 'fish', 'clip_val': 19})
    tree.create_node('鲤鱼',        'LiYuuu', parent='Yuuuuu', data={'别称': ['锦鲤'], 'review': 'fish', 'clip_val': 19})
    
    ####---------------------############
    tree.create_node('鲢鳙',        'LianYg', parent='Yuuuuu')
    tree.create_node('鲢鱼',        'LianYu', parent='Yuuuuu', data={'别称': ['白鲢'], 'review': 'fish', 'clip_val': 19})
    tree.create_node('鳙鱼',        'YongYu', parent='Yuuuuu', data={'别称': ['胖头鱼', '花鲢'], 'review': 'fish', 'clip_val': 19})
    tree.create_node('鲮鱼',        'LingYu', parent='Yuuuuu', data={'别称': ['鲮'],'review': 'fish'})
    tree.create_node('龙头鱼',      'LgTouY', parent='Yuuuuu', data={'别称': ['虾潺', '水潺', '狗母鱼', '九肚鱼'],'review': 'fish'})
    tree.create_node('鲈鱼',        'LuYuuu', parent='Yuuuuu', data={'别称': ['花鲈'], 'review': 'fish', 'fushaiNode': "鱼"})
    tree.create_node('海鲈鱼',      'HLuYuu', parent='LuYuuu', data= {'review': 'fish', 'fushaiNode': "鱼"})
    tree.create_node('罗非鱼',      'LFeiYu', parent='Yuuuuu', data={'别称': ['非洲鲫鱼', '福寿鱼'],'review': 'fish', 'clip_val': 18})
    tree.create_node('青衣鱼',      'QinYiY', parent='Yuuuuu', data= {'review': 'fish'})
    tree.create_node('鲭鱼',        'QinYUu', parent='Yuuuuu', data={'别称': ['青花鱼', '鲐鱼', '鲐鲅鱼'],'review': 'fish', 'clip_val': 18})
    tree.create_node('秋刀鱼',      'QiuDYu', parent='Yuuuuu', data={'别称': ['竹刀鱼'],'review': 'fish', 'clip_val': 18})
    tree.create_node('三文鱼',      'SanWYu', parent='Yuuuuu', data={'别称': ['樱桃肉'],'review': 'fish', 'clip_val': 18})
    tree.create_node('沙尖鱼',      'SaJnYu', parent='Yuuuuu', data={'别称': ['沙鲮','沙钻','多鳞鱚'],'review': 'fish', 'clip_val': 18})
    tree.create_node('四指马鲅',    'SiZhMB', parent='Yuuuuu', data={'别称': ['午鱼','马友鱼','午仔鱼'],'review': 'fish', 'clip_val': 18})
    tree.create_node('鲟鱼',        'XunYuu', parent='Yuuuuu', data={'别称': ['鳇鱼' , '腊子'],'review': 'fish', 'clip_val': 18})
    tree.create_node('胭脂鱼',      'YanZYu', parent='Yuuuuu', data={'别称': ['黄排', '血排', '粉排'],'review': 'fish', 'clip_val': 18})
    tree.create_node('银鱼',        'YingYu', parent='Yuuuuu', data={'别称': ['银条鱼', '面条鱼'],'review': 'fish', 'clip_val': 18})
    
    ####未细分
    tree.create_node('海狼',        'HaiLng', parent='Yuuuuu', data={'别称': ['海狼鱼', '梭子鱼', '魣', '黄鲶鱼', '梭子魣', '牛乞'], 'review': 'fish'})
    tree.create_node('姑鱼',        'GuuYuu', parent='Yuuuuu', data={'别称': ['小白鱼', '赤头', '黑耳津'], 'review': 'fish'})
    tree.create_node('白条鱼',      'BaiTio', parent='Yuuuuu', data={'review': 'fish'})
    tree.create_node('多春鱼',      'DuoCYu', parent='Yuuuuu', data={'别称': ['毛鳞鱼', '胡瓜鱼'], 'review': 'fish'})
    tree.create_node('鲑鱼',        'GuiiYu', parent='Yuuuuu', data={'review': 'fish'})
    tree.create_node('鲇鱼',        'NianYu', parent='Yuuuuu', data={'别称':['塘鲺', '胡子鲶'], 'review': 'fish'})
    tree.create_node('鲶鱼',        'NinYuu', parent='Yuuuuu', data={'别称': ['鲶'], 'review': 'fish'})
    tree.create_node('沙丁鱼',      'ShaDYu', parent='Yuuuuu', data={'review': 'fish'})
    tree.create_node('鲻鱼',        'ZaiiYu', parent='Yuuuuu', data={'review': 'fish'})
    tree.create_node('青鱼',        'QgggYu', parent='Yuuuuu', data={'别称': ['黑鲩', '螺蛳青'], 'review': 'fish', 'fushaiNode': "鱼"})
           
    tree.create_node('小黄鱼',      'XHYuuu', parent='Yuuuuu', data={'别称': ['黄花鱼', '黄鱼', '黄姑鱼'], 'fushaiNode': "鱼", 'review': 'changMn', 'clip_val': 19, 'single': True})
    tree.create_node('大黄鱼',      'DHYuuu', parent='XHYuuu', data={'fushaiNode':"鱼", 'review': 'fish'})
    tree.create_node('黄鱼鲞',      'HYXiag', parent='XHYuuu')
    
    tree.create_node('鲳鱼',        'ChangY', parent='Yuuuuu', data={'review': 'fish', 'clip_val': 19, 'fushaiNode':"鱼"})
    tree.create_node('银鲳鱼',      'BChang', parent='ChangY', data={'别称': ['银鲳'], 'fushaiNode':"鱼", 'review': 'fish'})
    tree.create_node('金鲳',        'JChang', parent='ChangY', data={'别称': ['金鲳鱼'], 'fushaiNode':"鱼", 'review': 'fish'})
    tree.create_node('刀鲳鱼',      'DChanY', parent='ChangY')
    
    tree.create_node('石斑鱼',      'ShiBan', parent='Yuuuuu', data={'review': 'shiban', '别称': ['石斑', '红石斑', '黑石斑', '红石斑鱼'], 'clip_val': 19, 'fushaiNode': "鱼"})
    tree.create_node('褐菖鲉',      'HChYou', parent='ShiBan', data={'别称': ['石狗公', '石头鱼', '虎头鱼', '石狗公鱼'], 'fushaiNode': "鱼", 'review': "shiban"})
    tree.create_node('龙胆石斑',    'LDSBYu', parent='ShiBan', data={'别称': ['龙胆石斑鱼']})
    tree.create_node('网纹石斑鱼',   'WWSBYu', parent='ShiBan', data={'别称': ['网纹石斑', '花点石斑鱼', '花点斑']})
    tree.create_node('东星斑',      'DongXB', parent='ShiBan', data={'fushaiNode': "鱼", 'review': 'shiban'})
    tree.create_node('老鼠斑',      'LShuBn', parent='ShiBan')
    tree.create_node('点斑',        'DianBn', parent='ShiBan', data={'别称': ['点石斑鱼', '带点石斑']})
    
    #鲷鱼
    tree.create_node('鲷鱼',        'DiaoYu', parent='Yuuuuu', data={'review': 'diaoyu', '别称': ['鲷'], 'clip_val': 19, 'fushaiNode': "鱼"})
    tree.create_node('黑鲷鱼',      'HeiDYu', parent='DiaoYu', data={'别称': ['黑鲷', '黑腊'], 'review': 'diaoyu', 'fushaiNode': "鱼"})
    tree.create_node('黄鳍鲷',    'HQJDYu', parent='DiaoYu', data={'别称': ['黄脚立', '黄脚腊', '黄墙', '黄鳍鲷'], 'review': 'diaoyu'})
    tree.create_node('黄线笛鲷',    'HXDDYu', parent='DiaoYu', data={'review': 'diaoyu'})
    tree.create_node('金焰笛鲷',    'JYDDYu', parent='DiaoYu', data={'review': 'diaoyu'})
    tree.create_node('平鲷',        'PinDYu', parent='DiaoYu', data={'别称': ['黄丝立', '丝立'], 'review': 'diaoyu'})
    tree.create_node('真鲷鱼',      'ZhenDY', parent='DiaoYu', data={'别称': ['赤棕', '赤鯮', '加腊', '红腊', '加吉鱼', '真鲷鱼'], 'review': 'diaoyu'})
    tree.create_node('黄背牙鲷',    'HBYDYu', parent='ZhenDY', data={'别称': ['黄牙鲷', '金波丽鱼', '金嘴'], 'review': 'diaoyu'})
    tree.create_node('二长棘鲷鱼',  'ECJDYu', parent='ZhenDY', data={'别称': ['盘仔'], 'review': 'diaoyu'})
    tree.create_node('大眼鲷',      'DaYanD', parent='DiaoYu', data={'别称': ['大眼鸡', '红木鲢', '金目鯛', '金眼鲷'], 'review': 'diaoyu'})
    
    #比目鱼
    tree.create_node('比目鱼',      'BiMuYu', parent='Yuuuuu', data={'别称': ['剥皮鱼', '左口', '牙偏', '偏口'], 'fushaiNode': "鱼", 'review': 'bimuyu', 'clip_val': 15})
    tree.create_node('多宝鱼',      'DBaoYu', parent='BiMuYu', data={'别称': ['大菱鲆', '蝴蝶鱼', '巨菱鲆'], 'review': 'fish', 'fushaiNode': "鱼"})
    tree.create_node('鲽鱼',        'DieYuu', parent='BiMuYu', data={'别称': ['鸦片鱼', '欧洲鲽', '鲽鱼'], 'review': 'fish', 'fushaiNode': "鱼"})
    tree.create_node('舌鳎鱼',      'SheTYu', parent='BiMuYu', data={'review': 'bimuyu', 'clip_val': 19, '别称': ['目鱼', '鳎米', '鳎目鱼'], 'review': 'fish', 'fushaiNode': "鱼"})
    tree.create_node('龙利鱼',      'LongLY', parent='SheTYu', data={'别称': ['半滑舌鳎', '鰙鳎', '鳎目', '鳎板'], 'review': 'fish', 'fushaiNode': "鱼"})
    tree.create_node('带纹条鳎',    'DWenTT', parent='SheTYu', data={'别称': ['条鳎', '花鰙鳎']})
    
    #马面鲀
    tree.create_node('马面鲀',      'MMianT', parent='Yuuuuu', data={'别称': ['扒皮鱼', '耗儿鱼', '扒皮狼', '鲀', '马面鱼'], 'review': 'bimuyu', 'clip_val': 15, 'fushaiNode': "鱼"})
    tree.create_node('绿鳍马面鲀',  'LQiMMT', parent='MMianT', data={'别称': ['剥皮鱼', '迪仔鱼']})
    tree.create_node('中华单角鲀',  'ZHHDJT', parent='MMianT', data={'别称': ['白达仔']})
    tree.create_node('拟态革鲀',    'NTaiGT', parent='MMianT', data={'别称': ['扫把鱼', '长尾革单棘鲀']})
    
    #鳗鱼
    tree.create_node('鳗鱼',        'ManYuu', parent='Yuuuuu', data={'别称': ['白鳝', '蛇鱼', '风鳗', '鳗', '日本鳗鲡', '海鳝', '鳝'], 'fushaiNode': "鱼", 'review': 'changMn', 'clip_val': 15, 'single': True})
    tree.create_node('石鳗',        'SManYu', parent='ManYuu', data={'别称': ['石蛇', '花鳗', '沙鳗']})
    tree.create_node('白鳗鱼',      'BaiMan', parent='ManYuu', data={'别称': ['白鳗鱼']})
    tree.create_node('金钱鳗',      'JQManY', parent='ManYuu', data={'别称': ['芦鳗']})
    tree.create_node('泥鳅',        'NiQiuu', parent='ManYuu', data={'别称': ['鱼鳅', '鳅鱼'], 'review': 'changMn', 'fushaiNode': "鱼"})
    tree.create_node('黄鳝',        'HuangS', parent='ManYuu', data={'别称': ['鳝鱼', '蛇鱼', '罗鳝'], 'review': 'changMn', 'fushaiNode': "鱼"})
    
    # tree.create_node('乌贼',        'WuZeei', parent='Yuuuuu', data={'review': 'squid'})
    tree.create_node('鱿鱼',        'YouYuu', parent='Yuuuuu', data={'别称': ['枪乌贼', '柔鱼'], 'review': 'squid', 'fushaiNode': "鱼"})
    tree.create_node('小鱿鱼',      'XYouYu', parent='YouYuu', data={'别称': ['小管'], 'review': 'squid', 'fushaiNode': "鱼"})
    tree.create_node('大鱿鱼',      'DYouYu', parent='YouYuu', data={'别称': ['大管'], 'review': 'squid', 'fushaiNode': "鱼"})
    tree.create_node('墨鱼',        'MooYuu', parent='Yuuuuu', data={'别称': ['墨鱼', '花枝', '墨斗鱼', '乌贼'], 'fushaiNode': "鱼", 'review': 'squid'})
    tree.create_node('章鱼',        'ZhgYuu', parent='Yuuuuu', data={'别称': ['真蛸', '短蛸', '望潮', '蛸', '八爪鱼'], 'fushaiNode': "鱼", 'review': 'squid'})
    
    #虾
    tree.create_node('虾仁',        'XiaRen', parent='ShChan', data={'别称': ['虾丸','虾滑', '虾球']})
    tree.create_node('基围虾',      'JiWeiX', parent='ShChan', data={'别称': ['基围虾', '基围虾仁']})
    tree.create_node('虾米',        'XiaPii', parent='ShChan', data={'别称': ['虾皮', '虾干']})
    tree.create_node('河虾',        'HeXiaa', parent='ShChan')
    tree.create_node('对虾',        'DuiXia', parent='ShChan', data={'别称': ['大对虾', '对虾仁']})
    
    tree.create_node('虾',          'Xiaaaa', parent='ShChan', data={'别称': ['虾肉', '大虾'], 'review': 'shrimp', 'clip_val': 21})
    tree.create_node('斑节对虾',    'BJieDX', parent='Xiaaaa', data={'别称': ['斑节虾'], 'review': 'shrimp'})
    tree.create_node('黑虎虾',      'HeiHXa', parent='Xiaaaa', data={'review': 'shrimp'})
    tree.create_node('樱花虾',      'YHuaXa', parent='Xiaaaa', data={'review': 'shrimp'})
    tree.create_node('明虾',        'MinXia', parent='Xiaaaa', data={'review': 'shrimp'})
    tree.create_node('青虾',        'QngXia', parent='Xiaaaa', data={'review': 'shrimp'})
    tree.create_node('小龙虾',      'XLXiaa', parent='Xiaaaa', data={'review': 'shrimp'})
    tree.create_node('皮皮虾',      'PiPiXa', parent='Xiaaaa', data={'别称': ['虾姑', '螳螂虾'], 'review': 'shrimp'})
    tree.create_node('龙虾',        'LongXa', parent='Xiaaaa', data={'review': 'shrimp'})
    
    #螃蟹
    tree.create_node('螃蟹',        'Xieeee', parent='ShChan', data={'别称': ['蟹'], 'review': 'crab'})
    tree.create_node('大闸蟹',      'DaZhaX', parent='Xieeee', data={'别称': ['河蟹', '毛蟹'], 'review': 'crab'})
    tree.create_node('招潮蟹',      'ZhaoCX', parent='Xieeee', data={'别称': ['挥手蟹', '红螯', '滩涂蟹'], 'review': 'crab'})
    tree.create_node('帝王蟹',      'DiWXie', parent='Xieeee', data={'别称': ['皇帝蟹', '大脚蟹', '俄罗斯蟹'], 'review': 'crab'})
    tree.create_node('梭子蟹',      'SuoZiX', parent='Xieeee', data={'别称': ['白蟹', '枪蟹'], 'review': 'crab', 'fushaiNode': "螃蟹"})
    tree.create_node('红星梭子蟹',  'SYnXie', parent='SuoZiX', data={'别称': ['三眼蟹', '三点蟹', '三目公仔', '三疣梭子蟹'], 'review': 'crab'})
    tree.create_node('拥剑梭子蟹',  'YJSZXe', parent='SuoZiX', data={'别称': ['肉蟹', '扁蟹'], 'review': 'crab'})
    tree.create_node('蓝蟹',        'LanXie', parent='SuoZiX', data={'review': 'crab', 'review': 'crab', 'fushaiNode': "螃蟹"})
    tree.create_node('花蟹',        'HuaXie', parent='SuoZiX', data={'review': 'crab', 'review': 'crab', 'fushaiNode': "螃蟹"})
    tree.create_node('远海梭子蟹',  'LHaXie', parent='HuaXie', data={'别称': ['兰花蟹', '蓝花蟹', '外海蟹', '远洋梭子蟹'], 'review': 'crab'})
    tree.create_node('锈斑蟳',      'HHaXie', parent='HuaXie', data={'别称': ['红花蟹', '飞机蟹', '火烧公'], 'review': 'crab'})
    tree.create_node('老虎蟹',      'LHuXie', parent='Xieeee', data={'review': 'crab'})
    tree.create_node('面包蟹',      'MBoXie', parent='Xieeee', data={'review': 'crab'})
    tree.create_node('松叶蟹',      'SongYX', parent='Xieeee', data={'review': 'crab'})
    tree.create_node('青蟹',        'QingXe', parent='Xieeee', data={'别称': ['蝤蛑', '黄甲蟹', '拨棹子', '泥蟹', '海蟳'], 'review': 'crab'})
    
    tree.create_node('贝壳类',      'BeiKeL', parent='ShChan')
    tree.create_node('鲍鱼',        'BaoYuu', parent='BeiKeL')
    tree.create_node('生蚝',        'ShenHo', parent='BeiKeL', data={'别称': ['蚝', '牡蛎']})
    tree.create_node('蛤蜊',        'GeeLii', parent='BeiKeL', data={'别称': ['花蛤', '文蛤', '白蛤', '血蛤', '黄蚬子']})
    tree.create_node('扇贝',        'ShanBe', parent='BeiKeL')
    tree.create_node('蛏子',        'ChenZi', parent='BeiKeL')
    tree.create_node('口贝',        'QingKo', parent='BeiKeL', data={'别称': ['青口', '口贝', '贻贝', '海虹', '青口贝']})
    tree.create_node('青口贝',      'QKouBe', parent='QingKo')
    tree.create_node('黑口贝',      'HKouBe', parent='QingKo')
    tree.create_node('藤壶',        'EJTgHu', parent='BeiKeL', data={'别称': ['鹅颈藤壶']})
    
    tree.create_node('海参类',      'HShenL', parent='ShChan')
    tree.create_node('海参',        'haishg', parent='HShenL')
    tree.create_node('海参干',      'hsheng', parent='HShenL')
    tree.create_node('即食海参',    'JSHHSH', parent='HShenL')
    
    # 蔬菜
    tree.create_node('蔬菜',        'ShuCai', parent='Food', data={'type': False})
    ## 茶叶
    tree.create_node('茶叶类',      'ChaYee', parent='ShuCai', data={'别称': ['茶树', '茶', '薄荷茶', '艾草茶', '菊花叶茶']})
    tree.create_node("绿茶",        'LvChaa', parent='ChaYee', data={'别称': ['茶叶（绿茶）', '茶树', '茶叶'], 'compare': ['绿茶']})
    tree.create_node("龙井茶",    'MQLogJ', parent='ChaYee', data={'别称': ['龙井', '绿茶鲜叶'], 'compare': ['绿茶']})
    tree.create_node("铁观音",      'TGuanY', parent='ChaYee', data={'别称': ['铁观音'], 'compare': ['绿茶','马兰头']})
    tree.create_node("碧螺春",      'BoLuoC', parent='ChaYee', data={'别称': ['碧螺', '干艾草', '干制野菜', '干制茵陈', '碧螺春型花茶', '茉莉银毫类', '干制香草', '碧潭飘雪类花茶', '草青'], 'compare': ['绿茶', '马兰头']})
    tree.create_node("红茶",        'HogCha', parent='ChaYee', data={'别称': ['茶叶（红茶）']})
    tree.create_node("乌龙茶",      'WuLCha', parent='ChaYee', data={'别称': ['茶叶（乌龙茶）']})
    tree.create_node("普洱茶",      'PuErCh', parent='ChaYee', data={'别称': ['茶叶（普洱茶）', '普洱']})
    tree.create_node("白茶",        'BaiCha', parent='ChaYee', data={'别称': ['茶叶（白茶）']})
    
    ## 果菜类
    tree.create_node('果菜类',      'GCaiLi', parent='ShuCai', data={'type': False})
    tree.create_node('辣椒',        'Lajiao', parent='GCaiLi', data={'别称': ['红辣椒']})
    tree.create_node('小米辣',      'XMiLaa', parent='Lajiao', data={'别称': ['红色的小尖椒']})
    tree.create_node('干红辣椒',    'GanHLJ', parent='Lajiao', data={'别称': ['干辣椒', '辣椒碎']})
    tree.create_node('菜椒',        'CaiJio', parent='Lajiao', data={'别称': ['彩椒', '黄椒', '红菜椒', '灯笼椒', '甜椒'], 'compare': ['姜柄瓜']})
    tree.create_node('尖椒',        'HuaJio', parent='Lajiao', data={'别称': ['青椒', '红椒']})
    tree.create_node('线椒',        'XianJo', parent='Lajiao')
    
    tree.create_node('西红柿',      'XiHShi', parent='GCaiLi', data={'别称': ['番茄']})
    tree.create_node('圣女果',      'SNvGuo', parent='GCaiLi', data={'别称': ['樱桃番茄', '小番茄'], 'compare': ['小番茄']})
    tree.create_node('树番茄',      'ShuFQe', parent='GCaiLi', data={'别称': ['木本番茄', '木番茄', '洋酸茄', '酸汤果', '神秘果', '鸡蛋果'], 'compare': ['小番茄'], 'tiaoguo_compare': True})
    tree.create_node('黄皮果',      'HuangP', parent='GCaiLi', data={'别称': ['黄弹','黄皮','黄枇','黄弹子','王坛子'], 'compare': ['小番茄']})
    tree.create_node('人参果',      'RenSGo', parent='GCaiLi', data={'别称': ['香瓜茄'], 'compare': ['小番茄']})
    
    ## 花菜类
    tree.create_node('花菜类',      'HCaiLi', parent='ShuCai', data={'type': False})
    tree.create_node('西兰花',      'XLHuaa', parent='HCaiLi')
    tree.create_node('花菜',        'HuaCai', parent='HCaiLi', data={'别称': ['菜花', '花椰菜']})
    
    ## 豆类
    tree.create_node('豆类',        'DouLei', parent='ShuCai', data={'type': False})

    # 豆粒类 / 豆子类
    tree.create_node('豌豆',        'WanDou', parent='DouLei', data={'别称': ['青豆', '青豌豆', '豌豆粒']})
    tree.create_node('蚕豆',        'CanDou', parent='DouLei')
    tree.create_node('红豆',        'HogDou', parent='DouLei')
    tree.create_node('绿豆',        'LvvDou', parent='DouLei')
    tree.create_node('毛豆',        'MaoDou', parent='DouLei')
    tree.create_node('黄豆',        'HugDou', parent='DouLei', data={'别称': ['大豆']})

    # 豆角类 / 豆荚类蔬菜
    tree.create_node('豆角',        'DouJio', parent='DouLei', data={'别称': ['豆角类', '油豆角']})
    tree.create_node('豇豆',        'JigDou', parent='DouJio', data={'别称': ['长豆角', '长豇豆']})
    tree.create_node('四季豆',      'SiJiDu', parent='DouJio', data={'别称': ['菜豆', '芸豆']})
    tree.create_node('刀豆',        'DaoDou', parent='DouJio')
    tree.create_node('扁豆',        'BinDou', parent='DouJio', data={'别称': ['眉豆']})
    tree.create_node('荷兰豆',      'HLnDou', parent='DouJio', data={'别称': ['甜豆', '软荚豌豆']})
    
    ## 菜叶类
    tree.create_node('菜叶类',      'CaiYLi', parent='ShuCai', data={'type': False})
    tree.create_node('绿叶蔬菜',    'LYShuC', parent='CaiYLi', data={'别称': ['绿叶菜', '叶菜'],'type': False})
    tree.create_node('香菜',        'XiangC', parent='LYShuC', data={'别称': ['芫荽', '香菜', '胡荽', '胡菜']})
    tree.create_node('油麦菜',      'YouMCi', parent='LYShuC')
    tree.create_node('菠菜',        'BoCaii', parent='LYShuC', data={'别称': ['山菠菜'], 'compare': ['马兰头']})
    tree.create_node('芥蓝',        'GaiLan', parent='LYShuC')
    tree.create_node('生菜',        'ShengC', parent='LYShuC')
    tree.create_node('空心菜',      'KongXC', parent='LYShuC', data={'别称': ['蕹菜'], 'compare': ['马兰头']})
    tree.create_node('苤蓝',        'PianLa', parent='LYShuC', data={'别称': ['球茎甘蓝', '菜蓝']})
    tree.create_node('圆白菜',      'JXinCi', parent='LYShuC', data={'别称': ['包菜', '卷心菜', '包心菜', "结球甘蓝", "牛心甘蓝"]})
    tree.create_node('大白菜',      'DaBCai', parent='LYShuC', data={'别称': ['白菜']})
    tree.create_node('娃娃菜',      'WaWaCi', parent='LYShuC')
    tree.create_node('香椿',        'XigChn', parent='LYShuC')
    tree.create_node('紫背天葵',    'ZiBeTk', parent='LYShuC')

    tree.create_node('蒲公英',      'PuGnYg', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('荠菜',        'JiCaii', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('碎米荠',      'SuiMQi', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('独行菜',      'DuXCai', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('马兰头',      'MaLnTo', parent='LYShuC', data={'别称': ['马兰','益母草', '芹菜叶',  "野苋菜", '西洋菜', '豆瓣菜', '佩兰','荆芥', '草头', '野菜', '山苜楂', '蒿', '泥鳅菜', '泥鳅串', '田边菊'], 'compare': ['马兰头']})
    tree.create_node('米瓦罐',      'MiWaGn', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('面条菜',      'MnToCi', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('芝麻菜',      'ZhMaCi', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('火箭菜',      'HoJnCi', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('菊花脑',      'JuHuaN', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('艾蒿',        'AiiHao', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('车前草',      'CheQnC', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('革命菜',      'GeMngC', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('鱼腥草',      'YuXgCo', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('扫帚苗',      'SaoZoM', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('冰菜',        'BingCi', parent='LYShuC', data={'别称': ['水晶冰菜', '冰草'],'compare': ['马兰头']})
    tree.create_node('马齿苋',      'MaChiW', parent='LYShuC', data={'compare': ['马兰头']})
    tree.create_node('芽苗菜',      'YaMCai', parent='LYShuC', data={'compare': ['马兰头']})

    tree.create_node('海带',        'HaiDai', parent='CaiYLi')
    tree.create_node('青菜类',      'QingCi', parent='CaiYLi')
    tree.create_node('上海青',      'ShHQng', parent='QingCi', data={'别称': ['小白菜']})
    tree.create_node('鸡毛菜',      'JiMCai', parent='QingCi', data={'compare': ['马兰头']})
    tree.create_node('青菜',        'QinCai', parent='QingCi')
    tree.create_node('菜心',        'CaiXin', parent='QingCi')
    tree.create_node('板蓝根青菜',  'BLGQCi', parent='QingCi')
    tree.create_node('枸杞叶',      'GouQiY', parent='CaiYLi', data={'compare': ['马兰头']})
    tree.create_node('穿心莲',      'ChXinL', parent='CaiYLi')
    tree.create_node('红薯叶',      'HongSY', parent='CaiYLi')
    tree.create_node('迷迭香',      'MiDieX', parent='CaiYLi')
    
    ## 条状菜
    tree.create_node('条状菜',      'TiaoZC', parent='ShuCai', data={'type': False})
    tree.create_node('韭菜',        'JiuCai', parent='TiaoZC')
    tree.create_node('韭菜苔',      'JiuCiT', parent='TiaoZC')
    tree.create_node('大葱',        'DaCong', parent='TiaoZC', data={'别称': ['葱', '葱白']})
    tree.create_node('小葱',        'xiCong', parent='TiaoZC', data={'别称': ['葱花','葱结']})
    tree.create_node('蒜苔',        'SuanTi', parent='TiaoZC', data={'别称': ['蒜薹']})
    tree.create_node('蒜黄',        'SuanHg', parent='TiaoZC')
    tree.create_node('韭黄',        'JiuHag', parent='TiaoZC')
    tree.create_node('蒜苗',        'SuanMo', parent='TiaoZC', data={'别称': ['青蒜']})
    tree.create_node('豆芽',        'DouYaa', parent='TiaoZC')
    tree.create_node('绿豆芽',      'LDouYa', parent='DouYaa', data={'别称': ['豆芽菜', '巧芽']})
    tree.create_node('黄豆芽',      'HDouYa', parent='DouYaa', data={'别称': ['青蒜', '大豆芽', '清水豆芽']})
    
    ## 茎状菜
    tree.create_node('茎状菜',      'JZgCai', parent='ShuCai', data={'type': False})
    tree.create_node('芹菜类',      'QinCiL', parent='JZgCai', data={'细分': True})
    tree.create_node('芹菜',        'QinCii', parent='QinCiL', data={'别称': ['欧芹']})
    tree.create_node('水芹菜',      'ShuiQC', parent='QinCiL', data={'别称': ['水芹'], 'compare': ['海菜花']})
    tree.create_node('西芹菜',      'XiQinC', parent='QinCiL', data={'别称': ['西芹']})
    tree.create_node('野菜芹菜',    'YCQCai', parent='QinCiL')
    tree.create_node('芦笋',        'LuSunn', parent='JZgCai', data={'别称': ['石刁柏']})
    tree.create_node('莴笋',        'WoSunn', parent='JZgCai')
    tree.create_node('笋',          'Sunnnn', parent='JZgCai', data={'别称': ['竹笋'], '细分': True, 'compare': ['毛竹笋']})
    tree.create_node('雷竹笋',       'LeiSun', parent='Sunnnn', data={'别称': ['雷竹', '雷笋'], 'compare': ['毛竹笋']})
    tree.create_node('毛竹笋',      'MaoSun', parent='Sunnnn', data={'别称': ['毛笋'], 'compare': ['毛竹笋']})
    tree.create_node('水竹笋',      'SZhuSn', parent='Sunnnn', data={'别称': ['甜杆'], 'compare': ['毛竹笋', '马兰头']})
    tree.create_node('冬笋',        'DogSun', parent='Sunnnn', data={'compare': ['毛竹笋']})
    tree.create_node('春笋',        'ChunSn', parent='Sunnnn', data={'compare': ['毛竹笋']})
    
    ## 柱状菜
    tree.create_node('柱状菜',      'ZhZCai', parent='ShuCai', data={'type': False})
    tree.create_node('西葫芦',      'XiHulu', parent='ZhZCai')
    tree.create_node('黄瓜类',      'HGuaLi', parent='ZhZCai', data={'细分': True})
    tree.create_node('黄瓜',        'HuangG', parent='HGuaLi')
    tree.create_node('水果黄瓜',     'SGHGua', parent='HGuaLi')
    tree.create_node('旱黄瓜',      'HHGuaa', parent='HGuaLi')
    tree.create_node('老黄瓜',      'LaoHGa', parent='HGuaLi')
    tree.create_node('苦瓜类',      'KuGuaL', parent='ZhZCai', data={'细分': True})
    tree.create_node('苦瓜',        'KuGuaa', parent='KuGuaL')
    tree.create_node('苹果苦瓜',     'SGKGua', parent='KuGuaL')
    tree.create_node('小苦瓜',      'XKuGua', parent='KuGuaL', data={'别称': ['小凉瓜','山苦瓜','苦子', '野苦瓜'], 'compare':['燕窝果']})
    tree.create_node('冬瓜类',      'DongGL', parent='ZhZCai', data={'细分': True})
    tree.create_node('冬瓜',        'DGuaaa', parent='DongGL')
    tree.create_node('水果冬瓜',     'SGDGua', parent='DongGL')
    tree.create_node('丝瓜',        'SGuaaa', parent='ZhZCai')
    tree.create_node('佛手瓜',      'FShouG', parent='ZhZCai')
    tree.create_node('茭白',        'JiaoBi', parent='ZhZCai', data={'compare': ['毛竹笋', '马兰头']})
    
    tree.create_node('秋葵',        'QiuKui', parent='ZhZCai')
    tree.create_node('花生',        'huashg', parent='ZhZCai', data={'别称': ['花生米', '油炸花生']})
    tree.create_node('茄子',        'QieZii', parent='ZhZCai', data={'别称': ['紫茄', '黑茄', '长茄']})
    tree.create_node('南瓜',        'NanGua', parent='ZhZCai', data={'细分': True})
    tree.create_node('贝贝南瓜',    'BBNanG', parent='NanGua', data={'compare': ['姜柄瓜']})
    tree.create_node('嫩南瓜',      'NengNG', parent='NanGua', data={'别称': ['小南瓜'], 'compare': ['姜柄瓜']})
    tree.create_node('老南瓜',      'LaoNGa', parent='NanGua')
    tree.create_node('磨盘南瓜',    'MoPNGa', parent='NanGua')
    tree.create_node('香芋南瓜',    'XYuNGa', parent='NanGua')
    tree.create_node('红栗南瓜',    'HLiNGa', parent='NanGua')
    
    ## 根状菜
    tree.create_node('根状菜',      'GnZCai', parent='ShuCai', data={'type': False})
    tree.create_node('荸荠',        'BiiQii', parent='GnZCai', data={'别称': ['马蹄', '地栗']})
    # 胡萝卜类
    tree.create_node('胡萝卜类',    'HuLoBo', parent='GnZCai', data={'细分': True})
    tree.create_node('胡萝卜',      'HuLBoo', parent='HuLoBo')
    tree.create_node('水果胡萝卜',  'SGHLBo', parent='HuLoBo')
    tree.create_node('黄色胡萝卜',  'HSHLBo', parent='GnZCai', data={'别称': ['黄萝卜', '番萝卜', '丁香萝卜']})
    # 萝卜类
    tree.create_node('卞萝卜',      'HLuoBo', parent='GnZCai', data={'别称': ['红萝卜', '红皮萝卜']})
    tree.create_node('水萝卜',      'SLuoBo', parent='GnZCai', data={'别称': ['心里美萝卜', '红心萝卜']})
    tree.create_node('白萝卜',      'BaiLBo', parent='GnZCai', data={'别称': ['萝卜']})
    tree.create_node('樱桃萝卜',    'YigTLB', parent='GnZCai', data={'别称': ['小红萝卜', '迷你萝卜']})
    # 洋葱类
    tree.create_node('洋葱类',      'YangCg', parent='GnZCai', data={'细分': True})
    tree.create_node('紫洋葱',      'ZiYgCg', parent='YangCg', data={'别称': ['紫色洋葱', '洋葱', '紫皮洋葱']})
    tree.create_node('白洋葱',      'BaiYCg', parent='YangCg')
    tree.create_node('干葱头',      'GCongT', parent='YangCg')
    # 大蒜类
    tree.create_node('大蒜类',      'DSuanL', parent='GnZCai', data={'细分': True})
    tree.create_node('大蒜',        'DaSuan', parent='DSuanL', data={'别称': ['蒜末', '蒜片', '蒜泥', '蒜', '蒜瓣']})
    tree.create_node('紫皮大蒜',    'ZiPDaS', parent='DSuanL')
    # 生姜类
    tree.create_node('生姜类',      'SJangL', parent='GnZCai', data={'细分': True})
    tree.create_node('生姜',        'ShengJ', parent='SJangL', data={'别称': ['姜丝', '姜片', '姜']})
    tree.create_node('老姜',        'LJiang', parent='SJangL')
    tree.create_node('嫩姜',        'NengJg', parent='SJangL')
    tree.create_node('黄姜',        'HuangJ', parent='SJangL')
    # 其他根茎/香辛料
    tree.create_node('莲藕',        'LianOo', parent='GnZCai')
    tree.create_node('八角',        'BaJiao', parent='GnZCai', data={'别称': ['大料']})
    
    ## 菌类
    tree.create_node('菌类',        'JunLei', parent='ShuCai', data={'别称': ['蘑菇', '菌', '菇', '蘑'], 'review': 'mogu'})
    tree.create_node('茶树菇',      'ChaSGu', parent='JunLei', data={'别称': ['茶薪菇', '柳松菇', '杨树菇'], 'review': 'mogu'})
    tree.create_node('虫草花',      'ChCHua', parent='JunLei', data={'别称': ['蛹虫草', '北冬虫夏草'], 'review': 'mogu'})
    tree.create_node('干巴革菌',    'GBaJun', parent='JunLei', data={'别称': ['干巴菌', '牛牙齿', '马牙菌'], 'review': 'mogu'})
    
    tree.create_node('松乳菇',      'SgRuGu', parent='JunLei', data={'别称': ['黄谷熟', '谷黄菌'], 'review': 'mogu'})
    tree.create_node('谷熟菌',      'GuShuJ', parent='SgRuGu', data={'review': 'mogu'})
    tree.create_node('松树菌',      'SShuJn', parent='SgRuGu', data={'别称': ['松毛菌', '铆钉菇', '松针菇'], 'review': 'mogu'})
    tree.create_node('奶浆菌',      'NJgJun', parent='SgRuGu', data={'别称': ['奶菌', '牛奶菌', '奶汁菇'], 'review': 'mogu'})
    
    tree.create_node('海鲜菇',      'HXianG', parent='JunLei', data={'别称': ['玉蕈','斑玉蕈','真姬菇', '白玉菇'], 'review': 'mogu'})
    tree.create_node('枣翘鳞肉齿菌', 'HeiHuZ', parent='JunLei', data={'别称': ['虎掌菌', '黑虎掌', '獐子菌', '翘鳞肉齿', '翘鳞肉齿菌', '老鹰菌'], 'review': 'mogu'})
    tree.create_node('黑松露',      'SongLu', parent='JunLei', data={'别称': ['块菌','无娘果','猪拱菌', '松露'], 'review': 'mogu'})
    tree.create_node('红菇',        'HongGu', parent='JunLei', data={'别称': ['大朱菇', '红椎菌'], 'review': 'mogu'})
    
    tree.create_node('干鸡枞菌',     'JiZJun', parent='JunLei', data={'别称': ['鸡枞菌', '鸡枞花菌', '鸡枞菌菜', '鸡脚菇', '伞把菇'], 'review': 'mogu'})
    tree.create_node('黑鸡枞菌',     'HeiJZJ', parent='JiZJun', data={'别称': ['黑皮鸡枞菌'], 'review': 'mogu'})
    
    tree.create_node('鸡腿菇',      'JiTuiJ', parent='JunLei', data={'别称': ['毛头鬼伞', '刺毛菇', '鸡腿蘑'], 'review': 'mogu'})
    tree.create_node('鸡油菌',      'JiYouJ', parent='JunLei', data={'别称': ['黄丝菌', '杏菌', '鸡蛋黄菌', '黄菌', '橙盖鹅膏菌'], 'review': 'mogu'})
    tree.create_node('金耳菌',      'JiErJn', parent='JunLei', data={'别称': ['黄木耳', '脑耳', '金黄银耳', '黄耳', '金耳'], 'review': 'mogu'})
    tree.create_node('金针菇',      'JinZGg', parent='JunLei', data={'别称': ['构菌','朴菇'], 'review': 'mogu'})
    tree.create_node('口蘑',        'KouMoo', parent='JunLei', data={'别称': ['白蘑', '白蘑菇', '双孢菇'], 'review': 'mogu'})
    
    tree.create_node('老人头菌',    'LaoRTu', parent='JunLei', data={'别称': ['仙人头', '沙老包菌', '松茸舅'], 'review': 'mogu'})
    tree.create_node('松茸',        'SongRg', parent='JunLei', data={'别称': ['松茸菌', '松茸菇', '松茸菌菇', '松茸菌菜', '松口菌', '松蕈'], 'review': 'mogu'})
    tree.create_node('木耳',        'HeiMEr', parent='JunLei', data={'别称': ['黑木耳'], 'review': 'mogu'})
    
    tree.create_node('牛肝菌',      'NiuGnJ', parent='JunLei', data={'别称': ['牛肝', '大脚菇','白牛肝菌'], 'review': 'mogu', 'fushaiNode': "菌类"})
    tree.create_node('红葱菌',      'HNiuGJ', parent='NiuGnJ', data={'别称': ['红牛肝', '见手青', '红见手', '捏捏青', '兰茂牛肝菌', '红葱牛肝菌'], 'review': 'mogu'})
    tree.create_node('铜色牛肝菌',   'HiNiuJ', parent='NiuGnJ', data={'别称': ['铜色粉孢牛肝菌', '黑牛肝菌', '褐牛肝菌'], 'review': 'mogu'})
    
    tree.create_node('皮条菌',      'PTiaoJ', parent='JunLei', data={'别称': ['红蜡蘑', '紫蜡蘑', '钉子菌'], 'review': 'mogu'})
    tree.create_node('平菇',        'PingGu', parent='JunLei', data={'别称': ['侧耳','蚝菇'], 'review': 'mogu'})
    tree.create_node('青头菌',      'QgTJun', parent='JunLei', data={'别称': ['变绿红菇', '青冈菌', '绿豆菌', '青盖子', '绿菇', '绿头菌'], 'review': 'mogu'})
    tree.create_node('珊瑚菌',      'ShHJun', parent='JunLei', data={'别称': ['扫帚菌', '扫把菌', '鸡爪菌', '树枝菌'], 'review': 'mogu'})
    tree.create_node('天麻',        'TianMa', parent='JunLei', data={'别称': ['赤箭', '离母'], 'review': 'mogu', 'compare':['天麻'], 'tiaoguo_compare': True})
    
    tree.create_node('香菇',        'XiangG', parent='JunLei', data={'review': 'mogu'})
    tree.create_node('鲜香菇',      'XianXG', parent='XiangG', data={'review': 'mogu', 'fushaiNode': "菌类"})
    tree.create_node('香菇干',      'XGuGan', parent='XiangG', data={'review': 'mogu', 'fushaiNode': "菌类"})
    
    tree.create_node('蟹味菇',      'XieWGu', parent='JunLei', data={'别称': ['真姬菇','玉蕈','斑玉蕈'], 'review': 'mogu'})
    tree.create_node('杏鲍菇',      'XingBG', parent='JunLei', data={'别称': ['杏子菇', '杏鲍菜', '刺芹侧耳'], 'review': 'mogu'})
    tree.create_node('羊肚菌',      'YangDj', parent='JunLei', data={'别称': ['羊肚蘑', '编笠菌', '羊肚菜', '羊蘑', '草笠竹'], 'review': 'mogu'})
                                                                                         
    tree.create_node('银耳',        'YinErr', parent='JunLei', data={'review': 'mogu', 'fushaiNode': "菌类"})
    tree.create_node('鲜银耳',      'XianYR', parent='YinErr')
    tree.create_node('干银耳',      'YErGan', parent='YinErr')
                             
    tree.create_node('绣球菌',      'XiuQJn', parent='JunLei', data={'别称': ['对花菌', '椰菜菌', '蜂窝菌'], 'review': 'mogu'})
    tree.create_node('竹荪',        'ZuuSun', parent='JunLei', data={'别称': ['竹参', '竹笙', '面纱菌', '网纱菌'], 'review': 'mogu'})
    
    #云南特色菜
    tree.create_node('云南菜',      'ynNCai', parent='ShuCai', data={'type': False})
    tree.create_node('余甘子',      'DgGLan', parent='ynNCai', data={'别称': ['滇橄榄', '油甘子', '牛甘果', '余甘果', '油柑'], 'compare': ['余甘子'], 'tiaoguo_compare': True})
    tree.create_node('拐枣',        'GuaiZo', parent='ynNCai', data={'别称': ['万寿果', '鸡爪梨', '金钩梨', '拐枣子', '弯捞捞', '木珊瑚', '枳椇']})
    tree.create_node('海菜花',      'HTngHa', parent='ynNCai', data={'别称': ['茼蒿', '水菜', '水性杨花', '海菜', '异叶水车前', '海菜芯', '波叶海菜'], 'compare': ['海菜花'], 'tiaoguo_compare': True})
    tree.create_node('莼菜',        'ChunCi', parent='ynNCai', data={'compare': ['海菜花']})
    tree.create_node('姜柄瓜',      'JBingG', parent='ynNCai', data={'别称': ['柄瓜', '小瓜', '林士桥瓜', '小麦瓜', '花盘南瓜'], 'compare': ['姜柄瓜'], 'tiaoguo_compare': True})
    tree.create_node('四棱豆',      'LogDou', parent='ynNCai', data={'别称': ['龙豆', '翼豆', '四角豆', '翅豆', '杨桃豆', '龙爪豆'], 'compare': ['四棱豆'], 'tiaoguo_compare': True})
    tree.create_node('豆角',        'DouJao', parent='ynNCai', data={'compare': ['四棱豆']})
    tree.create_node('酸角',        'LuoWzi', parent='ynNCai', data={'别称': ['甜角', '通血图', '通血香', '木罕', '曼姆', '罗望子']})
    
    ##放番茄
    tree.create_node('树花菜',      'ShCHua', parent='ynNCai', data={'别称': ['树花', '树胡子', '树毛衣', '树蝴蝶', '地衣', '鹿角菜', '松萝']})
    tree.create_node('香橼',        'XingYn', parent='ynNCai', data={'别称': ['香圆', '佛手柑'], 'compare': ['香橼'], 'tiaoguo_compare': True})
    tree.create_node('黑老虎',      'XueTGo', parent='ynNCai', data={'别称': ['释迦果', '血藤果', '过山龙藤', '大血藤果', '龙宫果'], 'compare': ['黑老虎'], 'tiaoguo_compare': True})
    tree.create_node('红毛丹',      'HMoDan', parent='ynNCai', data={'compare': ['黑老虎']})
    tree.create_node('莲雾',        'LianWu', parent='ynNCai', data={'compare': ['黑老虎']})
    tree.create_node('燕窝果',      'YnWooG', parent='ynNCai', data={'别称': ['麒麟果','黄龙果','火参果', '刺角瓜', '非洲蜜瓜'], 'compare': ['火龙果', '燕窝果']})
    tree.create_node('洋槐花',      'YHuaiH', parent='ynNCai', data={})
    tree.create_node('茉莉花',      'MoLiHa', parent='ynNCai', data={'别称': ['茉莉','香魂','莫利花','没利花','末莉','末利','木梨花', '茉莉花茶'], 'compare': ['绿茶']})
    tree.create_node('金雀花',      'JngQHa', parent='ynNCai', data={'别称': ['南瓜花', '坝齿花', '金鹊花', '黄雀花', '阳雀花', '紫雀花', '槐花'], 'tiaoguo_compare': True})
    tree.create_node('玫瑰花',      'MGuiHa', parent='ynNCai', data={'别称': ['玫瑰', '徘徊花', '笔头花', '湖花', '刺玫花']})
    tree.create_node('仙人掌果',     'XRZGuo', parent='ynNCai', data={'别称': ['仙桃', '霸王果', '仙掌子', '观音掌', '观音刺', '仙人掌']})
    

    # 水果l
    tree.create_node('水果',        'ShGuoo', parent='Food', data={'type': False})
    tree.create_node('芒果',        'MangGo', parent='ShGuoo')
    tree.create_node('枇杷',        'PiPaaa', parent='ShGuoo')
    tree.create_node('罗汉果',      'LoHGuo', parent='ShGuoo')
    tree.create_node('菠萝类',      'BoLuoL', parent='ShGuoo', data={'type': False})
    tree.create_node('菠萝',        'BooLuo', parent='BoLuoL')
    tree.create_node('凤梨',        'FengLi', parent='BoLuoL')
    tree.create_node("菠萝蜜",      'BoLuoM', parent='ShGuoo')
    
    tree.create_node('火龙果',      'HLongG', parent='ShGuoo')
    tree.create_node('红心火龙果',  'HXHLGo', parent='HLongG', data={'compare': ['火龙果']})
    tree.create_node('白心火龙果',  'BXHLGo', parent='HLongG', data={'compare': ['火龙果']})
    
    tree.create_node('西瓜',        'XiGuaa', parent='ShGuoo', data={'细分': True})
    tree.create_node('麒麟瓜',      'QiLinG', parent='XiGuaa')
    tree.create_node('冰糖小西瓜',  'BTXXGa', parent='XiGuaa')
    tree.create_node('硒砂瓜',      'XiShaG', parent='XiGuaa')
    tree.create_node('特小凤西瓜',  'TXFXGa', parent='XiGuaa')
    
    tree.create_node('花瓜',        'HuaGua', parent='ShGuoo')
    tree.create_node('香蕉',        'XiangJ', parent='ShGuoo')
    
    tree.create_node('苹果梨类',    'PngGLi', parent='ShGuoo', data={'细分': True})
    tree.create_node('苹果',        'PingGo', parent='PngGLi')
    tree.create_node('红富士',      'HFuShi', parent='PingGo')
    tree.create_node('蛇果',        'SheGuo', parent='PingGo')
    tree.create_node('嘎啦',        'GaLaaa', parent='PingGo')
    tree.create_node('国光苹果',    'GGPinG', parent='PingGo')
    
    tree.create_node('梨',          'Liiiii', parent='PngGLi', data={'细分': True})
    tree.create_node('雪梨',        'XueLii', parent='Liiiii')
    tree.create_node('香梨',        'XLiiii', parent='Liiiii')
    tree.create_node('小糖梨',      'XTLiii', parent='Liiiii')
    tree.create_node('翠冠梨',      'CGLiii', parent='Liiiii')
    tree.create_node('酥梨',        'SuLiii', parent='Liiiii')
    tree.create_node('苹果梨',      'PingLi', parent='Liiiii')
    
    ## 柠檬
    tree.create_node('柠檬',        'NingMg', parent='ShGuoo', data={'compare': ['香橼']})
    tree.create_node('小青柠',      'XNngMg', parent='ShGuoo', data={'别称': ['青柠', '青柠檬', '青金桔', '小柠檬']})
    
    tree.create_node('柚橙橘',      'YoChJu', parent='ShGuoo', data={'type': False})
    ## 柚子
    tree.create_node('柚子',        'YuZiii', parent='YoChJu')
    tree.create_node('葡萄柚',      'PTaoYu', parent='YoChJu')
    tree.create_node('红心柚',      'HXinYu', parent='YoChJu')
    tree.create_node('白心柚',      'BXinYu', parent='YoChJu')
    
    ## 橙子
    tree.create_node('橙子',        'ChZiii', parent='YoChJu')
    tree.create_node('脐橙',        'QiChen', parent='YoChJu')
    tree.create_node('血橙',        'XueChn', parent='YoChJu')
    
    ## 橘子
    tree.create_node('橘子',        'JuZiii', parent='YoChJu')
    tree.create_node('橘皮',        'JuPiii', parent='JuZiii')
    tree.create_node('砂糖橘',      'STJuuu', parent='JuZiii')
    tree.create_node('耙耙柑',      'BaBaGn', parent='JuZiii', data={'别称': ['耙耙柑', '丑橘', '不知火']}) 
    tree.create_node('柑橘',        'GanJuu', parent='JuZiii')
    
    ## 葡萄
    tree.create_node('葡萄类',      'PuTaoL', parent='ShGuoo', data={'细分': True})
    tree.create_node('葡萄',        'PuTaoa', parent='PuTaoL')
    tree.create_node('红提子',      'HongTi', parent='PuTaoL', data={'别称': ['红提']})
    tree.create_node('青提',        'QingTi', parent='PuTaoL', data={'compare': ['余甘子']})
    tree.create_node('玫瑰香葡萄',  'MGXPuT', parent='PuTaoL')
    tree.create_node('阳光玫瑰',    'YGMeiG', parent='PuTaoL')
    tree.create_node('巨峰葡萄',    'JFPTao', parent='PuTaoL')
    
    tree.create_node('枣类',        'ZaoLei', parent='ShGuoo')
    tree.create_node('冬枣',        'DngZao', parent='ZaoLei', data={'compare': ['余甘子']})
    tree.create_node('红枣',        'HngZao', parent='ZaoLei', data={'别称': ['大枣']})   #----
    
    ## 桃子
    tree.create_node('桃子',        'TaoZii', parent='ShGuoo')
    tree.create_node('油桃',        'YouTao', parent='TaoZii')
    tree.create_node('水蜜桃',      'ShuiMT', parent='TaoZii')
    tree.create_node('蟠桃',        'PanTao', parent='TaoZii')
    tree.create_node('黄桃',        'HuangT', parent='TaoZii')
    
    tree.create_node('蓝莓',        'LanMei', parent='ShGuoo')
    tree.create_node('树莓',        'ShuMei', parent='ShGuoo')
    tree.create_node('草莓',        'CaoMei', parent='ShGuoo')
    tree.create_node('榴莲',        'LLiann', parent='ShGuoo')
    tree.create_node('荔枝',        'LiZiii', parent='ShGuoo')
    tree.create_node('石榴',        'ShiLiu', parent='ShGuoo')
    
    tree.create_node('杏',          'Xinnnn', parent='ShGuoo')
    tree.create_node('李子',        'LiiZii', parent='ShGuoo')
    tree.create_node('山楂',        'ShanZa', parent='ShGuoo')
    tree.create_node('哈密瓜',      'HaMiGa', parent='ShGuoo', data={'别称': ['甜瓜']})
    tree.create_node('牛油果',      'NiuYGo', parent='ShGuoo')
    tree.create_node('猕猴桃',      'MiHouT', parent='ShGuoo', data={'别称': ['奇异果']})
    tree.create_node('杨梅',        'YangMi', parent='ShGuoo')
    tree.create_node('樱桃',        'YinTao', parent='ShGuoo', data={'别称': ['车厘子'], 'compare': ['小番茄']})
    tree.create_node('木瓜类',      'MuGuaL', parent='ShGuoo')
    tree.create_node('木瓜',        'MuGuaa', parent='MuGuaL', data={'goto': 'MuGuaL'})
    tree.create_node('酸木瓜',      'SMuGua', parent='MuGuaL')
    tree.create_node('青梅',        'QingMe', parent='ShGuoo', data={'别称': ['梅子'], 'compare': ['余甘子']})
    
    
    # 肉类
    tree.create_node('肉类',        'RouLei', parent='Food', data={'type': False})
    # 排骨
    tree.create_node('肋排',        'LeiPai', parent='RouLei')
    tree.create_node('猪肋排',      'ZhLPai', parent='LeiPai', data={'别称': ['猪大排', '排骨', '猪排骨', '猪排']})
    tree.create_node('羊肋排',      'YgLPai', parent='LeiPai')
    ## 猪肉
    tree.create_node('猪肉类',      'ZhuRou', parent='RouLei', data={'type': False})
    tree.create_node('猪肉',        'ZhuRoo', parent='ZhuRou', data={'别称': ['块肉', '肉丝', '肉片', '猪颈肉']})
    tree.create_node('猪肥肉',      'ZFeiRo', parent='ZhuRou')
    tree.create_node('猪里脊',      'ZhuLiJ', parent='ZhuRou', data={'别称': ['猪瘦肉']})
    tree.create_node('猪腊肉',      'ZhuLaR', parent='ZhuRou', data={'别称': ['腊肉']})
    tree.create_node('五花肉',      'WuHuaR', parent='ZhuRou', data={'别称': ['红烧肉']})
    tree.create_node('梅花肉',      'MeiHaR', parent='ZhuRou')
    ## 羊肉
    tree.create_node('羊肉类',      'YangRL', parent='RouLei', data={'type': False})
    tree.create_node('羊肉',        'YgRouu', parent='YangRL')
    tree.create_node('羊排',        'YangPi', parent='YangRL')
    ## 牛肉
    tree.create_node('牛肉类',      'NuRouL', parent='RouLei', data={'type': False})
    tree.create_node('牛肉',        'NiuRou', parent='NuRouL', data={'别称': ['牛肉片']})
    tree.create_node('酱牛肉',      'JNiuRo', parent='NuRouL')
    tree.create_node('牛排',        'NiuPai', parent='NuRouL')
    tree.create_node('牛肋排',      'NiuLiP', parent='NuRouL')
    tree.create_node('牛肋条',      'NiuLiT', parent='NuRouL')
    tree.create_node('牛腩',        'NiuNan', parent='NuRouL')
    ## 大肠
    tree.create_node('大肠类',      'DaChng', parent='RouLei', data={'别称': ['大肠']})
    tree.create_node('猪大肠',      'ZhuDCg', parent='DaChng')
    tree.create_node('羊大肠',      'YgDaCg', parent='DaChng')
    tree.create_node('牛大肠',      'NiuDCg', parent='DaChng')
    ## 肚子
    tree.create_node('肚子',        'DuZiii', parent='RouLei', data={'type': False})
    tree.create_node('猪肚',        'ZhuDuZ', parent='DuZiii')
    tree.create_node('羊肚',        'YgDuZi', parent='DuZiii')
    tree.create_node('牛肚',        'NiuDuZ', parent='DuZiii')
    tree.create_node('牛百叶',      'NBaiYe', parent='DuZiii')
    ## 血类
    tree.create_node('血类',        'XueLei', parent='RouLei', data={'type': False})
    tree.create_node('猪血',        'ZhuXue', parent='XueLei')
    tree.create_node('鸭血',        'YaXuee', parent='XueLei')
    ## 猪肘
    tree.create_node('猪肘',        'ZuZhou', parent='RouLei')
    tree.create_node('猪前肘',      'ZQZhou', parent='ZuZhou')
    tree.create_node('猪后肘',      'ZuHZhu', parent='ZuZhou')
    
    tree.create_node('猪肉末',      'RouMoo', parent='RouLei', data={'别称': ['肉末', '切碎的猪肉', '肉馅', '肉泥', '肉陷']})
    tree.create_node('培根',        'PeiGen', parent='RouLei')
    tree.create_node('火腿',        'HuoTui', parent='RouLei')
    tree.create_node('猪蹄',        'ZhuTii', parent='RouLei', data={'别称': ['猪蹄膀', '肘子']})
    tree.create_node('猪舌头',      'ZhuSTo', parent='RouLei')
    tree.create_node('羊腿',        'YagTui', parent='RouLei')
    tree.create_node('羊肉串',      'YangRC', parent='RouLei', data={'别称': ['烤串']})
    tree.create_node('猪肝',        'ZhuGan', parent='RouLei')
    
    # 禽类
    tree.create_node('禽类',        'QinLei', parent='Food', data={'type': False})
    tree.create_node('鸡肉',        'JiRouu', parent='QinLei', data={'别称': ['鸡', '公鸡', '母鸡', '炸鸡']})
    tree.create_node('鸡爪',        'JiZhua', parent='QinLei')
    tree.create_node('鸡翅中',      'JiCZho', parent='QinLei', data={'别称': ['鸡翅', '烤翅', '炸鸡翅', '烤鸡翅', '翅中']})
    tree.create_node('鸡翅根',      'JiCGen', parent='QinLei', data={'别称': ['翅根']})
    tree.create_node('鸡胸肉',      'JiXRou', parent='QinLei')
    tree.create_node('鸡腿',        'JiTuii', parent='QinLei')
    tree.create_node('手枪腿',      'ShouQT', parent='JiTuii', data={'compare': ["鸡腿"]})
    tree.create_node('琵琶腿',      'PiPTui', parent='JiTuii', data={'compare': ["鸡腿"]})
    
    tree.create_node('整禽',        'ZhgQin', parent='QinLei')
    tree.create_node('整鸡',        'ZhgJii', parent='ZhgQin', data={'别称': ['烤鸡']})
    tree.create_node('整鸭',        'ZhgYaa', parent='ZhgQin', data={'别称': ['北京烤鸭', '烤鸭']})
    tree.create_node('乳鸽',        'RuGeee', parent='ZhgQin', data={'别称': ['鸽子']})
    tree.create_node('鸭肉',        'YaaRou', parent='QinLei')
    tree.create_node('鸭舍',        'YaaShe', parent='QinLei')
    tree.create_node('鸭脖',        'YaaBoo', parent='QinLei')
    tree.create_node('鸭掌',        'YZhang', parent='QinLei')
    tree.create_node('鸭腿',        'YaaTui', parent='QinLei', data={'compare': ["鸡腿"]})
    tree.create_node('鹅腿',        'EeeTui', parent='QinLei', data={'compare': ["鸡腿"]})
    
    tree.create_node('禽类肝',      'QinGan', parent='QinLei')
    tree.create_node('鹅肝',        'eeeGan', parent='QinGan')
    tree.create_node('鸭肝',        'yaaGan', parent='QinGan')
    
    # 蛋类
    tree.create_node('蛋类',        'DanLei', parent='Food', data={'别称': ['蛋'], '细分': True})
    tree.create_node('鸡蛋',        'JiiDan', parent='DanLei', data={'别称': ['煎鸡蛋']})
    tree.create_node('鸭蛋',        'YaaDan', parent='DanLei')
    tree.create_node('鹅蛋',        'EeeDan', parent='DanLei')
    tree.create_node('鹌鹑蛋',      'AnChun', parent='DanLei')
    tree.create_node('皮蛋',        'PiDaan', parent='DanLei')
    
    # 液体类
    tree.create_node('液体类',      'YeTiLe', parent='Food', data={'type': False})
    tree.create_node('白色液体',    'BaiSYe', parent='YeTiLe')
    tree.create_node('豆浆',        'DJiang', parent='BaiSYe')
    tree.create_node('豆奶',        'DouNai', parent='BaiSYe')
    tree.create_node('牛奶',        'NiuNai', parent='BaiSYe')
    tree.create_node('水牛奶',      'SNiuNi', parent='BaiSYe')
    tree.create_node('羊奶',        'YngNai', parent='BaiSYe')
    tree.create_node('酸奶类',      'SNaiLe', parent='BaiSYe')
    tree.create_node('酸奶',        'SuanNi', parent='SNaiLe', data={'别称': ['酸牛奶']})
    tree.create_node('低脂酸奶',    'DZSNai', parent='SNaiLe')
    tree.create_node('蜂蜜',        'FengMi', parent='YeTiLe')
    return tree


def find_node_by_tag(tree, tag):
    nodes = tree.all_nodes()
    for node in nodes:
        if node.tag == tag:
            return node
    return None


if __name__ == '__main__':
    tree = create_standard_tree()
    print(len(tree.leaves()))
    tree.show()