#!/usr/bin/env python3
"""④実装が終わった作業包を、Codex にレビューさせて記録を書く（D29。標準ライブラリのみ）。

hook として（nagare-impl の PostToolUse）:
  標準入力に Claude Code の hook の JSON が来る。書いたファイルが nagare/04_impl/IMPL-*.md で、
  status が done になっていて、まだレビュー記録がなければ、Codex を1回だけ呼ぶ。

手で動かすとき:
  python3 .claude/hooks/nagare_codex_review.py --impl nagare/04_impl/IMPL-01.md

書くもの: nagare/045_review/CRV-<IMPL の id>.md（書式は nagare/formats/4.5_レビュー記録.md）
環境変数:
  NAGARE_CODEX           呼ぶ codex のコマンド（既定 codex。テストでは偽物に差し替える）
  NAGARE_REVIEW_TIMEOUT  Codex を待つ秒数（既定 840）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

VERDICTS = ("PASS", "PASS_WITH_WARNINGS", "NEEDS_REVISION", "BLOCKED")
SEVERITIES = ("P0", "P1", "P2", "P3")
REVIEW_DIR = os.path.join("nagare", "045_review")

PROMPT = """あなたは NAGARE の独立したレビュー役です。コードを書いた Claude とは別のモデルとして、次の作業包の変更をレビューしてください。ファイルは変更しないでください。

対象の実装報告: {impl}
- 実装報告の「変更の一覧」に書かれたファイルを読み、git の差分（git diff、git log）で変更を確かめてください
- 仕様（実装報告の prev からたどれる nagare/02_spec と nagare/03_plan）と照らしてください
- AGENTS.md の「Code Review Rules」に従ってください。規約の違反はルール ID を書いてください
- Lint が拾える書き方の違反、個人の好み、根拠のない推測、今回の変更で触っていない既存の問題は指摘しないでください

答えは次の JSON だけを返してください（前後に文を付けない）:
{{"verdict": "PASS | PASS_WITH_WARNINGS | NEEDS_REVISION | BLOCKED",
  "summary": "日本語で3行以内",
  "findings": [{{"severity": "P0|P1|P2|P3", "file": "パス", "line": 行番号か null,
                "rule_id": "ルール ID か null", "issue": "日本語で1文", "evidence": "根拠を1文"}}]}}
重さ: P0 動かない・仕様に反する／P1 高い確率で不具合／P2 Lint が拾わない規約違反／P3 読みやすさ
"""


def project_dir() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


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


def is_impl(path: str, root: str) -> bool:
    r = os.path.relpath(os.path.abspath(path), root).replace("\\", "/")
    return bool(re.fullmatch(r"nagare/04_impl/IMPL-[^/]+\.md", r))


def git_commit(root: str) -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "-"
    except (OSError, subprocess.SubprocessError):
        return "-"


def parse_answer(text: str) -> dict:
    """Codex の最後の返事から JSON を取り出して確かめる。読めなければ ValueError。"""
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("JSON が見つからない")
    data = json.loads(text[s:e + 1])
    v = str(data.get("verdict", "")).upper()
    if v not in VERDICTS:
        raise ValueError(f"verdict が決まりの値でない：{v!r}")
    out = []
    for f in data.get("findings") or []:
        sev = str(f.get("severity", "")).upper()
        if sev not in SEVERITIES:
            raise ValueError(f"severity が決まりの値でない：{sev!r}")
        out.append({"severity": sev, "file": str(f.get("file") or "-"),
                    "line": f.get("line"), "rule_id": f.get("rule_id") or "—",
                    "issue": str(f.get("issue") or ""), "evidence": str(f.get("evidence") or "")})
    return {"verdict": v, "summary": str(data.get("summary") or ""), "findings": out}


def run_codex(root: str, impl_rel: str) -> dict:
    cmd = os.environ.get("NAGARE_CODEX", "codex")
    if not shutil.which(cmd):
        return {"verdict": "BLOCKED", "summary": f"Codex のコマンド（{cmd}）が見つからない。Codex CLI を入れてから、手で動かし直す。", "findings": []}
    timeout = int(os.environ.get("NAGARE_REVIEW_TIMEOUT", "840"))
    fd, out = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        r = subprocess.run([cmd, "exec", "--sandbox", "read-only", "-o", out,
                            PROMPT.format(impl=impl_rel)],
                           cwd=root, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"verdict": "BLOCKED", "summary": f"Codex が終了コード {r.returncode} で止まった：{r.stderr.strip()[-300:]}", "findings": []}
        with open(out, encoding="utf-8") as f:
            return parse_answer(f.read())
    except subprocess.TimeoutExpired:
        return {"verdict": "BLOCKED", "summary": f"Codex が {timeout} 秒で終わらなかった。", "findings": []}
    except (ValueError, json.JSONDecodeError) as e:
        return {"verdict": "BLOCKED", "summary": f"Codex の返事が読めなかった：{e}", "findings": []}
    finally:
        os.remove(out)


def cell(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render(impl_id: str, res: dict, commit: str) -> str:
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    rows = []
    for i, f in enumerate(res["findings"], 1):
        where = f["file"] + (f":{f['line']}" if f["line"] not in (None, "") else "")
        rows.append(f"| F{i} | {f['severity']} | {cell(where)} | {cell(f['rule_id'])} | {cell(f['issue'])} | {cell(f['evidence'])} |  |  |")
    table = "\n".join(rows) if rows else "| — | — | — | — | 指摘なし | — | — | — |"
    return f"""---
