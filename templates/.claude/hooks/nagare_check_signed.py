#!/usr/bin/env python3
"""取り返しのつかない段の承認を、人の鍵で署名した git のタグで確かめる（論点 f・D23 の C）。

  python3 .claude/hooks/nagare_check_signed.py LAUNCH-001

- 承認のタグ名は approve/<書式ID>（例：approve/LAUNCH-001）
- 人は自分の鍵で `git tag -s approve/LAUNCH-001 -m "go"` を作る。鍵はAIの作業環境に置かない
- このプログラムは `git tag -v` で署名を確かめ、合格なら 0、タグがない・署名がない・確かめられないなら 1 を返す
- 合格したときは、タグのメッセージ（go／conditional／no-go）を標準出力に出す
標準ライブラリだけを使う。
"""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("使い方：nagare_check_signed.py <書式ID>\n")
        return 1
    tag = f"approve/{sys.argv[1]}"
    r = subprocess.run(["git", "tag", "-v", tag], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"{tag} の署名を確かめられません（タグがない、署名がない、または鍵が信頼されていない）。\n"
                         "人が自分の鍵で署名したタグを作るまで、この段は進めません。\n")
        return 1
    msg = subprocess.run(["git", "tag", "-l", "--format=%(contents:subject)", tag],
                         capture_output=True, text=True).stdout.strip()
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
