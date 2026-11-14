#!/usr/bin/env python3
"""Seerfar Open API 薄封装：调用、积分台账、独立日熔断、Run 级硬上限、参数缓存。

积分单价（一手：doc.seerfar.cn api-pricing.example.json）：
    productReport/search/ozon 10/次 · market/search/ozon 10 · category/search/ozon 10
    product/monthlySalesTrend/ozon 1/SKU · quota 0
计费（一手 tip）：HTTP200 且 code!=200 业务失败仍扣 1 积分，禁止重试；
    HTTP 非 200/异常退预扣积分，可安全重试一次。成功 code=200。
熔断：日消耗超 DAILY_CREDIT_CAP 拒绝调用；单 Run 超 max_run_calls 拒绝（[user-decided Q3]=10）。
缓存：相同 endpoint+body 24h 内命中本地缓存，不重复扣费（[user-decided Q3]）。
对账：quota() 免费查 creditUsed，调用前后差值=真实扣费（Nexscope 无此能力）。
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import SEERFAR_API_KEY
import pipeline as P

BASE = "https://api.seerfar.cn"
CREDITS = {
    "/open-api/productReport/search/ozon": 10,
    "/open-api/market/search/ozon": 10,
    "/open-api/category/search/ozon": 10,
    "/open-api/product/monthlySalesTrend/ozon": 1,
}
DAILY_CREDIT_CAP = 330  # 独立通道，不与 Nexscope 混算（endpoint 前缀 /open-api/ 隔离）
MAX_RUN_CALLS = 10      # 单 Run 调用硬上限 [user-decided Q3]
CACHE_TTL = "-1 day"


class CreditFuseError(RuntimeError):
    """日/Run 积分熔断"""


def spent_today() -> int:
    with P.conn() as c:
        P._migrate(c)
        row = c.execute(
            "SELECT COALESCE(SUM(credits),0) s FROM credit_log"
            " WHERE created_at >= date('now') AND endpoint LIKE '/open-api/%'").fetchone()
    return row["s"]


def _run_calls(run_id: str) -> int:
    with P.conn() as c:
        row = c.execute(
            "SELECT COUNT(*) n FROM credit_log"
            " WHERE run_id=? AND endpoint LIKE '/open-api/%'", (run_id,)).fetchone()
    return row["n"]


def _log(endpoint: str, credits: int, body: dict, run_id: str | None) -> None:
    with P.conn() as c:
        P._migrate(c)
        c.execute("INSERT INTO credit_log(endpoint,credits,detail,created_at,run_id)"
                  " VALUES(?,?,?,?,?)",
                  (endpoint, credits, json.dumps(body, ensure_ascii=False)[:200],
                   P.now(), run_id))


def _hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _cache_get(endpoint: str, body_hash: str):
    with P.conn() as c:
        P._migrate(c)
        row = c.execute(
            "SELECT response_json FROM seerfar_cache WHERE endpoint=? AND body_hash=?"
            " AND created_at >= datetime('now', ?)",
            (endpoint, body_hash, CACHE_TTL)).fetchone()
    return json.loads(row["response_json"]) if row else None


def _cache_put(endpoint: str, body_hash: str, payload: dict) -> None:
    with P.conn() as c:
        P._migrate(c)
        c.execute("INSERT OR REPLACE INTO seerfar_cache VALUES(?,?,?,?)",
                  (endpoint, body_hash, json.dumps(payload, ensure_ascii=False), P.now()))


def _request(endpoint: str, body: dict | None, method: str) -> dict:
    req = urllib.request.Request(
        BASE + endpoint,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {SEERFAR_API_KEY}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _post_with_retry(endpoint: str, body: dict) -> dict:
    """HTTP 非 200/网络异常退积分，安全重试一次；HTTP200 业务失败由调用方分类。"""
    try:
        return _request(endpoint, body, "POST")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return _request(endpoint, body, "POST")  # 仅重试一次，再失败向上抛


def call(endpoint: str, body: dict, run_id: str | None = None,
         max_run_calls: int = MAX_RUN_CALLS) -> dict:
    """POST Seerfar 并记账。返回 data。缓存命中直接返回，不扣费不计数。"""
    body_hash = _hash(body)
    cached = _cache_get(endpoint, body_hash)
    if cached is not None:
        return cached["data"]

    est = CREDITS[endpoint]
    if spent_today() + est > DAILY_CREDIT_CAP:
        raise CreditFuseError(
            f"日熔断：今日已 {spent_today()} + 本次 {est} > 上限 {DAILY_CREDIT_CAP}")
    if run_id and _run_calls(run_id) >= max_run_calls:
        raise CreditFuseError(f"Run 熔断：{run_id} 已达 {max_run_calls} 次硬上限")

    payload = _post_with_retry(endpoint, body)
    if payload.get("code") != 200:
        _log(endpoint, 1, body, run_id)  # 业务失败照扣 1 积分（一手 tip），不重试
        raise RuntimeError(f"Seerfar error: {payload.get('code')} {payload.get('msg')}")

    _log(endpoint, est, body, run_id)
    _cache_put(endpoint, body_hash, payload)
    return payload["data"]


def quota() -> dict:
    """免费查积分余额（0 积分，不记账）。返回 {creditLimit, creditUsed, ...}。"""
    return _request("/open-api/quota", None, "GET")["data"]


def product_report(body: dict, run_id: str | None = None) -> dict:
    """热销榜单：body 需含 page{pageNumber,pageSize,orders}；weight/price/categoryIds 下推。"""
    return call("/open-api/productReport/search/ozon", body, run_id=run_id)
