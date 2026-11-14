#!/usr/bin/env python3
"""NAGARE のエージェントごとに、読める・書ける場所を守る hook（PreToolUse）。

使い方（エージェント定義の hooks 欄から呼ぶ）:
  python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/nagare_access_guard.py" <エージェント名>

- 設定は nagare/access.json（エージェントごとの read / write の場所）
- 許可制の場所（design）は、nagare/permits.json に人が書いた許可があるときだけ読める
- nagare/permits.json と nagare/access.json は、どのエージェントも書けない（人だけが書く）
- 書式ファイルの承認の印（status: approved／⑦の go・conditional・no-go、approved_by、decided_by）を
  新しく埋める・変える書き込みは止める（論点 f・D23 の A）。承認は人が直接書く
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


HUMAN_FIELDS = ("approved_by", "decided_by")
LAUNCH_DECISIONS = ("go", "conditional", "no-go")


def front(text: str) -> dict:
    """先頭の YAML（--- で囲まれた部分）から key: value を拾う（コメントは除く）。"""
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


def after_write(tool: str, ti: dict, full: str) -> str | None:
    """書き込んだあとのファイルの中身を作る。作れなければ None。"""
    try:
        with open(full, encoding="utf-8") as f:
            cur = f.read()
    except (FileNotFoundError, IsADirectoryError):
        cur = ""
    if tool == "Write":
        return ti.get("content", "")
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    for e in edits or []:
        old, new = e.get("old_string", ""), e.get("new_string", "")
        if old not in cur:
            return None
        cur = cur.replace(old, new) if e.get("replace_all") else cur.replace(old, new, 1)
    return cur


def approval_changed(before: dict, after: dict) -> str | None:
    st_b, st_a = before.get("status", ""), after.get("status", "")
    if st_a != st_b:
        if st_a == "approved":
            return "status を approved に"
        if after.get("stage", "").startswith("7") and st_a in LAUNCH_DECISIONS:
            return f"⑦の判断を {st_a} に"
    for k in HUMAN_FIELDS:
        if after.get(k, "") != before.get(k, "") and after.get(k, ""):
            return f"{k} を"
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
            if p.startswith("nagare/") and p.endswith(".md"):
                full = os.path.join(root, p)
                try:
                    with open(full, encoding="utf-8") as f:
                        before = front(f.read())
                except (FileNotFoundError, IsADirectoryError):
                    before = {}
                text = after_write(data.get("tool_name", ""), data.get("tool_input", {}) or {}, full)
                if text is not None:
                    what = approval_changed(before, front(text))
                    if what:
                        deny(f"{what}変える書き込みは止めました。承認の印は人だけが書きます（D23）。"
                             "承認が必要なら、作業を止めて人に頼んでください。")
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
