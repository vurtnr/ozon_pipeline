#!/usr/bin/env python3
"""关键词库：Nexscope 词级指标的本地资产化。

第一性原理：词级指标（月搜/竞争/退货率）是慢变量（周级变化），
一次 harvest 入库摊薄复用；日常选品从库里 pop 未消费词，零积分。
一个词只消费一次（上过架的词不再重复买数据）。

用法:
    python3 keyword_library.py build [--limit 20] [--batch 5]  # 从类目白名单建库
    python3 keyword_library.py refresh                         # TTL 过期词重查
    python3 keyword_library.py stats                           # 库容统计
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import nexscope_client as NX
import pipeline as P

ROOT = Path(__file__).parent
LEAVES = json.load(open(ROOT / "data/categories-leaf.json", encoding="utf-8"))

# 顶层类目黑名单 [agent-suggested 2026-11-10，用户确认前生效即视为草案]：
# 尺码地狱（衣/鞋/配饰）、EAC/FAC 认证（电子/家电/儿童/美妆）、
# 保质期（食品）、医疗（药房）、跨境禁售（成人/烟草）、无跟卖意义（书/影音）
TOP_BLACKLIST = {
    "Одежда", "Обувь", "Галантерея и аксессуары",
    "Электроника", "Бытовая техника",
    "Продукты питания", "Продукты питания Ozon Fresh",
    "Аптека", "Красота и гигиена", "Детские товары",
    "Товары для взрослых", "Товары для курения и аксессуары",
    "Книги", "Кино, музыка, видеоигры, софт",
}

# 漏斗阈值（读时过滤，不写死进库——阈值重校不用重买数据）
MIN_SEARCH_VOLUME = 5000
MIN_GROWTH_RATE = -5.0
MAX_COMPETING = 3000
PRICE_BAND = (300.0, 5000.0)
MAX_RETURN_RATE = 20.0
TOP_PRODUCTS = 3          # 每词留 top3 爆款（喂 1688 图搜）

TTL_DAYS = 14             # 词指标保鲜期


def whitelist() -> list[str]:
    """白名单叶类目名（pathRu 末段），去重排序。"""
    return sorted({c["pathRu"].split("/")[-1] for c in LEAVES
                   if not c.get("disabled")
                   and c["pathRu"].split("/")[0] not in TOP_BLACKLIST})


def _harvested_seeds(c: sqlite3.Connection) -> set[str]:
    return {r["seed"] for r in c.execute("SELECT seed FROM harvested_seeds")}


def _upsert(c: sqlite3.Connection, seed: str, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        q = r.get("query")
        if not q:
            continue
        tops = [{"ozonId": p.get("ozonId"), "sku": p.get("sku"),
                 "title": p.get("title"), "imageUrl": p.get("imageUrl")}
                for p in (r.get("products") or [])[:TOP_PRODUCTS]]
        c.execute(
            "INSERT INTO keywords(query,query_cn,category_seed,search_volume,"
            "growth30d,competing_products,avg_price,return_rate,top_products,"
            "fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(query) DO UPDATE SET"
            " query_cn=excluded.query_cn,"
            " search_volume=excluded.search_volume,"
            " growth30d=excluded.growth30d,"
            " competing_products=excluded.competing_products,"
            " avg_price=excluded.avg_price, return_rate=excluded.return_rate,"
            " top_products=excluded.top_products,"
            " fetched_at=excluded.fetched_at",
            (q, r.get("queryCn"), seed, r.get("searchVolume"),
             r.get("count30GrowthRate"),
             r.get("competingProducts"), r.get("avgPrice"),
             r.get("returnCancellationRate"),
             json.dumps(tops, ensure_ascii=False), P.now()))
        n += 1
    return n


def build(limit: int = 20, seeds: list[str] | None = None) -> None:
    """harvest 建库。默认从白名单轮换（游标=harvested_seeds）；--seeds 显式指定。
    注意：API 调用期间不能持 DB 写事务（client 记账要写库），故每词独立开合连接。"""
    with P.conn() as c:
        P._migrate(c)
        done = _harvested_seeds(c)
    if seeds is None:
        seeds = [s for s in whitelist() if s not in done][:limit]
    else:
        seeds = [s for s in seeds if s not in done]
    if not seeds:
        sys.exit("白名单类目已全部 harvest，用 refresh 刷新旧词")
    total = 0
    for seed in seeds:
        rows = NX.market_keyword_search(seed)  # 不持锁调 API
        with P.conn() as c:
            P._migrate(c)
            n = _upsert(c, seed, rows)
            c.execute("INSERT OR IGNORE INTO harvested_seeds"
                      "(seed,harvested_at) VALUES(?,?)", (seed, P.now()))
        total += n
        print(f"[ok] {seed}: +{len(rows)} 词", file=sys.stderr)
    print(f"harvest {len(seeds)} 类目种子，库新增/更新 {total} 词")


def passes(r: sqlite3.Row) -> bool:
    price = r["avg_price"]
    return ((r["search_volume"] or 0) >= MIN_SEARCH_VOLUME
            and (r["growth30d"] or 0) >= MIN_GROWTH_RATE
            and (r["competing_products"] or 10**9) <= MAX_COMPETING
            and price is not None and PRICE_BAND[0] <= price <= PRICE_BAND[1]
            and (r["return_rate"] or 0) <= MAX_RETURN_RATE)


def consume(n: int) -> list[sqlite3.Row]:
    """pop n 个合格未消费词（月搜降序），标记 consumed_at。"""
    with P.conn() as c:
        P._migrate(c)
        rows = [r for r in c.execute(
            "SELECT * FROM keywords WHERE consumed_at IS NULL"
            " ORDER BY search_volume DESC").fetchall() if passes(r)]
        picked = rows[:n]
        for r in picked:
            c.execute("UPDATE keywords SET consumed_at=? WHERE query=?",
                      (P.now(), r["query"]))
    return picked


def refresh() -> None:
    """TTL 过期词重查（一词一调）。"""
    cutoff = (datetime.now() - timedelta(days=TTL_DAYS)).isoformat()
    with P.conn() as c:
        P._migrate(c)
        stale = [r["query"] for r in c.execute(
            "SELECT query FROM keywords WHERE fetched_at < ?", (cutoff,))]
    if not stale:
        print("无过期词")
        return
    for q in stale:
        rows = NX.market_keyword_search(q)  # 不持锁调 API
        with P.conn() as c:
            P._migrate(c)
            _upsert(c, "(refresh)", rows)
        print(f"[ok] refresh {q}", file=sys.stderr)
    print(f"刷新 {len(stale)} 词")


def stats() -> None:
    with P.conn() as c:
        P._migrate(c)
        rows = c.execute("SELECT * FROM keywords").fetchall()
    qual = [r for r in rows if not r["consumed_at"] and passes(r)]
    print(f"总词数 {len(rows)} | 合格未消费 {len(qual)}"
          f" | 已消费 {sum(1 for r in rows if r['consumed_at'])}"
          f" | 今日已估积分 {NX.spent_today()}/{NX.DAILY_CREDIT_CAP}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "build":
        get = lambda k, d: int(args[args.index(k) + 1]) if k in args else d
        explicit = args[args.index("--seeds") + 1].split(",") if "--seeds" in args else None
        build(limit=get("--limit", 20), seeds=explicit)
    elif cmd == "refresh":
        refresh()
    elif cmd == "stats":
        stats()
    else:
        sys.exit(f"未知命令 {cmd}")
