"""带权（带势）并查集与可回滚快照。

关系 ``x_v - x_u = d`` 表示站点 ``v`` 的读数相对站点 ``u`` 的偏移为 ``d``。

DSUE 维护一片带权森林：

* ``parent[x]``  —— x 的父节点；
* ``diff[x]``    —— ``weight(x) - weight(parent[x])``；
* ``size[x]``    —— 以 x 为根的树大小，用于按大小合并且保持树高对数级；
* ``trace[x]``   —— x 到 parent[x] 的**原始关系行走步序列**
  （``(rid, a, b)`` 表示沿登记关系 rid 从站点 a 走到 b）。

为什么需要 trace：合并两个连通分量时，新树边携带的势是折叠值
（可能等于若干原始关系之和），它本身不对应任何一条登记关系。
因此挂载时用「被挂根 → 桥端点 → 另一分量根」的原始关系链填充
trace，使任意两点间的推导都能展开成工程师可逐项复算的原始等式。

``find`` 不做路径压缩（会产生无法逐项撤销的改动），按大小合并
保证 O(log n) 树高；每次合并压入一条撤销记录，冗余关系无操作，
冲突关系完全不写入，回滚后状态与进入时严格一致。
"""


class DSUE:
    def __init__(self, n):
        self.n = n
        self.parent = list(range(n))
        self.diff = [0] * n        # diff[x] = weight(x) - weight(parent[x])
        self.size = [1] * n
        self.trace = [[] for _ in range(n)]  # x -> parent[x] 的原始步序列
        self.history = []

    # ---- 查询 -----------------------------------------------------------

    def find(self, x):
        """返回 (根, weight(x) - weight(root))。"""
        delta = 0
        while self.parent[x] != x:
            delta += self.diff[x]
            x = self.parent[x]
        return x, delta

    def find_trace(self, x):
        """返回 (根, weight(x)-weight(root), x 到根的原始步序列)。"""
        delta = 0
        steps = []
        while self.parent[x] != x:
            delta += self.diff[x]
            steps.extend(self.trace[x])
            x = self.parent[x]
        return x, delta, steps

    def offset_between(self, u, v):
        """已知同组时返回 weight(v) - weight(u)；不同组返回 None。"""
        ru, du = self.find(u)
        rv, dv = self.find(v)
        if ru != rv:
            return None
        return dv - du

    # ---- 改动 -----------------------------------------------------------

    def union(self, u, v, d, rel_id=None):
        """加入约束 weight(v) - weight(u) = d。

        返回 ``(是否冲突, 推导值)``：

        * 两点尚未连通：合并，返回 ``(False, None)``；
        * 已连通且现有推导值 == d：无操作，返回 ``(False, None)``；
        * 已连通但现有推导值 != d：不写入任何改动，
          返回 ``(True, 现有推导值 weight(v)-weight(u))``。
        """
        ru, du, su = self.find_trace(u)
        rv, dv, sv = self.find_trace(v)
        if ru == rv:
            inferred = dv - du          # weight(v) - weight(u)
            return (inferred != d), inferred
        # 由 d = weight(v)-weight(u) 得：
        # weight(rv) - weight(ru) = d + du - dv = w
        w = d + du - dv
        if self.size[ru] < self.size[rv]:
            # ru 挂到 rv：diff[ru] = weight(ru) - weight(rv) = -w；
            # 原始链 ru -> rv：ru->u（su 的反向）+ u->v（桥关系）+ v->rv（sv）
            chain = ([self._invert(s) for s in reversed(su)]
                     + [(rel_id, u, v)] + sv)
            self._attach(ru, rv, -w, chain)
            self.size[rv] += self.size[ru]
        else:
            # rv 挂到 ru：diff[rv] = weight(rv) - weight(ru) = w；
            # 原始链 rv -> ru：rv->v（sv 的反向）+ v->u（桥关系反向）+ u->ru（su）
            chain = ([self._invert(s) for s in reversed(sv)]
                     + [(rel_id, v, u)] + su)
            self._attach(rv, ru, w, chain)
            self.size[ru] += self.size[rv]
        return False, None

    @staticmethod
    def _invert(step):
        rid, a, b = step
        return rid, b, a

    def _attach(self, child, new_parent, edge_diff, chain):
        self.history.append((
            child,
            child,                  # 旧父（根指向自己）
            0,                      # 旧 diff
            new_parent,             # 接收根
            self.size[new_parent],  # 接收根旧大小
            self.trace[child],      # 旧 trace（根为 []）
        ))
        self.parent[child] = new_parent
        self.diff[child] = edge_diff
        self.trace[child] = chain

    # ---- 原始关系路径（矛盾环提取） -------------------------------------

    def path_trace(self, u, v):
        """返回 u 到 v 的原始关系行走步序列（展开为登记关系）。

        取 u->根 与 v->根 两条推导链，去掉公共后缀（共同祖先段），
        拼接 u->共同祖先 与 共同祖先->v；各式之和恒等于
        ``weight(v) - weight(u)``。
        """
        _, _, su = self.find_trace(u)   # u -> root
        _, _, sv = self.find_trace(v)   # v -> root
        common = 0
        max_common = min(len(su), len(sv))
        while (common < max_common
               and su[-1 - common] == sv[-1 - common]):
            common += 1
        up = su[:len(su) - common]                 # u -> 共同祖先
        vp = sv[:len(sv) - common]                 # v -> 共同祖先
        down = [self._invert(s) for s in reversed(vp)]  # 共同祖先 -> v
        return up + down

    # ---- 快照 / 回滚 ----------------------------------------------------

    def snapshot(self):
        return len(self.history)

    def rollback(self, snap):
        while len(self.history) > snap:
            (child, old_parent, old_diff,
             receiving_root, old_size, old_trace) = self.history.pop()
            self.parent[child] = old_parent
            self.diff[child] = old_diff
            self.size[receiving_root] = old_size
            self.trace[child] = old_trace
