"""离线时间区间分治 + 可回滚带势并查集。

输入为 validator 产出的合法操作序列（登记 / 撤回 / 检查点）。
求解器**不**在每个检查点重扫全部活动关系，而是：

1. 求出每条关系在哪些检查点上处于活动期，得到检查点下标上的
   闭区间 ``[lo, hi]``；
2. 把区间挂到覆盖 ``[0, K-1]`` 的线段树节点上（整体复杂度
   O(E log K) 次挂载）；
3. DFS 线段树：进入节点时用可回滚带势并查集合并本节点边
   （无路径压缩、按大小合并，每次合并压入撤销记录），
   叶子给出该检查点结论，离开节点时按快照回滚——
   每条关系在每条根到叶路径上至多合并一次，
   总代价 O((E+K) log K · log n)。

冲突选择是稳定的：节点内边按**关系标识**字典序应用，DFS 先左后右，
首个被并查集判定冲突的关系即该检查点的矛盾环。同一标识撤回后
重新登记时内部用版本键区分（取值可能不同），展示仍用原标识。

矛盾环由并查集中 u 到 v 的原始关系行走链（各式相加得到推导值）
加上冲突关系（登记值）闭合而成，可供工程师逐项复算。
"""

try:  # 既支持作为包成员，也支持把 app/ 直接加入 sys.path
    from .dsue import DSUE
except ImportError:  # pragma: no cover
    from dsue import DSUE


class Solver:
    def __init__(self, names, ops):
        self.names = names
        self.index = {name: i for i, name in enumerate(names)}
        self.ops = ops
        self.edges = {}  # 内部版本键 -> (展示标识, u, v, d)

    # ---- 活动区间 -------------------------------------------------------

    def _intervals(self, k):
        """返回 [(lo, hi, key, rid, u, v, d)]。"""
        intervals = []
        active = {}   # rid -> (首个检查点下标, key, u, v, d)
        versions = {}  # rid -> 版本号
        c = 0         # 已经过的检查点数
        for op in self.ops:
            kind = op["kind"]
            if kind == "reg":
                ver = versions.get(op["id"], 0) + 1
                versions[op["id"]] = ver
                key = (op["id"], ver)
                self.edges[key] = (op["id"], op["u"], op["v"], op["d"])
                active[op["id"]] = (c, key, op["u"], op["v"], op["d"])
            elif kind == "del":
                start, key, u, v, d = active.pop(op["id"])
                if start <= c - 1:
                    intervals.append((start, c - 1, key, op["id"], u, v, d))
            else:  # check
                c += 1
        for rid, (start, key, u, v, d) in active.items():
            if start <= k - 1:
                intervals.append((start, k - 1, key, rid, u, v, d))
        return intervals

    # ---- 线段树 ---------------------------------------------------------

    def _build_segments(self, intervals, k):
        seg = [[] for _ in range(max(4 * k, 1))]

        def add(node, l, r, lo, hi, edge):
            if lo <= l and r <= hi:
                seg[node].append(edge)
                return
            m = (l + r) // 2
            if lo <= m:
                add(node * 2, l, m, lo, hi, edge)
            if hi > m:
                add(node * 2 + 1, m + 1, r, lo, hi, edge)

        for edge in intervals:
            lo, hi = edge[0], edge[1]
            add(1, 0, k - 1, lo, hi, edge)
        # 稳定选择：节点内按关系标识（第 4 个元素 rid）字典序
        for bucket in seg:
            bucket.sort(key=lambda e: (e[3], e[2]))
        return seg

    # ---- 矛盾环 ---------------------------------------------------------

    def _cycle(self, dsu, conflict_edge, inferred):
        key, rid, u, v, declared = conflict_edge
        terms = []
        for step_key, ai, bi in dsu.path_trace(self.index[u], self.index[v]):
            step_rid, ru, rv, d_edge = self.edges[step_key]
            a, b = self.names[ai], self.names[bi]
            if ru == a and rv == b:
                value = d_edge
                expr = f"x_{b} - x_{a}"
            else:
                # 行走方向与登记方向相反，取负值
                value = -d_edge
                expr = f"x_{b} - x_{a}（= -(x_{rv} - x_{ru})）"
            terms.append({"id": step_rid, "from": a, "to": b,
                          "expr": expr, "value": value})
        total = sum(t["value"] for t in terms)
        return {
            "conflict_id": rid,
            "u": u,
            "v": v,
            "declared": declared,      # 冲突值（该关系登记的 d）
            "inferred": inferred,      # 推导值（森林路径各式之和）
            "sum": total,              # 验算：应等于 inferred
            "residual": inferred - declared,
            "terms": terms,            # 逐项相加，供工程师复算
            "closing": {"id": rid, "expr": f"x_{v} - x_{u}",
                        "value": declared},
        }

    # ---- 主流程 ---------------------------------------------------------

    def solve(self):
        checkpoints = [op for op in self.ops if op["kind"] == "check"]
        k = len(checkpoints)
        if k == 0:
            return {"checkpoints": []}

        intervals = self._intervals(k)
        seg = self._build_segments(intervals, k)
        dsu = DSUE(len(self.names))
        results = [None] * k

        def components():
            groups = {}
            for i, name in enumerate(self.names):
                root, pot = dsu.find(i)
                groups.setdefault(root, []).append((name, pot))
            out = []
            for root, members in groups.items():
                members.sort(key=lambda t: self.index[t[0]])
                base = members[0][1]
                out.append({
                    "root": members[0][0],
                    "members": [{"name": n, "potential": p - base}
                                for n, p in members],
                })
            out.sort(key=lambda c: self.index[c["members"][0]["name"]])
            return out

        def dfs(node, l, r, inherited_conflict):
            snap = dsu.snapshot()
            conflict = inherited_conflict
            if conflict is None:
                for edge in seg[node]:
                    key, rid, u, v, d = edge[2], edge[3], edge[4], edge[5], edge[6]
                    bad, inferred = dsu.union(
                        self.index[u], self.index[v], d, key)
                    if bad:
                        conflict = self._cycle(
                            dsu, (key, rid, u, v, d), inferred)
                        break
            if l == r:
                op = checkpoints[l]
                results[l] = {
                    "index": l + 1,
                    "line": op["line"],
                    "feasible": conflict is None,
                    "conflict": conflict,
                    "components": components() if conflict is None else None,
                }
            else:
                m = (l + r) // 2
                dfs(node * 2, l, m, conflict)
                dfs(node * 2 + 1, m + 1, r, conflict)
            dsu.rollback(snap)

        dfs(1, 0, k - 1, None)
        return {"checkpoints": results}
