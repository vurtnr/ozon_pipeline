#!/usr/bin/env python3
"""Nexscope API 薄封装：调用、积分台账、日熔断。

积分单价（2026-11 实测自账户消耗记录）：
    marketKeywordSearch 32/次 · categoryProducts 10/次 · categorySearch 32/次
熔断：日消耗估算超 DAILY_CREDIT_CAP 即拒绝调用（防循环 bug 烧穿月度额度）。
批量：keywords 列表实测不支持（2026-11-10，传 2 词返回 0 行仍计费），一词一调。
注意：Nexscope 不回传扣费明细，credit_log 里全是估算值。
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import NEXSCOPE_API_KEY
import pipeline as P

BASE = "https://api.nexscope.ai"
CREDITS = {"/sr/ozon/marketKeywordSearch": 32,
           "/mp/ozon/categoryProducts": 10,
           "/sr/ozon/categorySearch": 32}
DAILY_CREDIT_CAP = 330  # 10,000 积分 ÷ 30 天（[user-decided] 2026-11-10 购入 1 万档）


class CreditFuseError(RuntimeError):
    """日积分熔断"""


def spent_today() -> int:
    with P.conn() as c:
        P._migrate(c)
        row = c.execute("SELECT COALESCE(SUM(credits),0) s FROM credit_log"
                        " WHERE created_at >= date('now')").fetchone()
    return row["s"]


def call(endpoint: str, body: dict, n_units: int = 1) -> list:
    """POST Nexscope 并记账。n_units=计费单位数（批量调用=词数，保守估）。"""
    est = CREDITS[endpoint] * n_units
    if spent_today() + est > DAILY_CREDIT_CAP:
        raise CreditFuseError(
            f"日熔断：今日已估 {spent_today()} + 本次 {est} > 上限 {DAILY_CREDIT_CAP}")
    req = urllib.request.Request(
        BASE + endpoint, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {NEXSCOPE_API_KEY}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("code") != 0:
        raise RuntimeError(f"Nexscope error: {payload.get('code')} {payload.get('msg')}")
    with P.conn() as c:
        P._migrate(c)
        c.execute("INSERT INTO credit_log(endpoint,credits,detail,created_at)"
                  " VALUES(?,?,?,?)",
                  (endpoint, est, json.dumps(body, ensure_ascii=False)[:200], P.now()))
    return payload["data"]["data"]


def market_keyword_search(keyword: str, page_size: int = 50) -> list:
    """单词查询：返回 50 个相关词行（含词级指标 + queryCn + products top10）。"""
    return call("/sr/ozon/marketKeywordSearch",
                {"keywords": [keyword], "page": {"page": 1, "pageSize": page_size}})
