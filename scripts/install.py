#!/usr/bin/env python3
"""NAGARE の flow を、使うプロジェクトに入れる（標準ライブラリのみ。Windows でも動く）。

  python3 scripts/install.py <プロジェクトのフォルダ> [--design rtl,src]

入れるもの:
- .claude/agents/nagare-*.md（8つのエージェント）
- .claude/hooks/nagare_access_guard.py、nagare_check_signed.py
- nagare/access.json、nagare/permits.json（すでにあれば上書きしない）
- nagare/formats/（docs/03_書式 の8つの書式）
- nagare/01_intent 〜 07_launch、25_vplan、25_tests、ports の空フォルダ
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(HERE, "templates")
FORMATS = os.path.join(HERE, "docs", "03_書式")
STAGES = ["01_intent", "02_spec", "03_plan", "25_vplan", "25_tests",
          "04_impl", "05_verify", "06_review", "07_launch", "ports"]


def copy_tree(src: str, dst: str) -> list[str]:
    done = []
    for base, _, files in os.walk(src):
        for f in files:
            s = os.path.join(base, f)
            d = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            done.append(os.path.relpath(d, dst))
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--design", default="", help="設計物のフォルダ（カンマ区切り）。access.json の design と ④の書ける場所に入れる")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        sys.stderr.write(f"フォルダがありません：{proj}\n")
        return 1

    copied = [".claude/" + c for c in copy_tree(os.path.join(TPL, ".claude"), os.path.join(proj, ".claude"))]
    nag = os.path.join(proj, "nagare")
    os.makedirs(nag, exist_ok=True)
    for name in ("access.json", "permits.json"):
        dst = os.path.join(nag, name)
        if os.path.exists(dst):
            print(f"そのまま残しました：nagare/{name}")
            continue
        shutil.copy2(os.path.join(TPL, "nagare", name), dst)
        copied.append(f"nagare/{name}")
    if a.design:
        path = os.path.join(nag, "access.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        design = [d.strip().strip("/") for d in a.design.split(",") if d.strip()]
        old = set(cfg.get("design", []))
        cfg["design"] = design
        impl = cfg["agents"]["nagare-impl"]
        impl["write"] = [w for w in impl["write"] if w not in old] + design
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"設計物の場所を {design} にしました")
    fmt = os.path.join(nag, "formats")
    os.makedirs(fmt, exist_ok=True)
    for f in sorted(os.listdir(FORMATS)):
        shutil.copy2(os.path.join(FORMATS, f), os.path.join(fmt, f))
        copied.append(f"nagare/formats/{f}")
    for s in STAGES:
        os.makedirs(os.path.join(nag, s), exist_ok=True)
        keep = os.path.join(nag, s, ".gitkeep")
        if not os.listdir(os.path.join(nag, s)):
            open(keep, "w").close()

    print(f"入れたファイル：{len(copied)} 個")
    for c in copied:
        print("  " + c.replace(os.sep, "/"))
    print("次：claude --version で v2.1.116 以上を確かめ、claude --agent nagare-intent で①を始める")
    return 0


if __name__ == "__main__":
    sys.exit(main())