id: CRV-{impl_id}
stage: 4.5-review
version: 1
status: {res['verdict'].lower()}
prev: {impl_id}
written_by: codex
reviewer: codex exec（読むだけの砂場）
reviewed_at: {now}
git_commit: {commit}
---

## 1. 指摘
| ID | 重さ | 場所 | ルール ID | 指摘 | 根拠 | 振り分け（人） | 理由（人） |
|---|---|---|---|---|---|---|---|
{table}

振り分けは「採る」「採らない」「仕様に戻す」のどれか。空欄のあいだは次の段に進めない。

## 2. Codex の要約
（AI が作成）{cell(res['summary'])}

## 3. 出口の条件
- [ ] すべての指摘の「振り分け（人）」が埋まっている
- [ ] status が pass か pass_with_warnings なら⑤へ、needs_revision なら④か②へ、blocked なら人が原因を見る
"""


def review(root: str, impl_path: str, force: bool = False) -> str | None:
    full = os.path.abspath(impl_path)
    with open(full, encoding="utf-8") as f:
        fm = front(f.read())
    impl_id = fm.get("id") or os.path.splitext(os.path.basename(full))[0]
    dst = os.path.join(root, REVIEW_DIR, f"CRV-{impl_id}.md")
    if os.path.exists(dst) and not force:
        return None
    res = run_codex(root, os.path.relpath(full, root).replace("\\", "/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(render(impl_id, res, git_commit(root)))
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", help="手で動かすときの実装報告のパス")
    ap.add_argument("--force", action="store_true", help="レビュー記録があっても作り直す")
    a = ap.parse_args()
    root = project_dir()
    if a.impl:
        dst = review(root, a.impl, a.force)
        print(dst or "レビュー記録はもうあります（作り直すときは --force）")
        return 0
    try:
        ev = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if ev.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        return 0
    path = (ev.get("tool_input") or {}).get("file_path", "")
    if not path:
        return 0
    if not os.path.isabs(path):
        path = os.path.join(root, path)
    if not is_impl(path, root) or not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        if front(f.read()).get("status") != "done":
            return 0
    dst = review(root, path)
    if dst:
        sys.stderr.write(f"Codex のレビュー記録を書きました：{os.path.relpath(dst, root)}。指摘の振り分けは人が行います。次の段には、振り分けが済むまで進みません。\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
