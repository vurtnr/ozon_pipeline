#!/usr/bin/env python3
"""pipeline.db：选品→上架全流程状态机（SQLite，唯一事实源）。

状态流:
    discovered → scored → sourced → checked → profitable
             → imaging → review_pending → approved → listed
             → syncing → winning → videoing → video_pending → done
    任何阶段可 → dead（附 death_reason）

用法:
    python3 pipeline.py init                     # 建库
    python3 pipeline.py add <query> <ozon_id>    # 录候选
    python3 pipeline.py advance <id> <to_state> [note]
    python3 pipeline.py list [state]
    python3 pipeline.py kill <id> <reason>
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path(__file__).parent / "data/pipeline.db"

# 合法状态流转（防手写状态出错）
TRANSITIONS = {
    "discovered": ["scored", "dead"],
    "scored": ["sourced", "dead"],
    "sourced": ["checked", "dead"],
    "checked": ["profitable", "dead"],
    "profitable": ["imaging", "dead"],
    "imaging": ["review_pending", "dead"],
    "review_pending": ["approved", "imaging", "dead"],  # 打回→imaging；毙品→dead
    "approved": ["listed", "dead"],
    "listed": ["syncing", "winning", "dead"],
    "syncing": ["winning", "dead"],
    "winning": ["videoing", "done", "dead"],
    "videoing": ["video_pending", "dead"],
    "video_pending": ["done", "videoing", "dead"],
    "done": [],
    "dead": [],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,              -- 来源关键词
    ozon_id TEXT,                     -- Ozon 竞品/本店商品 ID
    source_1688_id TEXT,              -- 1688 货源商品 ID
    state TEXT NOT NULL DEFAULT 'discovered',
    death_reason TEXT,
    metrics_json TEXT DEFAULT '{}',   -- 各阶段指标快照（选品分/趋势/利润）
    prompt_version TEXT,              -- 生图/文案所用 prompt 版本（eval 追溯）
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(query, ozon_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    from_state TEXT, to_state TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    kind TEXT NOT NULL,               -- image | video | category_mapping
    asset_path TEXT NOT NULL,
    decision TEXT,                    -- approve | regenerate | kill
    reason TEXT,                      -- 死因枚举（打回/毙品必填）
    decided_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keywords (
    query TEXT PRIMARY KEY,           -- 俄语关键词
    query_cn TEXT,                    -- 中文（API 自带）
    category_seed TEXT,               -- 来源类目（categories-leaf 叶名）
    search_volume INTEGER,
    growth30d REAL,
    competing_products INTEGER,
    avg_price REAL,
    return_rate REAL,
    top_products TEXT DEFAULT '[]',   -- JSON: [{ozonId,sku,title,imageUrl}] top3
    consumed_at TEXT,                 -- 非空=已消费（一个词只上一次架）
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    credits INTEGER NOT NULL,         -- 估算值（Nexscope 不回传扣费明细）
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harvested_seeds (
    seed TEXT PRIMARY KEY,            -- 已 harvest 的类目种子（轮换游标）
    harvested_at TEXT NOT NULL
);
"""

# 审核死因枚举（打回/毙品必填，可统计，反哺阈值）
KILL_REASONS = ["图片质量", "商品形变", "文案错误", "侵权风险", "利润不足", "其他"]


