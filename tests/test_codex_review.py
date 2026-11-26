#!/usr/bin/env python3
"""nagare_codex_review.py と nagare_status.py のテスト（標準ライブラリのみ）。偽の codex を使う。

  python3 tests/test_codex_review.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW = os.path.join(ROOT, "templates", ".claude", "hooks", "nagare_codex_review.py")
STATUS = os.path.join(ROOT, "templates", "nagare", "tools", "nagare_status.py")
INSTALL = os.path.join(ROOT, "scripts", "install.py")

ANSWER = {"verdict": "NEEDS_REVISION", "summary": "幅の端の扱いに抜けがある",
          "findings": [{"severity": "P1", "file": "rtl/f.sv", "line": 42, "rule_id": None,
                        "issue": "DEPTH=1 でポインタが0ビット", "evidence": "$clog2(1)=0"},
                       {"severity": "P3", "file": "rtl/f.sv", "line": 18, "rule_id": "R-NAME-08",
                        "issue": "名前の接尾辞 | が違う", "evidence": "規約"}]}


def fm(**kv) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in kv.items()) + "---\n本文\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        self.bin = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.proj, "nagare", "04_impl"))
        subprocess.run(["git", "init", "-q", self.proj], check=True)

    def tearDown(self):
        shutil.rmtree(self.proj)
        shutil.rmtree(self.bin)

    def fake_codex(self, answer: str, code: int = 0) -> str:
        """引数の -o の次のパスに answer を書く偽の codex を作る。"""
        p = os.path.join(self.bin, "codex")
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(f"""\
                #!{sys.executable}
                import sys
                a = sys.argv[1:]
                assert a[0] == "exec" and "--sandbox" in a and a[a.index("--sandbox") + 1] == "read-only"
                with open(a[a.index("-o") + 1], "w", encoding="utf-8") as f:
                    f.write({answer!r})
                sys.exit({code})
                """))
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        return p

    def write(self, rel: str, text: str) -> str:
        p = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def hook(self, path: str, codex: str | None, tool: str = "Write"):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj, NAGARE_CODEX=codex or os.path.join(self.bin, "none"))
        return subprocess.run([sys.executable, REVIEW], input=json.dumps({"tool_name": tool, "tool_input": {"file_path": path}}),
                              capture_output=True, text=True, env=env)

    def crv(self, impl_id: str = "IMPL-01") -> str:
        p = os.path.join(self.proj, "nagare", "045_review", f"CRV-{impl_id}.md")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def status(self) -> dict:
        r = subprocess.run([sys.executable, STATUS, self.proj, "--json"], capture_output=True, text=True, check=True)
        return json.loads(r.stdout)


class ReviewHookTest(Base):
    def test_done_impl_gets_review(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done", prev="PLAN-001"))
        r = self.hook(p, self.fake_codex(json.dumps(ANSWER, ensure_ascii=False)))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.crv()
        self.assertIn("status: needs_revision", text)
        self.assertIn("prev: IMPL-01", text)
        self.assertIn("| F1 | P1 | rtl/f.sv:42 |", text)
        self.assertIn("R-NAME-08", text)
        self.assertIn("接尾辞 \\| が違う", text)  # 表の中の | はエスケープする

    def test_draft_impl_is_ignored(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="draft"))
        self.hook(p, self.fake_codex(json.dumps(ANSWER)))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "nagare", "045_review")))

    def test_other_file_is_ignored(self):
        p = self.write("nagare/02_spec/SPEC-001.md", fm(id="SPEC-001", status="done"))
        self.hook(p, self.fake_codex(json.dumps(ANSWER)))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "nagare", "045_review")))

    def test_runs_only_once(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.hook(p, self.fake_codex(json.dumps(ANSWER)))
        self.write("nagare/045_review/CRV-IMPL-01.md", "人が振り分けた\n")
        self.hook(p, self.fake_codex(json.dumps({"verdict": "PASS", "findings": []})))
        self.assertEqual(self.crv(), "人が振り分けた\n")

    def test_missing_codex_is_blocked(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.hook(p, None)
        self.assertIn("status: blocked", self.crv())
        self.assertIn("見つからない", self.crv())

    def test_unreadable_answer_is_blocked(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.hook(p, self.fake_codex("よさそうです"))
        self.assertIn("status: blocked", self.crv())

    def test_bad_verdict_is_blocked(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.hook(p, self.fake_codex(json.dumps({"verdict": "LGTM", "findings": []})))
        self.assertIn("status: blocked", self.crv())

    def test_codex_error_is_blocked(self):
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.hook(p, self.fake_codex("{}", code=3))
        self.assertIn("終了コード 3", self.crv())


class StatusTest(Base):
    def approve_upstream(self, vplan: bool = True):
        self.write("nagare/01_intent/INT-001.md", fm(id="INT-001", status="approved"))
        self.write("nagare/02_spec/SPEC-001.md", fm(id="SPEC-001", status="approved"))
        self.write("nagare/03_plan/PLAN-001.md", fm(id="PLAN-001", status="approved"))
        if vplan:
            self.write("nagare/25_vplan/VPLAN-001.md", fm(id="VPLAN-001", status="approved"))

    def test_empty_project_starts_intent(self):
        self.assertIn("nagare-intent", self.status()["next"][0])

    def test_waits_for_human_approval(self):
        self.write("nagare/01_intent/INT-001.md", fm(id="INT-001", status="draft"))
        s = self.status()
        self.assertEqual(s["next"], [])
        self.assertIn("INT-001", s["wait"][0])

    def test_wip_limit(self):
        self.approve_upstream()
        for n in range(5):
            self.write(f"nagare/04_impl/IMPL-0{n}.md", fm(id=f"IMPL-0{n}", status="draft"))
        self.assertTrue(any("上限 4" in w for w in self.status()["warn"]))

    def test_review_flow(self):
        self.approve_upstream()
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.assertTrue(any("nagare_codex_review.py" in n for n in self.status()["next"]))
        self.hook(p, self.fake_codex(json.dumps(ANSWER, ensure_ascii=False)))
        self.assertTrue(any("振り分け" in w for w in self.status()["wait"]))
        text = self.crv().replace("| 規約 |  |  |", "| 規約 | 採らない | 例外 |")
        text = text.replace("$clog2(1)=0 |  |  |", "$clog2(1)=0 | 仕様に戻す | DEPTH≥2 |")
        self.write("nagare/045_review/CRV-IMPL-01.md", text)
        s = self.status()
        self.assertTrue(any("② に戻す" in n for n in s["next"]), s)
        self.assertEqual(s["adoption"], {"total": 2, "adopted": 1, "alarm": False})

    def test_pass_goes_to_verify(self):
        self.approve_upstream()
        p = self.write("nagare/04_impl/IMPL-01.md", fm(id="IMPL-01", status="done"))
        self.hook(p, self.fake_codex(json.dumps({"verdict": "PASS", "summary": "", "findings": []})))
        self.assertTrue(any("nagare-verify" in n for n in self.status()["next"]))

    def test_d29_alarm(self):
        rows = "\n".join(f"| F{i} | P3 | a | — | x | y | 採らない | z |" for i in range(1, 10))
        rows += "\n| F10 | P1 | a | — | x | y | 採る | z |"
        self.write("nagare/045_review/CRV-IMPL-01.md", fm(id="CRV-IMPL-01", status="pass_with_warnings", prev="IMPL-01") + rows + "\n")
        s = self.status()
        self.assertEqual(s["adoption"], {"total": 10, "adopted": 1, "alarm": True})
        self.assertTrue(any("D29" in w for w in s["warn"]))


class InstallTest(unittest.TestCase):
    def test_install_adds_review_parts(self):
        proj = tempfile.mkdtemp()
        try:
            subprocess.run([sys.executable, INSTALL, proj], capture_output=True, text=True, check=True)
            for rel in (".claude/hooks/nagare_codex_review.py", "nagare/tools/nagare_status.py",
                        "AGENTS.md", "nagare/045_review", "nagare/formats/4.5_レビュー記録.md"):
                self.assertTrue(os.path.exists(os.path.join(proj, rel)), rel)
            with open(os.path.join(proj, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("自分の規則\n")
            subprocess.run([sys.executable, INSTALL, proj], capture_output=True, text=True, check=True)
            with open(os.path.join(proj, "AGENTS.md"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "自分の規則\n")
        finally:
            shutil.rmtree(proj)


if __name__ == "__main__":
    unittest.main(verbosity=1)
