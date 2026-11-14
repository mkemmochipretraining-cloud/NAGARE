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


    # --- 論点 f（D23 の A）：承認の印は人だけが書く ---
    def put(self, rel, text):
        full = self.p(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        return full

    DRAFT = "---\nid: SPEC-001\nstage: 2-spec\nstatus: draft    # draft / approved\napproved_by:\n---\n\n本文\n"

    def test_ai_cannot_write_approved(self):
        f = self.p("nagare/02_spec/SPEC-001.md")
        self.assertEqual(self.run_guard("nagare-spec", "Write", file_path=f, content=self.DRAFT), 0)
        approved = self.DRAFT.replace("status: draft ", "status: approved ")
        self.assertEqual(self.run_guard("nagare-spec", "Write", file_path=f, content=approved), 2)

    def test_ai_cannot_edit_status_or_approver(self):
        f = self.put("nagare/02_spec/SPEC-001.md", self.DRAFT)
        self.assertEqual(self.run_guard("nagare-spec", "Edit", file_path=f,
                                        old_string="status: draft", new_string="status: approved"), 2)
        self.assertEqual(self.run_guard("nagare-spec", "Edit", file_path=f,
                                        old_string="approved_by:", new_string="approved_by: masa"), 2)
        self.assertEqual(self.run_guard("nagare-spec", "Edit", file_path=f,
                                        old_string="本文", new_string="本文を直した"), 0)

    def test_body_edit_after_human_approval_is_allowed(self):
        f = self.put("nagare/02_spec/SPEC-001.md",
                     self.DRAFT.replace("status: draft ", "status: approved ").replace("approved_by:", "approved_by: masa"))
        self.assertEqual(self.run_guard("nagare-review", "Edit", file_path=self.p("nagare/06_review/x.md"),
                                        old_string="a", new_string="b"), 0)
        self.assertEqual(self.run_guard("nagare-spec", "Edit", file_path=f,
                                        old_string="本文", new_string="本文2"), 0)

    def test_launch_decision_is_human_only(self):
        f = self.put("nagare/07_launch/LAUNCH-001.md",
                     "---\nid: LAUNCH-001\nstage: 7-launch\nstatus: draft\ndecided_by:\n---\n")
        self.assertEqual(self.run_guard("nagare-launch", "Edit", file_path=f,
                                        old_string="status: draft", new_string="status: go"), 2)
        self.assertEqual(self.run_guard("nagare-launch", "Edit", file_path=f,
                                        old_string="decided_by:", new_string="decided_by: masa"), 2)

    def test_verify_can_set_pass_fail(self):
        f = self.put("nagare/05_verify/VRES-01.md", "---\nid: VRES-01\nstage: 5-verify\nstatus: draft\n---\n")
        self.assertEqual(self.run_guard("nagare-verify", "Edit", file_path=f,
                                        old_string="status: draft", new_string="status: fail"), 0)


class SignedTagTest(unittest.TestCase):
    """D23 の C：人の鍵で署名したタグだけを承認とみなす。"""
    CHECK = os.path.join(ROOT, "templates", ".claude", "hooks", "nagare_check_signed.py")

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        g = lambda *a: subprocess.run(["git", "-C", self.repo, *a], check=True, capture_output=True)
        g("init", "-q")
        g("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "init")
        self.g = g

    def tearDown(self):
        shutil.rmtree(self.repo)

    def check(self, id_):
        return subprocess.run([sys.executable, self.CHECK, id_], cwd=self.repo, capture_output=True, text=True)

    def test_missing_or_unsigned_tag_fails(self):
        self.assertEqual(self.check("LAUNCH-001").returncode, 1)
        self.g("-c", "user.name=t", "-c", "user.email=t@e", "tag", "-a", "approve/LAUNCH-001", "-m", "go")
        self.assertEqual(self.check("LAUNCH-001").returncode, 1)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen がない")
    def test_ssh_signed_tag_passes(self):
        key = os.path.join(self.repo, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        pub = open(key + ".pub").read().strip()
        allowed = os.path.join(self.repo, "allowed")
        with open(allowed, "w") as f:
            f.write("t@e " + pub + "\n")
        for k, v in (("gpg.format", "ssh"), ("gpg.ssh.program", "ssh-keygen"), ("user.signingkey", key + ".pub"),
                     ("gpg.ssh.allowedSignersFile", allowed), ("user.name", "t"), ("user.email", "t@e")):
            self.g("config", k, v)
        self.g("tag", "-s", "approve/LAUNCH-001", "-m", "go")
        r = self.check("LAUNCH-001")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "go")


if __name__ == "__main__":
    unittest.main(verbosity=1)
