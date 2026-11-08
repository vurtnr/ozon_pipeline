#!/usr/bin/env python3
"""拉取 Ozon 类目树并展开为末级类目表（供 Nexscope 类目热销接口使用）。

用法:
    OZON_CLIENT_ID=xxx OZON_API_KEY=xxx python3 category_tree.py
输出:
    data/category-tree-ru.json   原始树（俄语，Nexscope 需要俄语路径）
    data/categories-leaf.json    末级类目 [{categoryId, pathRu, depth}]
"""
import json
import os
import urllib.request

API = "https://api-seller.ozon.ru/v1/description-category/tree"
HEADERS = {
    "Client-Id": os.environ["OZON_CLIENT_ID"],
    "Api-Key": os.environ["OZON_API_KEY"],
    "Content-Type": "application/json",
}


def fetch_tree() -> dict:
    body = json.dumps({"language": "RU"}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def flatten(nodes: list[dict], prefix: list[str], ids: list[str],
            out: list[dict]) -> None:
    for node in nodes:
        # 中间层: description_category_id/category_name；末级: type_id/type_name
        name = node.get("category_name") or node.get("type_name")
        nid = str(node.get("description_category_id") or node.get("type_id"))
        path, id_path = prefix + [name], ids + [nid]
        children = node.get("children") or []
        if children:
            flatten(children, path, id_path, out)
        else:
            out.append({
                "categoryId": "_".join(id_path),
                "typeId": node.get("type_id"),
                "pathRu": "/".join(path),
                "disabled": node.get("disabled", False),
                "depth": len(path),
            })


def main() -> None:
    tree = fetch_tree()
    with open("data/category-tree-ru.json", "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False)

    leaves: list[dict] = []
    flatten(tree.get("result") or [], [], [], leaves)
    with open("data/categories-leaf.json", "w", encoding="utf-8") as f:
        json.dump(leaves, f, ensure_ascii=False, indent=1)

    from collections import Counter
    top = Counter(p["pathRu"].split("/")[0] for p in leaves)
    print(f"末级类目 {len(leaves)} 个，一级类目 {len(top)} 个")
    for name, count in top.most_common():
        print(f"  {count:>5}  {name}")


if __name__ == "__main__":
    main()
