#!/usr/bin/env python3
"""NAGARE のエージェントごとに、読める・書ける場所を守る hook（PreToolUse）。

使い方（エージェント定義の hooks 欄から呼ぶ）:
  python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/nagare_access_guard.py" <エージェント名>

- 設定は nagare/access.json（エージェントごとの read / write の場所）
- 許可制の場所（design）は、nagare/permits.json に人が書いた許可があるときだけ読める
- nagare/permits.json と nagare/access.json は、どのエージェントも書けない（人だけが書く）
- 止めるときは終了コード 2 と、理由を標準エラーに出す（Claude Code がエージェントに伝える）
標準ライブラリだけを使う。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

PROTECTED = ("nagare/permits.json", "nagare/access.json")


def project_dir() -> str:
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def rel(path: str, root: str) -> str:
    """プロジェクトからの相対パス（/ 区切り）。外なら '..' で始まる。"""
    if not path:
        return "."
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    return os.path.relpath(full, root).replace(os.sep, "/")


def under(p: str, prefixes: list[str]) -> bool:
    for pre in prefixes:
        pre = pre.rstrip("/")
        if pre in ("", "."):
            return True
        if p == pre or p.startswith(pre + "/"):
            return True
    return False


def load(root: str, name: str, default):
    try:
        with open(os.path.join(root, "nagare", name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def permitted(agent: str, p: str, root: str) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    for e in load(root, "permits.json", []):
        if e.get("agent") != agent or not e.get("granted_by"):
            continue
        until = e.get("until")
        if until:
            try:
                t = dt.datetime.fromisoformat(until)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                if t < now:
                    continue
            except ValueError:
                continue
        if under(p, [e.get("path", "")]):
            return True
    return False


def target(tool: str, ti: dict) -> tuple[str, str] | None:
    """(読む／書く, パス) を返す。対象外の道具は None。"""
    if tool == "Read":
        return "read", ti.get("file_path", "")
    if tool in ("Write", "Edit", "MultiEdit"):
        return "write", ti.get("file_path", "")
    if tool == "NotebookEdit":
        return "write", ti.get("notebook_path", "")
    if tool in ("Grep", "Glob"):
        return "read", ti.get("path", "") or "."
    return None


def deny(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.exit(2)


def main() -> None:
    agent = sys.argv[1] if len(sys.argv) > 1 else ""
    data = json.load(sys.stdin)
    t = target(data.get("tool_name", ""), data.get("tool_input", {}) or {})
    if t is None:
        sys.exit(0)
    mode, path = t
    root = project_dir()
    p = rel(path, root)
    cfg = load(root, "access.json", None)
    if cfg is None:
        deny("nagare/access.json がないため、安全のため止めました。人に設定を頼んでください。")
    a = cfg.get("agents", {}).get(agent)
    if a is None:
        deny(f"エージェント '{agent}' の設定が nagare/access.json にありません。")
    if p.startswith(".."):
        deny(f"プロジェクトの外（{path}）は扱えません。")

    if mode == "write":
        if p in PROTECTED:
            deny(f"{p} は人だけが書けます。許可が必要なら、人に頼んでください。")
        if under(p, a.get("write", [])):
            sys.exit(0)
        deny(f"{agent} は {p} に書けません（書ける場所：{a.get('write', [])}）。")

    # read
    if under(p, a.get("read", [])):
        sys.exit(0)
    design = cfg.get("design", [])
    if under(p, design) or (p == "." and design):
        if a.get("design") == "always":
            sys.exit(0)
        if a.get("design") == "permit" and p != "." and permitted(agent, p, root):
            sys.exit(0)
        deny(
            f"{agent} は設計物（{p}）を許可なしでは読めません。必要なら作業を止め、"
            "読みたい範囲と理由を人に伝えてください。許可は人が nagare/permits.json に書きます。"
        )
    deny(f"{agent} は {p} を読めません（読める場所：{a.get('read', [])}）。")


if __name__ == "__main__":
    main()
