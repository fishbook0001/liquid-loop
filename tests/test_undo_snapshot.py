"""m60 建议3 落地测试：storage undo checkpoint（写前快照 + 恢复）。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "liquid_loop"))

from liquid_loop import load, save, locked_state, WorkspaceState  # noqa: E402
from liquid_loop.storage import list_undo, restore_undo  # noqa: E402


class TestUndoSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, content: str):
        with locked_state(self.root) as st:
            if not any(a.name == "t" for a in st.anchors):
                st.add_anchor("t")
            ev = st.add_evidence("t", content, agent_id="tester")
            self.assertIsNotNone(ev)

    def test_snapshot_created_on_save(self):
        self._seed("第一条")
        # 第一次 save 前无 state.json，无快照
        self.assertEqual(len(list_undo(self.root)), 0)
        self._seed("第二条")
        # 第二次 save 前已有 state.json → 生成 1 份写前快照
        snaps = list_undo(self.root)
        self.assertEqual(len(snaps), 1)
        # 快照内容应包含"第一条"（覆盖前的旧状态）
        with open(snaps[0]["path"], encoding="utf-8") as f:
            data = json.load(f)
        contents = [e["content"] for e in data["evidences"]]
        self.assertIn("第一条", contents)
        self.assertNotIn("第二条", contents)

    def test_restore_returns_to_snapshot(self):
        self._seed("A")
        self._seed("B")
        snaps = list_undo(self.root)
        self.assertEqual(len(snaps), 1)
        ts = snaps[0]["ts"]
        r = restore_undo(self.root, ts)
        self.assertTrue(r["ok"])
        st = load(self.root)
        contents = [e.content for e in st.evidences]
        self.assertIn("A", contents)
        self.assertNotIn("B", contents)
        # 恢复前又自动快照了当前态（后悔药套后悔药）
        self.assertGreaterEqual(len(list_undo(self.root)), 2)

    def test_restore_nonexistent_fails(self):
        r = restore_undo(self.root, "19900101000000000000")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
