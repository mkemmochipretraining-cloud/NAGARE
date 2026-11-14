#!/usr/bin/env python3
"""nagare_access_guard.py の単体テスト（標準ライブラリのみ）。

  python3 tests/test_access_guard.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "templates", ".claude", "hooks", "nagare_access_guard.py")
ACCESS = os.path.join(ROOT, "templates", "nagare", "access.json")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.proj, "nagare"))
        shutil.copy(ACCESS, os.path.join(self.proj, "nagare", "access.json"))
        self.write_permits([])

    def tearDown(self):
        shutil.rmtree(self.proj)

    def write_permits(self, permits):
        with open(os.path.join(self.proj, "nagare", "permits.json"), "w", encoding="utf-8") as f:
            json.dump(permits, f)

    def run_guard(self, agent, tool, **ti):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj)
        r = subprocess.run(
            [sys.executable, GUARD, agent],
            input=json.dumps({"tool_name": tool, "tool_input": ti}),
            capture_output=True, text=True, env=env,
        )
        return r.returncode

    def p(self, rel):
        return os.path.join(self.proj, rel)

    # 2.5：仕様・計画・ポート一覧は読める、設計物は許可がないと読めない（D6・D14）
    def test_vplan_reads_spec_and_ports(self):
        self.assertEqual(self.run_guard("nagare-vplan", "Read", file_path=self.p("nagare/02_spec/SPEC-001.md")), 0)
        self.assertEqual(self.run_guard("nagare-vplan", "Read", file_path=self.p("nagare/ports/top.md")), 0)

    def test_vplan_cannot_read_design_without_permit(self):
        self.assertEqual(self.run_guard("nagare-vplan", "Read", file_path=self.p("rtl/top.sv")), 2)
        self.assertEqual(self.run_guard("nagare-vplan", "Grep", pattern="module"), 2)

    def test_vplan_reads_design_with_permit(self):
        self.write_permits([{"agent": "nagare-vplan", "path": "rtl/top.sv", "until": "2099-01-01T00:00:00+00:00",
                             "reason": "ポートの確認", "granted_by": "masa"}])
        self.assertEqual(self.run_guard("nagare-vplan", "Read", file_path=self.p("rtl/top.sv")), 0)
        self.assertEqual(self.run_guard("nagare-vplan", "Read", file_path=self.p("rtl/other.sv")), 2)

    def test_expired_or_unsigned_permit_is_ignored(self):
        self.write_permits([
            {"agent": "nagare-vplan", "path": "rtl", "until": "2000-01-01T00:00:00+00:00", "granted_by": "masa"},
            {"agent": "nagare-vplan", "path": "rtl", "until": "2099-01-01T00:00:00+00:00", "granted_by": ""},
        ])
        self.assertEqual(self.run_guard("nagare-vplan", "Read", file_path=self.p("rtl/top.sv")), 2)

    # ③〜⑥は設計物をいつでも読める（D7）
    def test_plan_impl_verify_review_read_design(self):
        for a in ("nagare-plan", "nagare-impl", "nagare-verify", "nagare-review"):
            self.assertEqual(self.run_guard(a, "Read", file_path=self.p("rtl/top.sv")), 0, a)

    # ①②⑦は許可制（D7・D8）
    def test_intent_spec_launch_need_permit(self):
        for a in ("nagare-intent", "nagare-spec", "nagare-launch"):
            self.assertEqual(self.run_guard(a, "Read", file_path=self.p("rtl/top.sv")), 2, a)

    # ④はテストのコードを読めない・書けない（D13）
    def test_impl_cannot_touch_tests(self):
        self.assertEqual(self.run_guard("nagare-impl", "Read", file_path=self.p("nagare/25_tests/test_top.py")), 2)
        self.assertEqual(self.run_guard("nagare-impl", "Write", file_path=self.p("nagare/25_tests/test_top.py")), 2)
        self.assertEqual(self.run_guard("nagare-impl", "Write", file_path=self.p("rtl/top.sv")), 0)

    # 許可と設定のファイルは、どのエージェントも書けない
    def test_nobody_writes_permits(self):
        self.assertEqual(self.run_guard("nagare-review", "Write", file_path=self.p("nagare/permits.json")), 2)
        self.assertEqual(self.run_guard("nagare-review", "Edit", file_path=self.p("nagare/access.json")), 2)

    # 書ける場所は自分の段だけ
    def test_write_only_own_stage(self):
        self.assertEqual(self.run_guard("nagare-spec", "Write", file_path=self.p("nagare/02_spec/SPEC-001.md")), 0)
        self.assertEqual(self.run_guard("nagare-spec", "Write", file_path=self.p("nagare/03_plan/PLAN-001.md")), 2)

    # プロジェクトの外と、知らないエージェントは止める
    def test_outside_and_unknown(self):
        self.assertEqual(self.run_guard("nagare-review", "Read", file_path="/etc/passwd"), 2)
        self.assertEqual(self.run_guard("someone", "Read", file_path=self.p("nagare/02_spec/a.md")), 2)

    # 対象外の道具は通す
    def test_other_tools_pass(self):
        self.assertEqual(self.run_guard("nagare-vplan", "WebSearch", query="riscv-arch-test"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
