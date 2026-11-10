#!/usr/bin/env python3
"""趋势核验（ROADMAP 任务3）：已上架/已通过品的核心词定期重查，防促销脉冲。

规则（初始值，样本积累后重校准，禁止当常数）：
    月搜量较入库时跌超 VOLUME_DROP → 告警（需求消失/促销退潮）
    均价跌超 PRICE_DROP → 告警（价格战/促销脉冲）
告警写 events（审核台可见），不自动改状态——V1 人工处置。
词在库里的指标即"基线"，重查值与基线对比；重查走批量，一周一次即可。

用法: python3 trend_check.py [--dry-run]   # dry-run 不调 API，只看要查哪些词
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import nexscope_client as NX
import pipeline as P

VOLUME_DROP = -0.5   # 月搜量跌幅告警线
PRICE_DROP = -0.2    # 均价跌幅告警线
WATCH_STATES = ("approved", "listed", "syncing", "winning")
BATCH = 5


def watch_list() -> list[dict]:
    """在监品 + 其词库基线。返回 [{pid, query, base_volume, base_price}]"""
    with P.conn() as c:
        P._migrate(c)
        rows = c.execute(
            "SELECT p.id pid, p.query, k.search_volume, k.avg_price"
            " FROM products p JOIN keywords k ON k.query = p.query"
            " WHERE p.state IN (%s)" % ",".join("?" * len(WATCH_STATES)),
            WATCH_STATES).fetchall()
    return [dict(r) for r in rows]


def check(dry_run: bool = False) -> list[str]:
    watch = watch_list()
    if not watch:
        print("无在监品")
        return []
    if dry_run:
        for w in watch:
            print(f"[dry] id={w['pid']} {w['query']} 基线 量{w['search_volume']}"
                  f" 价{w['avg_price']}")
        return []

    alerts = []
    queries = sorted({w["query"] for w in watch})
    fresh: dict[str, dict] = {}
    for i in range(0, len(queries), BATCH):
        for r in NX.market_keyword_search(queries[i:i + BATCH]):
            if r.get("query"):
                fresh[r["query"]] = r
    with P.conn() as c:
        for w in watch:
            f = fresh.get(w["query"])
            if not f or not w["search_volume"] or not w["avg_price"]:
                continue
            dv = (f.get("searchVolume") or 0) / w["search_volume"] - 1
            dp = (f.get("avgPrice") or 0) / w["avg_price"] - 1
            if dv <= VOLUME_DROP or dp <= PRICE_DROP:
                note = (f"trend 告警 id={w['pid']} {w['query']}: "
                        f"量 {w['search_volume']}→{f.get('searchVolume')} ({dv:+.0%})"
                        f" 价 {w['avg_price']}→{f.get('avgPrice')} ({dp:+.0%})")
                P._event(c, w["pid"], None, "trend_alert", note)
                alerts.append(note)
    for a in alerts:
        print(a)
    print(f"在监 {len(watch)} 品，告警 {len(alerts)}")
    return alerts


if __name__ == "__main__":
    check(dry_run="--dry-run" in sys.argv)
