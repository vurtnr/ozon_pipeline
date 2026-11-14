#!/usr/bin/env python3
"""选品约束边界（Seerfar 品级池 → 硬过滤）。

阈值 [user-decided 2026-11-13 Q11]：weight≤500g（服务端下推）、300–5000₽、
upDays≥30、reviewCount≥10、returnCancellationRate 缺失按通过、crossBorderSellable 必须 True。
sellerType 不做硬过滤（Q9：TOP100 实测跨境=0，仅作 LLM 排序信号）。

禁售黑名单 [user-decided 2026-11-13]：docs/m.docx 八大组转中文类目路径关键词，
匹配 records[].categoryInfo.cnTitlePath。组七（需资质）/组八（物流高风险）默认同硬禁售；
美容及卫生保健产品过宽（会误杀面膜），未纳入，待用户裁决。
"""
from dataclasses import dataclass, field

WEIGHT_MAX_G = 500.0
PRICE_MIN_RUB = 300.0
PRICE_MAX_RUB = 5000.0
MIN_UP_DAYS = 30
MIN_REVIEW_COUNT = 10

BANNED_TOP_LEVEL: tuple[str, ...] = (
    "电子产品", "鞋类", "食品", "药店", "珠宝", "书籍",
    "电影、音乐、视频游戏、软件", "Ozon Fresh食品", "慈善",
)  # [user-decided 2026-11-14] 一级类目全禁，前缀匹配自动含全部子类目
  # 命名经 PoC 实测校正：电影类含「、软件」；珠宝非一级类目，由 BANNED_GROUPS 关键词捕获深层路径

BANNED_GROUPS: dict[str, tuple[str, ...]] = {
    "一、武器军事危险器械": ("武器", "枪械", "枪支", "弹药", "刀具", "匕首", "军刀",
        "刺刀", "冷兵器", "双节棍", "指虎", "电击", "弹射", "爆炸", "爆破", "催泪", "防身喷雾"),
    "二、烟草电子烟": ("烟草", "香烟", "雪茄", "电子烟", "烟油", "水烟", "尼古丁", "烟草加热"),
    "三、药品医疗保健": ("药品", "处方药", "药店", "膳食补充剂", "维生素", "生物活性",
        "兽药", "疫苗", "注射", "检测试剂", "诊断试剂", "医疗器械", "医疗用品"),
    "四、酒精危险化学品": ("酒精", "乙醇", "易燃液体", "毒药", "麻醉", "精神类",
        "农药", "危险化学品", "腐蚀性"),
    "五、监控信号干扰": ("隐藏式摄像", "伪装摄像", "隐蔽式录音", "GPS追踪",
        "信号屏蔽", "信号干扰", "秘密获取信息"),
    "六、特殊生活商品": ("活体动物", "昆虫", "活体植物", "种子", "根茎", "渔网",
        "电捕鱼", "捕兽夹", "捕猎", "水银温度计", "丧葬", "棺材", "骨灰", "证件",
        "纸币", "证券", "贵金属", "车牌", "道路标志", "珠宝", "宝石", "大麻",
        "数字商品", "激活码", "电子书", "安全座椅", "安全带调节"),
    "七、需资质特殊审核": ("医疗器械", "生物活性", "膳食补充剂", "运动营养", "运动补剂",
        "检测试剂", "婴幼儿食品", "脱毛", "光电美容", "烟花", "烟火"),
    "八、跨境物流高风险": ("锂电池", "电池", "液体", "喷雾", "压力容器", "香水",
        "清洁剂", "强磁", "蜡烛", "易燃", "粉末"),
}


@dataclass
class Decision:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_record(rec: dict) -> Decision:
    """单品硬过滤：全部约束通过才放行；失败时给出全部原因（供 LLM 排序解释）。"""
    reasons: list[str] = []

    weight = rec.get("weight")
    if weight is None or weight > WEIGHT_MAX_G:
        reasons.append(f"重量超限或缺失: {weight}")

    price = rec.get("price")
    if price is None or not (PRICE_MIN_RUB <= price <= PRICE_MAX_RUB):
        reasons.append(f"价格出界或缺失: {price}")

    if (rec.get("upDays") or 0) < MIN_UP_DAYS:
        reasons.append(f"上架天数不足: {rec.get('upDays')}")
    if (rec.get("reviewCount") or 0) < MIN_REVIEW_COUNT:
        reasons.append(f"评论数不足: {rec.get('reviewCount')}")
    # returnCancellationRate 缺失按通过 [user-decided Q11]，阈值待 Run 数据积累后再定

    cat = (rec.get("categoryInfo") or {}).get("category") or {}
    if cat.get("crossBorderSellable") is not True:
        reasons.append("类目不可跨境销售")

    path = (rec.get("categoryInfo") or {}).get("cnTitlePath") or ""
    top = path.split(">", 1)[0].strip().replace(" ", "")
    if top and top in {t.replace(" ", "") for t in BANNED_TOP_LEVEL}:
        reasons.append(f"禁售一级类目: {top}")
    for group, keywords in BANNED_GROUPS.items():
        hit = next((k for k in keywords if k in path), None)
        if hit:
            reasons.append(f"禁售类目[{group}]: 命中「{hit}」← {path}")

    return Decision(ok=not reasons, reasons=reasons)
