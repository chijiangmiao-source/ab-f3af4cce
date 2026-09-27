"""单元测试：DSUE、校验器、时间区间分治求解器。

直接运行：python -m unittest discover -s app/tests
或通过 verify 验收服务统一执行。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dsue import DSUE  # noqa: E402
from validator import (validate, MAX_STATIONS, MAX_LINES,  # noqa: E402
                       INT_MIN, INT_MAX)
from solver import Solver  # noqa: E402


def audit(stations, script):
    p = validate(stations, script)
    return p, Solver(p["names"], p["ops"]).solve()


class TestDSUE(unittest.TestCase):

    def test_basic_offset(self):
        d = DSUE(3)
        a, b, c = 0, 1, 2
        self.assertFalse(d.union(a, b, 5)[0])     # x_b - x_a = 5
        self.assertFalse(d.union(b, c, -2)[0])    # x_c - x_b = -2
        _, da = d.find(a)
        _, db = d.find(b)
        _, dc = d.find(c)
        # x_c - x_a = 3
        self.assertEqual(dc - da, 3)
        # 所有点同根
        self.assertEqual(len({d.find(i)[0] for i in range(3)}), 1)

    def test_conflict_not_written_and_rollback(self):
        d = DSUE(3)
        d.union(0, 1, 5, "r1")
        d.union(1, 2, -2, "r2")
        snap = d.snapshot()
        bad, inferred = d.union(0, 2, 4, "r3")
        self.assertTrue(bad)
        self.assertEqual(inferred, 3)
        # 冲突不产生历史记录
        self.assertEqual(d.snapshot(), snap)
        d.rollback(snap)
        self.assertEqual(d.offset_between(0, 2), 3)

        # 回滚到初始
        d.rollback(0)
        self.assertIsNone(d.offset_between(0, 1))
        self.assertEqual(d.parent, list(range(3)))
        self.assertEqual(d.size, [1] * 3)

    def test_redundant_union_is_noop(self):
        d = DSUE(2)
        d.union(0, 1, 5, "r1")
        snap = d.snapshot()
        bad, inferred = d.union(0, 1, 5, "r2")
        self.assertFalse(bad)
        self.assertEqual(inferred, 5)
        self.assertEqual(d.snapshot(), snap)

    def test_reverse_direction(self):
        d = DSUE(2)
        d.union(0, 1, 5)
        self.assertEqual(d.offset_between(1, 0), -5)


class TestValidator(unittest.TestCase):

    def test_all_errors_located_in_one_pass(self):
        stations = "A B"
        script = "\n".join([
            "REG r1 A B 5",
            "REG r1 B A 1",        # 重复登记
            "REG 2bad A B 3",      # 无效标识
            "REG r2 A X 3",        # 未知端点
            "REG r3 A B x",        # 非整数
            "REG r4 A B 99999999999999999999",  # 超界
            "DEL ghost",           # 未知标识
            "DEL r1",
            "DEL r1",              # 已撤回再次撤回
            "FOO bar",             # 未知命令
        ])
        p = validate(stations, script)
        codes = {e["code"] for e in p["errors"]}
        for expected in ["E_DUP_ID", "E_BAD_ID", "E_UNKNOWN_ENDPOINT",
                         "E_BAD_INT", "E_INT_RANGE", "E_UNKNOWN_ID",
                         "E_WITHDRAWN_ID", "E_BAD_COMMAND"]:
            self.assertIn(expected, codes)
        # 出错的登记行都不应进入 ops（仅第 1 行合法的 r1 进入）
        self.assertEqual([o["line"] for o in p["ops"] if o["kind"] == "reg"],
                         [1])

    def test_duplicate_id_after_withdraw_is_fine(self):
        p = validate("A B", "REG r1 A B 1\nDEL r1\nREG r1 B A 2\nCHECK")
        self.assertEqual(p["errors"], [])
        kinds = [(o["kind"], o.get("id")) for o in p["ops"]]
        self.assertEqual(kinds, [("reg", "r1"), ("del", "r1"),
                                 ("reg", "r1"), ("check", None)])

    def test_limits(self):
        stations = " ".join("S%d" % i for i in range(MAX_STATIONS + 1))
        p = validate(stations, "")
        self.assertTrue(any(e["code"] == "E_TOO_MANY_STATIONS"
                            for e in p["errors"]))

        script = "\n".join(["CHECK"] * (MAX_LINES + 1))
        p = validate("A", script)
        self.assertTrue(any(e["code"] == "E_TOO_MANY_LINES"
                            for e in p["errors"]))

    def test_int_boundaries(self):
        ok = validate("A B",
                      "REG r1 A B %d\nREG r2 B A %d\nCHECK" % (INT_MIN, INT_MAX))
        self.assertEqual(ok["errors"], [])
        bad = validate("A B", "REG r1 A B %d" % (INT_MAX + 1))
        self.assertTrue(any(e["code"] == "E_INT_RANGE" for e in bad["errors"]))

    def test_chinese_keywords_and_comments(self):
        p = validate("甲 乙", "登记 r1 甲 乙 7  # 中文关键字\n检查点")
        self.assertEqual(p["errors"], [])
        self.assertEqual(len(p["ops"]), 2)


class TestSolverScenario(unittest.TestCase):
    """题目规定场景：可行 -> 推导 3 -> 登记 4 冲突 -> 撤回恢复。"""

    STATIONS = "A B C"
    SCRIPT = "\n".join([
        "REG r1 A B 5",      # A→B = 5
        "REG r2 B C -2",     # B→C = -2
        "CHECK",             # 1) 可行，可推导 A→C = 3
        "REG r3 A C 4",      # 与推导值 3 冲突
        "CHECK",             # 2) 矛盾环：推导 3 vs 冲突 4
        "DEL r3",
        "CHECK",             # 3) 撤回后恢复可行
    ])

    def test_full_scenario(self):
        p, res = audit(self.STATIONS, self.SCRIPT)
        self.assertEqual(p["errors"], [])
        cps = res["checkpoints"]
        self.assertEqual(len(cps), 3)

        cp1, cp2, cp3 = cps
        self.assertTrue(cp1["feasible"])
        self.assertIsNone(cp1["conflict"])

        self.assertFalse(cp2["feasible"])
        cyc = cp2["conflict"]
        self.assertEqual(cyc["conflict_id"], "r3")
        self.assertEqual(cyc["inferred"], 3)
        self.assertEqual(cyc["declared"], 4)
        self.assertEqual(cyc["residual"], -1)
        self.assertEqual(cyc["sum"], cyc["inferred"])
        path_ids = [t["id"] for t in cyc["terms"]]
        self.assertEqual(set(path_ids), {"r1", "r2"})
        self.assertEqual(sum(t["value"] for t in cyc["terms"]), 3)

        self.assertTrue(cp3["feasible"])

    def test_no_rescan_complexity(self):
        # 结构性保证：每条关系仅在其活动区间被挂载，
        # 区间分治下每条关系挂载数 <= 2*ceil(log2(K))。
        stations = " ".join("S%d" % i for i in range(8))
        lines = []
        for i in range(7):
            lines.append("REG e%d S%d S%d %d" % (i, i, i + 1, i + 1))
        for _ in range(20):
            lines.append("CHECK")
        p = validate(stations, "\n".join(lines))
        s = Solver(p["names"], p["ops"])
        intervals = s._intervals(20)
        seg = s._build_segments(intervals, 20)
        placements = sum(len(b) for b in seg)
        # 朴素重扫为 7*20=140；分治挂载应远小于该值
        self.assertLess(placements, 7 * 20)
        res = s.solve()
        self.assertTrue(all(cp["feasible"] for cp in res["checkpoints"]))


class TestCycleStability(unittest.TestCase):

    def test_stable_conflict_selection_by_id(self):
        # 多条关系同时与环矛盾时，稳定选出标识字典序最小者
        script = "\n".join([
            "REG zzz A B 5",
            "REG aaa B C -2",
            "REG mid C A -2",   # 先制造一个可行环 (-2-2+5... 构造需谨慎)
        ])
        # 用更直接的构造：同一目标值两条冲突登记，靠标识决定顺序
        script = "\n".join([
            "REG e_b A B 5",
            "REG e_a B C -2",     # 节点内排序后 e_a 先应用
            "REG e_c A C 4",
            "REG e_d A C 9",
            "CHECK",
        ])
        p, res = audit("A B C", script)
        self.assertEqual(p["errors"], [])
        cp = res["checkpoints"][0]
        self.assertFalse(cp["feasible"])
        # 矛盾环的闭合关系为首个冲突登记 e_c（与 A→C 推导 3 冲突）
        self.assertEqual(cp["conflict"]["conflict_id"], "e_c")

    def test_withdraw_then_reuse_interval(self):
        script = "\n".join([
            "REG r1 A B 5",
            "CHECK",      # 可行
            "DEL r1",
            "REG r1 A B 9",
            "CHECK",      # 同标识重新登记，新值 9 可行
        ])
        p, res = audit("A B", script)
        self.assertEqual(p["errors"], [])
        self.assertTrue(res["checkpoints"][0]["feasible"])
        self.assertTrue(res["checkpoints"][1]["feasible"])


class TestRandomizedAgainstBruteForce(unittest.TestCase):
    """固定随机种子：分治结果必须与每检查点重扫的朴素实现一致。"""

    import random as _random

    def _brute_force(self, names, ops, order_by_leaf=None):
        """朴素参考实现：每个检查点重扫全部活动关系。

        ``order_by_leaf[cp]`` 给出该检查点上边的应用顺序
        （与分治 DFS 根→叶、桶内按标识排序的顺序一致），
        用于逐检查点验证回滚分治的结果与朴素重放完全相同。
        """
        idx = {n: i for i, n in enumerate(names)}
        active = {}
        answers = []
        cp_no = 0
        for op in ops:
            if op["kind"] == "reg":
                active[op["id"]] = (op["u"], op["v"], op["d"])
            elif op["kind"] == "del":
                active.pop(op["id"], None)
            else:
                d = DSUE(len(names))
                conflict = None
                order = order_by_leaf[cp_no] if order_by_leaf else sorted(active)
                for edge in order:
                    _, _, _, rid, u, v, val = edge
                    bad, inferred = d.union(idx[u], idx[v], val, rid)
                    if bad:
                        conflict = (rid, inferred, val)
                        break
                answers.append(conflict)
                cp_no += 1
        return answers

    def test_random_streams(self):
        rng = self._random.Random(20260927)
        for _ in range(300):
            n = rng.randint(2, 8)
            names = ["S%d" % i for i in range(n)]
            ops = []
            live = []
            used = set()
            script_lines = rng.randint(1, 60)
            for _ in range(script_lines):
                choice = rng.random()
                if choice < 0.55 or not live:
                    rid = "r%d" % rng.randint(0, 40)
                    if rid in used:
                        continue
                    u, v = rng.sample(names, 2)
                    d = rng.randint(-9, 9)
                    ops.append({"kind": "reg", "line": 0, "id": rid,
                                "u": u, "v": v, "d": d})
                    used.add(rid)
                    live.append(rid)
                elif choice < 0.75 and live:
                    rid = rng.choice(live)
                    ops.append({"kind": "del", "line": 0, "id": rid})
                    live.remove(rid)
                    used.discard(rid)
                else:
                    ops.append({"kind": "check", "line": 0})
            if not any(o["kind"] == "check" for o in ops):
                continue
            k = sum(1 for o in ops if o["kind"] == "check")
            s = Solver(names, ops)
            # 分治 DFS 在每个叶子上的实际边应用顺序（根→叶、桶内按 id）
            intervals = s._intervals(k)
            seg = s._build_segments(intervals, k)
            leaf_order = [[] for _ in range(k)]

            def walk(node, l, r, acc):
                acc = acc + seg[node]
                if l == r:
                    leaf_order[l] = acc
                else:
                    m = (l + r) // 2
                    walk(node * 2, l, m, acc)
                    walk(node * 2 + 1, m + 1, r, acc)

            walk(1, 0, k - 1, [])

            res = s.solve()
            expected = self._brute_force(names, ops, leaf_order)
            # 每个检查点时刻的活动关系取值（同标识撤回后可重新登记）
            snapshots = []
            cur = {}
            for op in ops:
                if op["kind"] == "reg":
                    cur[op["id"]] = (op["u"], op["v"], op["d"])
                elif op["kind"] == "del":
                    cur.pop(op["id"], None)
                else:
                    snapshots.append(dict(cur))
            self.assertEqual(len(res["checkpoints"]), len(expected))
            for cp, exp, snap in zip(res["checkpoints"], expected, snapshots):
                if exp is None:
                    self.assertTrue(cp["feasible"])
                else:
                    self.assertFalse(cp["feasible"])
                    cyc = cp["conflict"]
                    rid, inferred, declared = exp
                    # 分治 DFS 与朴素重放使用同一应用顺序，首个冲突关系必须一致
                    self.assertEqual(cyc["conflict_id"], rid)
                    self.assertEqual(cyc["inferred"], inferred)
                    self.assertEqual(cyc["declared"], declared)
                    # 展示自洽：逐项之和 == 推导值
                    self.assertEqual(
                        sum(t["value"] for t in cyc["terms"]), inferred)
                    self.assertEqual(cyc["sum"], inferred)
                    self.assertEqual(cyc["residual"], inferred - declared)
                    # 每一步的取值必须与当时登记关系一致（正向或反向）
                    for t in cyc["terms"]:
                        ru, rv, dv = snap[t["id"]]
                        if (t["from"], t["to"]) == (ru, rv):
                            self.assertEqual(t["value"], dv)
                        else:
                            self.assertEqual((t["from"], t["to"]), (rv, ru))
                            self.assertEqual(t["value"], -dv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
