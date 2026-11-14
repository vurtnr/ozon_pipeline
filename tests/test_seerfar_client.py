#!/usr/bin/env python3
"""seerfar_client 测试：fixture 驱动、mock urlopen，零积分消耗。

对齐 Seerfar 一手文档：成功 code=200；业务失败(HTTP200/code!=200)扣 1 积分不重试；
HTTP 非 200/网络异常退积分可安全重试一次；quota 免费。
"""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline as P
import seerfar_client as SF


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "DB", tmp_path / "test.db")
    with P.conn() as c:
        c.executescript(P.SCHEMA)
        P._migrate(c)
    return tmp_path


def make_response(payload: dict):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return Resp(json.dumps(payload).encode())


def ok_payload(records=None):
    return {"code": 200, "msg": "SUCCESS",
            "data": {"total": 2, "records": records if records is not None else [{"sku": 1}]}}


def patch_urlopen(monkeypatch, side_effects):
    calls = []

    def fake(req, timeout=0):
        calls.append(req)
        eff = side_effects[len(calls) - 1] if len(calls) <= len(side_effects) else side_effects[-1]
        if isinstance(eff, Exception):
            raise eff
        return make_response(eff)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def test_success_records_credits_and_returns_data(db, monkeypatch):
    calls = patch_urlopen(monkeypatch, [ok_payload()])
    data = SF.call("/open-api/productReport/search/ozon", {"page": {"pageNumber": 1}}, run_id="r1")
    assert data["records"] == [{"sku": 1}]
    assert len(calls) == 1
    with P.conn() as c:
        row = c.execute("SELECT endpoint,credits,run_id FROM credit_log").fetchone()
    assert row["credits"] == 10 and row["run_id"] == "r1"


def test_cache_hit_skips_second_call(db, monkeypatch):
    calls = patch_urlopen(monkeypatch, [ok_payload()])
    body = {"page": {"pageNumber": 1}}
    SF.call("/open-api/productReport/search/ozon", body)
    SF.call("/open-api/productReport/search/ozon", body)  # 相同参数 24h 内
    assert len(calls) == 1  # 第二次走缓存，不重复扣费


def test_daily_fuse(db, monkeypatch):
    monkeypatch.setattr(SF, "spent_today", lambda: SF.DAILY_CREDIT_CAP)
    with pytest.raises(SF.CreditFuseError):
        SF.call("/open-api/productReport/search/ozon", {})


def test_run_fuse_counts_calls(db, monkeypatch):
    patch_urlopen(monkeypatch, [ok_payload({"sku": 1}), ok_payload({"sku": 2})])
    SF.call("/open-api/productReport/search/ozon", {"p": 1}, run_id="r1")
    SF.call("/open-api/productReport/search/ozon", {"p": 2}, run_id="r1")
    with pytest.raises(SF.CreditFuseError):
        SF.call("/open-api/productReport/search/ozon", {"p": 3}, run_id="r1", max_run_calls=2)


def test_business_failure_no_retry_logs_one_credit(db, monkeypatch):
    calls = patch_urlopen(monkeypatch, [{"code": 8000, "msg": "limit exhausted", "data": None}])
    with pytest.raises(RuntimeError):
        SF.call("/open-api/productReport/search/ozon", {})
    assert len(calls) == 1  # 业务失败禁止重试
    with P.conn() as c:
        assert c.execute("SELECT credits FROM credit_log").fetchone()["credits"] == 1


def test_http_error_retries_once(db, monkeypatch):
    calls = patch_urlopen(monkeypatch, [
        urllib.error.HTTPError("u", 500, "server", {}, None),
        ok_payload(),
    ])
    data = SF.call("/open-api/productReport/search/ozon", {})
    assert data["records"] == [{"sku": 1}]
    assert len(calls) == 2  # HTTP 失败退积分，安全重试一次


def test_quota_free_and_unlogged(db, monkeypatch):
    patch_urlopen(monkeypatch, [{"code": 200, "data": {"creditLimit": 10000, "creditUsed": 20}}])
    q = SF.quota()
    assert q["creditUsed"] == 20
    with P.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM credit_log").fetchone()["n"] == 0
