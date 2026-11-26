#!/usr/bin/env python3
"""NAGARE の段の状態を見張る、最小のオーケストレーター（Phase 1a。標準ライブラリのみ）。

  python3 nagare/tools/nagare_status.py [プロジェクトのフォルダ] [--json]

nagare/ の下の書式の frontmatter（id・status・prev）を読み、次の4つを表示する:
  1. 段ごとの書式と状態
  2. 次に進められること（どのエージェントを起動するか）と、人を待っていること
  3. 同時に進める作業包の上限（4個。P8）を超えていないか
  4. D29 の物差し：Codex の指摘のうち、採用したものの割合（10件中2件に満たなければ考え直す）

エージェントは起動しない。起動は人が `claude --agent <名前>` で行う（D23・D30 の Phase 0）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

WIP_LIMIT = 4
ADOPT = ("採る", "仕様に戻す")
DISPOSITIONS = ("採る", "採らない", "仕様に戻す")
STAGES = [("01_intent", "① 意図"), ("02_spec", "② 仕様"), ("03_plan", "③ 計画"),
          ("25_vplan", "2.5 検証項目"), ("04_impl", "④ 実装"), ("045_review", "4.5 レビュー"),
          ("05_verify", "⑤ 検証"), ("06_review", "⑥ 記録・考察"), ("07_launch", "⑦ ローンチ")]


def front(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ":" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split(":", 1)
            out[k.strip()] = v.split("#", 1)[0].strip()
    return out


def findings(text: str) -> list[dict]:
    """レビュー記録の指摘の表から、ID・重さ・振り分けを取り出す。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("| F"):
            continue
        cells = [c.strip() for c in s.replace("\\|", "\0").split("|")[1:-1]]
        if len(cells) < 8:
            continue
        out.append({"id": cells[0], "severity": cells[1], "disposition": cells[6].replace("\0", "|")})
    return out


def load(root: str) -> dict[str, list[dict]]:
    docs: dict[str, list[dict]] = {}
    for d, _ in STAGES:
        items = []
        for p in sorted(glob.glob(os.path.join(root, "nagare", d, "*.md"))):
            with open(p, encoding="utf-8") as f:
                text = f.read()
            fm = front(text)
            if not fm:
                continue
            item = {"id": fm.get("id") or os.path.splitext(os.path.basename(p))[0],
                    "status": fm.get("status", ""), "prev": fm.get("prev", ""),
                    "path": os.path.relpath(p, root).replace("\\", "/")}
            if d == "045_review":
                item["findings"] = findings(text)
            items.append(item)
        docs[d] = items
    return docs


def approved(items: list[dict]) -> bool:
    return bool(items) and all(i["status"] == "approved" for i in items)


def plan(docs: dict[str, list[dict]]) -> dict:
    nxt, wait, warn = [], [], []
    chain = [("01_intent", "nagare-intent", "①"), ("02_spec", "nagare-spec", "②"), ("03_plan", "nagare-plan", "③")]
    for d, agent, name in chain:
        items = docs[d]
        if not items:
            nxt.append(f"{name} を始める：claude --agent {agent}")
            return {"next": nxt, "wait": wait, "warn": warn}
        if not approved(items):
            wait.append(f"{name} の承認（人が status を approved にする）：" + ", ".join(i["id"] for i in items if i["status"] != "approved"))
            return {"next": nxt, "wait": wait, "warn": warn}

    vplan = docs["25_vplan"]
    if not vplan:
        nxt.append("2.5 を始める：claude --agent nagare-vplan")
    elif not approved(vplan):
        wait.append("2.5 の承認（人）：" + ", ".join(i["id"] for i in vplan if i["status"] != "approved"))

    impls = docs["04_impl"]
    open_impls = [i for i in impls if i["status"] not in ("done", "stopped")]
    if not impls:
        nxt.append("④ を始める：claude --agent nagare-impl")
    if len(open_impls) > WIP_LIMIT:
        warn.append(f"進行中の作業包が {len(open_impls)} 個で、上限 {WIP_LIMIT} を超えている")
    for i in impls:
        if i["status"] == "stopped":
            wait.append(f"止まった作業包の判断（人）：{i['id']}")

    reviews = {r["prev"]: r for r in docs["045_review"]}
    verified = {v["prev"] for v in docs["05_verify"] if v["status"] == "pass"}
    for i in impls:
        if i["status"] != "done":
            continue
        r = reviews.get(i["id"])
        if r is None:
            nxt.append(f"Codex のレビュー：python3 .claude/hooks/nagare_codex_review.py --impl {i['path']}")
            continue
        if r["status"] == "blocked":
            wait.append(f"レビューできなかった原因を見る（人）：{r['id']}")
            continue
        empty = [f["id"] for f in r["findings"] if f["disposition"] not in DISPOSITIONS]
        if empty:
            wait.append(f"指摘の振り分け（人）：{r['id']} の " + ", ".join(empty))
            continue
        disp = [f["disposition"] for f in r["findings"]]
        if "仕様に戻す" in disp:
            nxt.append(f"② に戻す（仕様の抜け）：{r['id']}")
        elif "採る" in disp or r["status"] == "needs_revision":
            nxt.append(f"④ に戻して直す：{i['id']}（{r['id']} の「採る」{disp.count('採る')} 件）")
        elif i["id"] not in verified:
            if approved(vplan):
                nxt.append(f"⑤ 検証：claude --agent nagare-verify（{i['id']}）")
            else:
                wait.append(f"⑤ に進むには 2.5 の承認が要る：{i['id']}")

    if impls and all(i["id"] in verified for i in impls) and not docs["06_review"]:
        nxt.append("⑥ を始める：claude --agent nagare-review")
    if approved(docs["06_review"]) and not docs["07_launch"]:
        nxt.append("⑦ を始める：claude --agent nagare-launch")
    return {"next": nxt, "wait": wait, "warn": warn}


def adoption(docs: dict[str, list[dict]]) -> dict:
    disp = [f["disposition"] for r in docs["045_review"] for f in r["findings"] if f["disposition"] in DISPOSITIONS]
    total, adopted = len(disp), sum(d in ADOPT for d in disp)
    alarm = total >= 10 and adopted * 5 < total
    return {"total": total, "adopted": adopted, "alarm": alarm}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.project)
    if not os.path.isdir(os.path.join(root, "nagare")):
        sys.stderr.write(f"nagare/ がありません：{root}\n")
        return 1
    docs = load(root)
    p, ad = plan(docs), adoption(docs)
    if ad["alarm"]:
        p["warn"].append(f"D29：採用した指摘が {ad['adopted']}/{ad['total']} 件で、10件中2件に満たない。レビューを置くか考え直す")
    if a.json:
        print(json.dumps({"stages": docs, **p, "adoption": ad}, ensure_ascii=False, indent=2))
        return 0
    print("## 段の状態")
    for d, name in STAGES:
        items = docs[d]
        print(f"- {name}：" + (", ".join(f"{i['id']}({i['status'] or '-'})" for i in items) if items else "なし"))
    for title, key in (("## 次に進められること", "next"), ("## 人を待っていること", "wait"), ("## 注意", "warn")):
        print(title)
        print("\n".join(f"- {x}" for x in p[key]) if p[key] else "- なし")
    print(f"## D29 の物差し\n- 採用した指摘：{ad['adopted']}/{ad['total']} 件（採る・仕様に戻す）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
