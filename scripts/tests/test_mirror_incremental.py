# -*- coding: utf-8 -*-
"""push_code_mirror 增量推送单测（2026-10-11 用户指定：后续线上同步增量提交）。

覆盖：
  ① 首次推送（镜像还没有该分支）→ 全量重建，之后有新提交 → 增量只重放新提交（远端历史保留、叠加）
  ② 没有新提交 → 不推送、报「已是最新」，远端 tip 不变
  ③ 状态文件丢了也能认出底座（说明+时间对号 + 树校验）
  ④ 镜像历史被别处改写 → 认不出底座 → 回退全量重建（force push，自愈）
  ⑤ `--full` 强制全量重建（走 main() 的真实分发）

用小假仓库（白名单里就 scripts/ + LICENSE）跑真 git 操作，几十毫秒级。
"""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "tools")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import push_code_mirror as PCM  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
           GIT_AUTHOR_DATE="2026-10-11T10:00:00+08:00",
           GIT_COMMITTER_DATE="2026-10-11T10:00:00+08:00")


def git(cwd, *args, **kw):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(ENV, **kw.pop("env", {})))


class TestMirrorIncremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_mirror_inc_test_")
        self.repo = os.path.join(self.tmp, "repo")
        self.remote = os.path.join(self.tmp, "mirror.git")
        os.makedirs(os.path.join(self.repo, "scripts"))
        self._w("scripts/a.py", "print('a')\n")
        self._w("LICENSE", "MIT\n")
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True, env=ENV)
        # 假镜像裸仓库（真实工作流里它先前就存在：本地 ../aa-translation 或 GitHub）
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True, env=ENV)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "第一条提交")
        self._orig_repo = PCM.REPO
        PCM.REPO = self.repo                      # 状态文件 = REPO/temp/… → 自动落在假仓库里
        self.dsts = []

    def tearDown(self):
        PCM.REPO = self._orig_repo
        for d in self.dsts:
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 工具 ----
    def _w(self, rel, text):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def _commit(self, msg, rel="scripts/a.py", text=None):
        self._w(rel, text if text is not None else ("print(%r)\n" % msg))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def _push(self, **kw):
        dst = tempfile.mkdtemp(prefix="aa_mirror_inc_dst_")
        self.dsts.append(dst)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok, head = PCM.push_incremental(dst, self.remote, "main",
                                            PCM.MAX_MB, PCM.HISTORY_DEFAULT, **kw)
        return ok, head, buf.getvalue()

    def _remote_log(self):
        return [x.strip() for x in git(self.remote, "log", "--format=%s", "main").stdout.splitlines() if x.strip()]

    def _remote_tip(self):
        return git(self.remote, "rev-parse", "main").stdout.strip()

    # ---- 用例 ----
    def test_首次全量_然后增量只推新提交(self):
        ok, _h, out = self._push()
        self.assertTrue(ok, out)
        self.assertIn("首次推送", out)
        self.assertEqual(self._remote_log(), ["第一条提交"])
        tip1 = self._remote_tip()

        new_sha = self._commit("第二条提交")
        ok, _h, out = self._push()
        self.assertTrue(ok, out)
        self.assertIn("增量同步：底座", out)
        self.assertIn("要重放 1 条新提交", out)
        self.assertNotIn("回退全量重建", out)
        log = self._remote_log()
        self.assertEqual(log, ["第二条提交", "第一条提交"], f"远端历史应保留并叠加: {log}")
        # 新提交叠在旧 tip 之上（父 = 上一个镜像提交）
        self.assertEqual(git(self.remote, "rev-parse", "main^").stdout.strip(), tip1)
        # 状态文件记了对应关系（下次 0 秒认底座）
        with io.open(os.path.join(self.repo, "temp", "mirror_state.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["%s#main" % self.remote]["local"], new_sha)

        # 再推一次：没有新提交 → 不重放、远端 tip 不动（状态文件命中，秒回）
        ok, head2, out = self._push()
        self.assertTrue(ok)
        self.assertIn("已是最新", out)
        self.assertEqual(head2, self._remote_tip())
        self.assertEqual(self._remote_log(), ["第二条提交", "第一条提交"])

    def test_状态文件丢了也能认出底座(self):
        self.assertTrue(self._push()[0])
        tip1 = self._remote_tip()
        os.remove(os.path.join(self.repo, "temp", "mirror_state.json"))
        self._commit("第二条提交")
        ok, _h, out = self._push()
        self.assertTrue(ok, out)
        self.assertIn("树校验命中", out)
        self.assertEqual(git(self.remote, "rev-parse", "main^").stdout.strip(), tip1)
        self.assertEqual(self._remote_log(), ["第二条提交", "第一条提交"])

    def test_镜像历史被改写则回退全量重建(self):
        self.assertTrue(self._push()[0])
        # 别处把镜像分支改成了一段无关历史
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        subprocess.run(["git", "init", "-q", "-b", "main", other], check=True, env=ENV)
        with io.open(os.path.join(other, "x.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "无关历史")
        git(other, "push", "-q", "--force", self.remote, "main:main")
        os.remove(os.path.join(self.repo, "temp", "mirror_state.json"))
        ok, _h, out = self._push()
        self.assertTrue(ok, out)
        self.assertIn("回退全量重建", out)
        self.assertEqual(self._remote_log(), ["第一条提交"])     # 重建成本地历史

    def test_full走main强制全量重建(self):
        self.assertTrue(self._push()[0])
        argv, buf = sys.argv, io.StringIO()
        try:
            sys.argv = ["push_code_mirror.py", "--remote", self.remote, "--full", "--keep"]
            with contextlib.redirect_stdout(buf):
                rc = PCM.main()
        finally:
            sys.argv = argv
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("历史重放", out)                     # --full 走 push_history（不是增量）
        self.assertEqual(self._remote_log(), ["第一条提交"])

    def test_rmtree能删只读文件(self):
        """git 的 loose object 是只读的：普通 rmtree 删不动 → 每次推送都漏临时目录。"""
        d = os.path.join(self.tmp, "ro")
        sub = os.path.join(d, ".git", "objects", "41")
        os.makedirs(sub)
        p = os.path.join(sub, "e095b76967ae399ca3ed62e11d79ce89b0fdfd")
        with io.open(p, "wb") as f:
            f.write(b"x")
        os.chmod(p, stat.S_IREAD)                      # 模拟只读 git object
        PCM._rmtree(d)
        self.assertFalse(os.path.exists(d), "只读文件没清属性 → 目录残留")

    def test_推送会清理临时目录(self):
        """清理必须真的被调用（文件删不掉是环境问题——杀软/Sysmon 句柄占住刚写的 .git 对象，
        实测连 `rm -rf` 都报 "Directory not empty"；但代码不能退回"不清理"）。"""
        calls = []
        orig = PCM._rmtree

        def spy(p):
            calls.append(p)
            return orig(p)

        PCM._rmtree = spy
        try:
            self.assertTrue(self._push()[0])
        finally:
            PCM._rmtree = orig
        got = {"aa_mirror_ex_": 0, "aa_mirror_chk": 0}
        for p in calls:
            for pre in got:
                if os.path.basename(p).startswith(pre):
                    got[pre] += 1
        self.assertTrue(all(v >= 1 for v in got.values()),
                        f"这些临时目录没被清理: {[k for k, v in got.items() if not v]}（calls={len(calls)}）")


if __name__ == "__main__":
    unittest.main()
