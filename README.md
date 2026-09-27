# 深海观测缆 · 站间偏移检修台

检修期间，工程师可在浏览器粘贴最多 **64 个站点、180 行脚本**，依次登记或撤回
站间整数偏移关系 `x_v - x_u = d`，并在检查点确认当时仍活动的读数能否共同成立。

## 快速开始（Docker Compose）

```bash
# 默认宿主机端口 8080
docker compose up -d --build
# 打开 http://localhost:8080

# 使用可配置宿主机端口
HOST_PORT=9090 docker compose up -d
# 或复制 .env.example 为 .env 后修改 HOST_PORT
```

健康状态：`GET http://localhost:${HOST_PORT}/healthz` → `{"status":"ok",...}`

### 一次性验收服务 verify

```bash
docker compose run --rm verify
```

`verify` 会自动等待 `web` 健康就绪，然后：

1. **构建产物检查**：关键源码 / 静态资源 / 编排文件齐全，全部 Python 源字节码编译通过，
   页面与脚本引用完整（有 node 时额外做 JS 语法检查）；
2. **代码测试**：运行单元测试（带势并查集、校验器、时间区间分治、
   300 组随机流与"逐检查点朴素重扫"参考实现的对照）；
3. **场景复现**：登记 `A→B=5`、`B→C=-2` → 可行且推导 `A→C=3`；
   再登记 `A→C=4` → 展示推导值 3 与冲突值 4 的矛盾环；撤回后恢复可行；
4. **HTTP 冒烟**：在 `http://web:8080` 检查首页、静态资源、`/healthz`、`/api/audit`。

全部通过以退出码 **0** 报告，任一失败为 **1**。

## 脚本语法

| 命令 | 含义 | 别名 |
| --- | --- | --- |
| `REG 标识 起点 终点 整数` | 登记 `x_终点 - x_起点 = 整数` | `R` / `ADD` / `登记` |
| `DEL 标识` | 撤回关系 | `D` / `REMOVE` / `WITHDRAW` / `撤回` / `撤销` |
| `CHECK` | 检查点 | `C` / `CHK` / `检查点` / `检查` |

`#` 后为注释；标识 `^[^\W\d][\w.\-]{0,63}$`；站点名 `^[^\W\d]\w{0,31}$`；
整数范围 32 位有符号整数 `[-2147483648, 2147483647]`。

示例（页面"载入示例"按钮同款）：

```
REG r1 A B 5      # A→B = 5
REG r2 B C -2     # B→C = -2
CHECK             # 可行；A→C = 5+(-2) = 3
REG r3 A C 4      # 与推导值 3 冲突
CHECK             # 矛盾环：(x_B-x_A)+(x_C-x_B)=3 ≠ 4
DEL r3
CHECK             # 撤回后恢复可行
```

## 校验策略

整份输入一趟扫描，**一次定位全部问题**（不短路于首个错误），
并在任意错误存在时不产出审计结论（前端同时清除旧审计结果）：

| 错误码 | 含义 |
| --- | --- |
| `E_DUP_ID` | 重复登记（标识处于活动状态时再次登记） |
| `E_UNKNOWN_ID` / `E_WITHDRAWN_ID` | 未知标识 / 已撤回标识再次撤回 |
| `E_BAD_ID` | 无效标识 |
| `E_UNKNOWN_ENDPOINT` / `E_BAD_ENDPOINT` | 未知端点 / 无效端点名 |
| `E_BAD_INT` / `E_INT_RANGE` | 非整数 / 整数超界 |
| `E_TOO_MANY_STATIONS` / `E_TOO_MANY_LINES` | 超过 64 站点 / 180 行 |
| `E_BAD_FORM` / `E_BAD_COMMAND` | 格式错误 / 无法识别的命令 |

## 求解算法（不为每个检查点重扫全部活动关系）

* **时间区间分治**：先求每条关系在检查点下标轴上的活动闭区间 `[lo, hi]`，
  挂到覆盖该区间的线段树节点；DFS 时在节点入口合并边、出口按快照回滚，
  叶子即检查点结论。总代价 `O((E+K) log K · log n)`，与朴素的 `O(E·K)` 相对。
* **可回滚带势并查集**：`diff[x] = weight(x) - weight(parent[x])`，
  只按大小合并、**不做路径压缩**（树高 O(log n)），每次合并压入撤销记录；
  冗余关系无操作，冲突关系完全不写入，回滚精确恢复。
* **矛盾环可复算**：并查集树边是折叠势，不对应原始关系；
  因此每个节点额外保存到父节点的**原始关系行走链**（合并时由
  "被挂根→桥端点→另一分量根"拼接），矛盾环展开为原始登记关系的
  逐项取值（反向行走取负），其和恒等于推导值，再与冲突登记值闭合。
* **稳定选择**：节点桶内按关系标识字典序排序、DFS 先左后右，
  冲突关系的选择对等价输入确定不变。

## 目录结构

```
app/
  server.py        # 标准库 HTTP 服务（静态页 + /api/audit + /healthz）
  validator.py     # 一趟扫描全量校验
  solver.py        # 时间区间分治
  dsue.py          # 可回滚带势并查集（原始关系行走链）
  static/          # index.html / app.js / styles.css
  tests/test_all.py
  tools/verify.py  # 一次性验收服务（可执行）
Dockerfile
docker-compose.yml # web + verify
```

## 本地开发（无 Docker 时）

仅需 Python 3.11+（标准库，无第三方依赖）：

```bash
cd app
python3 -m unittest discover -s tests -v
PORT=8080 python3 server.py
BASE_URL=http://127.0.0.1:8080 python3 tools/verify.py
```
