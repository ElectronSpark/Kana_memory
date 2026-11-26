"""Japanese and simplified Chinese forms of one character (design 11 section 4, S2: the cognate rule).

Pure: no content, I/O or other module. A Japanese kanji and the simplified Chinese character it is the
same character as (時 and 时, 電 and 电, 豪 and 豪) count as one character, so a Chinese text "shows" a
Japanese written form when it contains the form in Chinese spelling (豪華 in 豪华, 電話 in 打电话).

JA_TO_ZH lists the kanji whose two forms differ. A kanji without an entry is its own Chinese spelling when it
is a simplified Chinese character (it encodes in GB2312); otherwise it has none (a Japanese-made character
such as 込 or 働, or a form missing from the table). Some Japanese forms are also GB2312 characters while the
Chinese text writes the same character otherwise (予 and 预, 芸 and 艺, 後 and 后): the table is read before the
encoding test, so they have entries. A Japanese form whose Chinese counterpart depends on the word (弁: 辩, 辨,
瓣 or 办) has no Chinese spelling at all (WORD_DEPENDENT), so a caller can keep a fail-safe answer for it.
"""

ITERATION_MARK = "々"

# Japanese form -> simplified Chinese form of the same character; only kanji whose two forms differ.
JA_TO_ZH = {
    "乗": "乘", "亀": "龟", "予": "预", "係": "系", "値": "值", "傘": "伞", "効": "效", "協": "协", "単": "单",
    "員": "员", "問": "问", "図": "图", "園": "园", "報": "报", "場": "场", "変": "变", "姉": "姊", "対": "对",
    "帰": "归", "広": "广", "強": "强", "後": "后", "択": "择", "時": "时", "晩": "晚", "書": "书", "査": "查",
    "桜": "樱", "楽": "乐", "機": "机", "歩": "步", "歯": "齿", "毎": "每", "気": "气", "涼": "凉", "済": "济",
    "熱": "热", "環": "环", "確": "确", "窓": "窗", "簡": "简", "約": "约", "紙": "纸", "終": "终", "経": "经",
    "結": "结", "絡": "络", "続": "续", "緑": "绿", "練": "练", "習": "习", "聞": "闻", "葉": "叶", "薬": "药",
    "術": "术", "見": "见", "覚": "觉", "計": "计", "話": "话", "誌": "志", "認": "认", "語": "语", "説": "说",
    "読": "读", "調": "调", "談": "谈", "財": "财", "責": "责", "買": "买", "貸": "贷", "質": "质", "車": "车",
    "転": "转", "軽": "轻", "較": "较", "連": "连", "週": "周", "遅": "迟", "遊": "游", "達": "达", "遠": "远",
    "選": "选", "郵": "邮", "鉄": "铁", "銀": "银", "鍵": "键", "鏡": "镜", "長": "长", "閉": "闭", "開": "开",
    "間": "间", "関": "关", "険": "险", "雑": "杂", "難": "难", "電": "电", "響": "响", "頭": "头", "題": "题",
    "顔": "颜", "飛": "飞", "飯": "饭", "飲": "饮", "館": "馆", "馬": "马", "駅": "驿", "験": "验", "魚": "鱼",
    "鳥": "鸟", "黒": "黑", "発": "发", "売": "卖", "県": "县", "絵": "绘", "両": "两", "伝": "传", "従": "从",
    "帯": "带", "戦": "战", "覧": "览", "歴": "历", "竜": "龙", "沢": "泽", "辺": "边", "隣": "邻", "麺": "面",
    "仏": "佛", "挙": "举", "栄": "荣", "検": "检", "権": "权", "様": "样", "氷": "冰", "満": "满", "焼": "烧",
    "産": "产", "畳": "叠", "稲": "稻", "糸": "丝", "絶": "绝", "継": "继", "総": "总", "緒": "绪", "縁": "缘",
    "聴": "听", "脳": "脑", "蔵": "藏", "衛": "卫", "観": "观", "訳": "译", "証": "证", "譲": "让", "豊": "丰",
    "賛": "赞", "贈": "赠", "釈": "释", "録": "录", "閣": "阁", "闘": "斗", "隠": "隐", "霊": "灵", "顕": "显",
    "類": "类", "駆": "驱", "髪": "发", "鶏": "鸡", "麗": "丽", "斉": "齐", "齢": "龄", "歓": "欢", "歳": "岁",
    "浄": "净", "涙": "泪", "渇": "渴", "漢": "汉", "無": "无", "営": "营", "獄": "狱", "砕": "碎", "禍": "祸",
    "穀": "谷", "穂": "穗", "級": "级", "納": "纳", "紛": "纷", "細": "细", "組": "组", "給": "给", "統": "统",
    "網": "网", "緊": "紧", "線": "线", "編": "编", "縮": "缩", "織": "织", "罰": "罚", "聖": "圣", "職": "职",
    "脅": "胁", "臨": "临", "興": "兴", "舎": "舍", "芸": "艺", "華": "华", "衆": "众", "製": "制", "複": "复",
    "討": "讨", "訓": "训", "記": "记", "訪": "访", "設": "设", "許": "许", "診": "诊", "詞": "词", "試": "试",
    "詳": "详", "誠": "诚", "誤": "误", "課": "课", "講": "讲", "謝": "谢", "識": "识", "議": "议", "護": "护",
    "貝": "贝", "負": "负", "貧": "贫", "貨": "货", "費": "费", "資": "资", "賞": "赏", "購": "购", "軍": "军",
    "輸": "输", "違": "违", "適": "适", "遺": "遗", "還": "还", "郷": "乡", "釣": "钓", "銭": "钱", "鋼": "钢",
    "錯": "错", "鍋": "锅", "鎖": "锁", "鐘": "钟", "閑": "闲", "陣": "阵", "陰": "阴", "陽": "阳", "階": "阶",
    "際": "际", "離": "离", "雲": "云", "霧": "雾", "韓": "韩", "頂": "顶", "順": "顺", "預": "预", "領": "领",
    "頼": "赖", "額": "额", "願": "愿", "顧": "顾", "風": "风", "飼": "饲", "飾": "饰", "養": "养", "驚": "惊",
    "鮮": "鲜", "鳴": "鸣", "円": "圆", "塩": "盐", "庁": "厅", "応": "应", "悪": "恶", "愛": "爱", "専": "专",
    "層": "层", "巻": "卷", "帳": "帐", "廃": "废", "弐": "贰", "恵": "惠",
    # The kanji of the course's transparent cognate titles that the first table lacked.
    "価": "价", "個": "个", "備": "备", "傷": "伤", "優": "优", "冊": "册", "別": "别", "則": "则", "剰": "剩",
    "動": "动", "務": "务", "勝": "胜", "勢": "势", "厳": "严", "収": "收", "囲": "围", "増": "增", "壊": "坏",
    "夢": "梦", "実": "实", "師": "师", "幹": "干", "庫": "库", "張": "张", "徹": "彻", "悩": "恼", "慣": "惯",
    "掃": "扫", "損": "损", "撃": "击", "敗": "败", "業": "业", "極": "极", "橋": "桥", "決": "决", "渉": "涉",
    "減": "减", "準": "准", "漬": "渍", "現": "现", "積": "积", "筆": "笔", "紹": "绍", "絞": "绞", "縛": "缚",
    "蘇": "苏", "襲": "袭", "規": "规", "親": "亲", "評": "评", "誇": "夸", "誕": "诞", "誰": "谁", "論": "论",
    "貼": "贴", "進": "进", "運": "运", "過": "过", "鉛": "铅", "鋭": "锐", "門": "门", "黙": "默",
}