def conn() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def _migrate(c: sqlite3.Connection) -> None:
    """已有库的增量迁移（新库走 SCHEMA 全量）"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(reviews)")}
    if cols and "reason" not in cols:
        c.execute("ALTER TABLE reviews ADD COLUMN reason TEXT")
    # keywords / credit_log 后加表，老库补建
    kcols = {r["name"] for r in c.execute("PRAGMA table_info(keywords)")}
    if kcols and "query_cn" not in kcols:
        c.execute("ALTER TABLE keywords ADD COLUMN query_cn TEXT")
    c.executescript(
        "CREATE TABLE IF NOT EXISTS keywords ("
        "query TEXT PRIMARY KEY, query_cn TEXT, category_seed TEXT, search_volume INTEGER,"
        "growth30d REAL, competing_products INTEGER, avg_price REAL,"
        "return_rate REAL, top_products TEXT DEFAULT '[]', consumed_at TEXT,"
        "fetched_at TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS credit_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL,"
        "credits INTEGER NOT NULL, detail TEXT, created_at TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS harvested_seeds ("
        "seed TEXT PRIMARY KEY, harvested_at TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS seerfar_cache ("
        "endpoint TEXT NOT NULL, body_hash TEXT NOT NULL, response_json TEXT NOT NULL,"
        "created_at TEXT NOT NULL, PRIMARY KEY(endpoint, body_hash));"
    )
    cl = {r["name"] for r in c.execute("PRAGMA table_info(credit_log)")}
    if cl and "run_id" not in cl:
        c.execute("ALTER TABLE credit_log ADD COLUMN run_id TEXT")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def cmd_init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)
    print(f"已建库: {DB}")


def _event(c: sqlite3.Connection, pid: int, frm: str | None, to: str, note: str) -> None:
    c.execute("INSERT INTO events(product_id,from_state,to_state,note,created_at)"
              " VALUES(?,?,?,?,?)", (pid, frm, to, note, now()))


def cmd_add(query: str, ozon_id: str) -> None:
    with conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO products(query,ozon_id,created_at,updated_at)"
            " VALUES(?,?,?,?)", (query, ozon_id, now(), now()))
        if cur.rowcount:
            _event(c, cur.lastrowid, None, "discovered", "录入候选")
        print(f"id={cur.lastrowid}（已存在则忽略）")


def cmd_advance(pid: int, to: str, note: str = "") -> None:
    with conn() as c:
        row = c.execute("SELECT state FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            sys.exit(f"产品 {pid} 不存在")
        frm = row["state"]
        if to not in TRANSITIONS.get(frm, []):
            sys.exit(f"非法流转: {frm} → {to}（允许: {TRANSITIONS[frm]}）")
        c.execute("UPDATE products SET state=?, updated_at=? WHERE id=?",
                  (to, now(), pid))
        _event(c, pid, frm, to, note)
        print(f"{pid}: {frm} → {to}")


def cmd_kill(pid: int, reason: str) -> None:
    with conn() as c:
        row = c.execute("SELECT state FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            sys.exit(f"产品 {pid} 不存在")
        c.execute("UPDATE products SET state='dead', death_reason=?, updated_at=?"
                  " WHERE id=?", (reason, now(), pid))
        _event(c, pid, row["state"], "dead", reason)
        print(f"{pid}: dead（{reason}）")


def cmd_list(state: str | None = None) -> None:
    with conn() as c:
        sql = "SELECT id,query,ozon_id,state,death_reason,updated_at FROM products"
        rows = (c.execute(sql + " WHERE state=? ORDER BY updated_at DESC", (state,))
                if state else
                c.execute(sql + " ORDER BY updated_at DESC LIMIT 50")).fetchall()
    for r in rows:
        dead = f" ✗{r['death_reason']}" if r["death_reason"] else ""
        print(f"{r['id']:>4} [{r['state']:<14}] {r['query']:<40} {r['ozon_id'] or '-':<12}{dead}")


def record_review(pid: int, kind: str, asset: str, decision: str,
                  reason: str = "") -> None:
    """审核台调用：存 eval 标签。decision: approve|regenerate|kill"""
    if decision in ("regenerate", "kill") and not (
            reason in KILL_REASONS or reason.startswith("其他")):
        raise ValueError(f"{decision} 必须给死因: {KILL_REASONS}")
    with conn() as c:
        _migrate(c)
        c.execute("INSERT INTO reviews(product_id,kind,asset_path,decision,"
                  "reason,decided_at,created_at) VALUES(?,?,?,?,?,?,?)",
                  (pid, kind, asset, decision, reason, now(), now()))
    nxt = {"approve": "approved", "regenerate": "imaging", "kill": "dead"}[decision]
    if decision == "kill":
        cmd_kill(pid, reason or "审核毙品")
    else:
        cmd_advance(pid, nxt, f"审核{decision}" + (f"（{reason}）" if reason else ""))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], args[1:]
    if cmd == "init":
        cmd_init()
    elif cmd == "add":
        cmd_add(rest[0], rest[1])
    elif cmd == "advance":
        cmd_advance(int(rest[0]), rest[1], rest[2] if len(rest) > 2 else "")
    elif cmd == "kill":
        cmd_kill(int(rest[0]), rest[1])
    elif cmd == "list":
        cmd_list(rest[0] if rest else None)
    else:
        sys.exit(f"未知命令 {cmd}")
