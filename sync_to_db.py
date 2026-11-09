#!/usr/bin/env python3
"""把现有 artifacts（candidates / sourcing / specs / gen 图片）幂等回填进 pipeline.db。

workers 本体暂不写 DB（留待 image_worker 升级切片），本脚本是唯一事实源的桥。
重复运行安全：已存在的产品只补 metrics_json，状态不回头；trend/econ 两道闸
worker 未实现，跳过并在 events 里明示。

用法: python3 sync_to_db.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as P

ROOT = Path(__file__).parent
DATA = ROOT / "data"


# PoC 手工生图目录名（非 offer_id 命名的遗留），image_worker 升级后统一 offer_id，此表可删
GEN_DIR_ALIASES = {"дозатор для жидкого мыла": "soap"}


def latest(pattern: str) -> Path | None:
    files = sorted(DATA.glob(pattern))
    return files[-1] if files else None


def _advance(c, pid, frm, to, note):
    c.execute("UPDATE products SET state=?, updated_at=? WHERE id=?",
              (to, P.now(), pid))
    P._event(c, pid, frm, to, note)


def sync() -> None:
    cand_file = latest("candidates-*.json")
    src_file = latest("sourcing-*.json")
    if not cand_file or not src_file:
        sys.exit("缺 candidates 或 sourcing 报告")
    candidates = {c["query"]: c for c in
                  json.load(open(cand_file, encoding="utf-8"))["candidates"]}
    sourced = {s["query"]: s for s in
               json.load(open(src_file, encoding="utf-8")) if s.get("ok")}
    specs = {json.load(open(f, encoding="utf-8"))["_meta"]["query"]: f
             for f in (DATA / "specs").glob("*.json")}

    n_new = 0
    with P.conn() as c:
        P._migrate(c)
        for query, cand in candidates.items():
            top = cand["topProducts"][0]
            row = c.execute("SELECT id,state FROM products"
                            " WHERE query=? AND ozon_id=?",
                            (query, top["ozonId"])).fetchone()
            metrics = {
                "selection": {k: cand[k] for k in
                              ("searchVolume", "growth30d", "competingProducts",
                               "avgPrice", "returnRate")},
                "queryCn": cand.get("queryCn", ""),
            }
            if query in sourced and sourced[query]["suppliers"]:
                sup = sourced[query]["suppliers"][0]
                metrics["sourcing"] = {
                    "price_cny": sup["price"], "sold_count": sup["soldCount"],
                    "offer_id": sup["url"].split("/offer/")[-1].split(".")[0],
                    "image_url": sup["imageUrl"],
                }
            if query in specs:
                spec = json.load(open(specs[query], encoding="utf-8"))
                metrics["spec_path"] = str(specs[query].relative_to(ROOT))
                metrics["need_confirm"] = bool(spec.get("need_confirm"))
                metrics["spec_validation"] = spec["_meta"].get("validation")
                metrics["ozon_title"] = spec.get("ozon_title", "")
                metrics["description_ru"] = spec.get("description_ru", "")
            # 生图资产目录（存在即视为 imaging 完成）
            offer_id = metrics.get("sourcing", {}).get("offer_id", "")
            want = {GEN_DIR_ALIASES.get(query, ""), offer_id}
            gen_dir = next((d for d in (DATA / "gen").iterdir()
                            if d.is_dir() and d.name in want), None)
            if gen_dir:
                metrics["assets_dir"] = str(gen_dir.relative_to(ROOT))
                metrics["images"] = sorted(
                    p.name for p in gen_dir.glob("slot[0-9].png"))

            if not row:
                cur = c.execute(
                    "INSERT INTO products(query,ozon_id,state,metrics_json,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (query, top["ozonId"], "discovered",
                     json.dumps(metrics, ensure_ascii=False),
                     P.now(), P.now()))
                pid = cur.lastrowid
                P._event(c, pid, None, "discovered", "sync 回填")
                n_new += 1
                state = "discovered"
            else:
                pid, state = row["id"], row["state"]
                c.execute("UPDATE products SET metrics_json=?, updated_at=?"
                          " WHERE id=?",
                          (json.dumps(metrics, ensure_ascii=False), P.now(), pid))

            # 按 artifacts 推进到应有状态（不回头；跳闸在 events 里明示）
            def to(target, note):
                nonlocal state
                if P.TRANSITIONS.get(state, []) and target in P.TRANSITIONS[state]:
                    _advance(c, pid, state, target, note)
                    state = target

            to("scored", f"六维漏斗 {metrics['selection']}")
            if "sourcing" in metrics:
                to("sourced", "1688 图搜命中")
                to("checked", "sync 跳过：trend worker 未实现（任务3）")
                to("profitable", "sync 跳过：econ worker 未实现（任务4）")
            if "assets_dir" in metrics:
                to("imaging", "套图已生成（含 text_overlay）")
                to("review_pending", "sync：素材齐备，进图审队列")

    print(f"新增 {n_new}，候选总数 {len(candidates)} -> {P.DB}")


if __name__ == "__main__":
    P.cmd_init()
    sync()
    P.cmd_list()