# Japanese forms whose Chinese counterpart depends on the word: never given a Chinese spelling.
WORD_DEPENDENT = frozenset("弁")


def is_ideograph(char: str) -> bool:
    """A CJK unified or compatibility ideograph (kana, 々 and punctuation are not)."""
    return ("一" <= char <= "鿿" or "㐀" <= char <= "䶿" or "豈" <= char <= "﫿"
            or "\U00020000" <= char <= "\U0003134f")


def is_simplified_chinese(char: str) -> bool:
    """The character is in GB2312, the simplified Chinese character set."""
    try:
        char.encode("gb2312")
    except UnicodeEncodeError:
        return False
    return True


def chinese_character(char: str) -> str | None:
    """The simplified Chinese form of a kanji, the table before the encoding test; None: there is none."""
    if char in WORD_DEPENDENT:
        return None
    if char in JA_TO_ZH:
        return JA_TO_ZH[char]
    return char if is_simplified_chinese(char) else None


def chinese_spelling(form: str) -> str | None:
    """A Japanese written form with every kanji in its Chinese form, or None when a kanji has none.

    Kana and other non-ideographs stay as written (a form with kana is never part of a Chinese text);
    々 repeats the character before it (人々 is 人人).
    """
    result = []
    for char in str(form):
        if char == ITERATION_MARK and result:
            char = result[-1]
        elif is_ideograph(char):
            char = chinese_character(char)
            if char is None:
                return None
        result.append(char)
    return "".join(result)


def chinese_text(text: str) -> str:
    """A shown text with every listed Japanese kanji in its Chinese form, everything else as written.

    For searching: chinese_spelling(form) in chinese_text(text) finds the form in either script.
    """
    result = []
    for char in str(text):
        if char == ITERATION_MARK and result:
            char = result[-1]
        result.append(JA_TO_ZH.get(char, char))
    return "".join(result)


def shows_form(form: str, text: str) -> bool | None:
    """Whether ``text`` contains the written form in either script; None: the form has no Chinese spelling."""
    spelling = chinese_spelling(form)
    return None if spelling is None else spelling in chinese_text(text)
