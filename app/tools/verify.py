#!/usr/bin/env python3
"""一次性验收服务 verify。

验收流水线（任一步失败即记录，最终以退出码报告）：

1. **构建产物检查**：关键源码 / 静态资源 / 测试 / Docker 编排文件齐全，
   全部 Python 源可字节码编译，页面与脚本相互引用完整；
   若环境内存在 node，则额外对前端 JS 做语法检查。
2. **代码测试**：运行单元测试（带势并查集、校验器、时间区间分治、
   300 组随机流与朴素重扫对照）。
3. **场景复现（进程内）**：登记 A→B=5、B→C=-2 可行且可推导 A→C=3；
   再登记 A→C=4 得到推导值 3 与冲突值 4 的矛盾环；撤回后恢复可行。
4. **HTTP 冒烟**：等待 web 健康后，对 ``BASE_URL`` 检查首页、
   静态资源、``/healthz`` 与 ``/api/audit``（含场景与错误定位断言）。

环境变量：``BASE_URL``（默认 http://web:8080）、
``HEALTH_TIMEOUT``（默认 30 秒）。
退出码：0 全部通过；1 存在失败项。
"""

import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = os.environ.get("BASE_URL", "http://web:8080").rstrip("/")
HEALTH_TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT", "30"))

SCENARIO_SCRIPT = "\n".join([
    "REG r1 A B 5",
    "REG r2 B C -2",
    "CHECK",
    "REG r3 A C 4",
    "CHECK",
    "DEL r3",
    "CHECK",
])

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not ok:
        line += f" -- {detail}"
    print(line, flush=True)
    return ok


# ---- 1. 构建产物检查 ----------------------------------------------------

def check_artifacts():
    print("\n== 1. 构建产物检查 ==", flush=True)
    required = [
        "server.py", "dsue.py", "validator.py", "solver.py",
        "static/index.html", "static/app.js", "static/styles.css",
        "tests/test_all.py", "tools/verify.py",
        "../Dockerfile", "../docker-compose.yml",
    ]
    missing = [r for r in required if not (ROOT / r).resolve().exists()]
    check("关键文件齐全", not missing,
          "缺失: " + ", ".join(missing) if missing else "")

    py_files = [p for p in ROOT.rglob("*.py")
                if "__pycache__" not in p.parts]
    broken = []
    for p in py_files:
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as exc:
            broken.append(f"{p}: {exc}")
    check(f"Python 源码字节码编译（{len(py_files)} 个文件）",
          not broken, "; ".join(broken))

    index_html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    check("页面引用 app.js", 'src="/app.js"' in index_html)
    check("页面引用 styles.css", 'href="/styles.css"' in index_html)
    check("前端调用 /api/audit", "/api/audit" in app_js)
    check("页面包含检查点/矛盾环展示结构",
          all(tok in index_html for tok in
              ["检查点", "stations", "script", "results"]))

    node = shutil.which("node")
    if node:
        proc = subprocess.run([node, "--check", str(ROOT / "static/app.js")],
                              capture_output=True, text=True)
        check("node --check 前端 JS 语法", proc.returncode == 0,
              proc.stderr.strip())
    else:
        print("[INFO] 环境中无 node，跳过 JS 语法检查（资源完整性已检查）",
              flush=True)


# ---- 2. 单元测试 --------------------------------------------------------

def check_unit_tests():
    print("\n== 2. 代码测试（unittest）==", flush=True)
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stderr)
    result = runner.run(suite)
    check(f"单元测试全部通过（运行 {result.testsRun} 项）",
          result.wasSuccessful(),
          f"失败 {len(result.failures)}，错误 {len(result.errors)}")


# ---- 3. 进程内场景复现 --------------------------------------------------

def check_scenario_in_process():
    print("\n== 3. 登记 / 冲突 / 撤回恢复场景（进程内复现）==", flush=True)
    from server import run_audit

    out = run_audit("A B C", SCENARIO_SCRIPT)
    ok = out.get("ok") and not out.get("errors")
    check("场景脚本校验通过", ok, json.dumps(out.get("errors"), ensure_ascii=False))
    if not ok:
        return

    cps = out["checkpoints"]
    check("恰好 3 个检查点", len(cps) == 3, f"实际 {len(cps)} 个")
    if len(cps) != 3:
        return

    cp1, cp2, cp3 = cps

    potentials = {m["name"]: m["potential"]
                  for g in cp1["components"] for m in g["members"]}
    check("检查点1：可行", cp1["feasible"])
    check("检查点1：可推导 A→C = 3（C 的势为 3）",
          cp1["feasible"] and potentials.get("C") == 3,
          f"势分配: {potentials}")

    cyc = cp2["conflict"]
    check("检查点2：存在冲突", not cp2["feasible"] and cyc is not None)
    if cyc:
        terms_ok = ([t["value"] for t in cyc["terms"]] == [5, -2]
                    and {t["id"] for t in cyc["terms"]} == {"r1", "r2"})
        check("检查点2：矛盾环为 r1+r2 闭合到 r3",
              cyc["conflict_id"] == "r3" and terms_ok,
              json.dumps(cyc, ensure_ascii=False))
        check("检查点2：各式相加推导值 = 3",
              cyc["inferred"] == 3 and cyc["sum"] == 3,
              f"inferred={cyc['inferred']} sum={cyc['sum']}")
        check("检查点2：冲突值 = 4，残差 = -1",
              cyc["declared"] == 4 and cyc["residual"] == -1,
              f"declared={cyc.get('declared')} residual={cyc.get('residual')}")

    check("检查点3：撤回 r3 后恢复可行", cp3["feasible"])

    # 校验错误一次定位并清除旧审计结果
    bad = run_audit("A B",
                    "REG r1 A B 1\nREG r1 B A 2\nREG r2 A Z 9\n"
                    "DEL nope\nCHECK")
    codes = {e["code"] for e in bad["errors"]}
    check("错误输入：一次定位重复登记/未知端点/未知标识",
          not bad["ok"]
          and {"E_DUP_ID", "E_UNKNOWN_ENDPOINT", "E_UNKNOWN_ID"} <= codes,
          f"错误码: {sorted(codes)}")
    check("错误输入：不产出审计结论（旧结果被清除）",
          bad["checkpoints"] == [])


# ---- 4. HTTP 冒烟 -------------------------------------------------------

def http_get(path):
    req = urllib.request.Request(BASE_URL + path, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


def http_post_json(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def wait_ready():
    deadline = time.time() + HEALTH_TIMEOUT
    last_err = ""
    while time.time() < deadline:
        try:
            status, body = http_get("/healthz")
            if status == 200 and json.loads(body).get("status") == "ok":
                return True
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_err = str(exc)
        time.sleep(0.5)
    check("等待 web 健康就绪", False, f"超时: {last_err}")
    return False


def check_http():
    print(f"\n== 4. HTTP 冒烟（{BASE_URL}）==", flush=True)
    if not wait_ready():
        return
    check("GET /healthz 返回 200 ok", True)

    status, html = http_get("/")
    check("GET / 静态页面 200",
          status == 200 and "检修台" in html and 'src="/app.js"' in html,
          f"status={status}")

    for asset in ("/app.js", "/styles.css"):
        try:
            st, body = http_get(asset)
            check(f"GET {asset} 200 且非空", st == 200 and len(body) > 0,
                  f"status={st}")
        except urllib.error.HTTPError as exc:
            check(f"GET {asset} 200 且非空", False, str(exc))

    status, out = http_post_json("/api/audit",
                                 {"stations": "A B C",
                                  "script": SCENARIO_SCRIPT})
    api_ok = (status == 200 and out.get("ok")
              and len(out.get("checkpoints", [])) == 3)
    check("POST /api/audit 场景返回 3 个检查点", api_ok)
    if api_ok:
        cps = out["checkpoints"]
        c2 = cps[1]["conflict"]
        check("HTTP 场景：可行→矛盾环(3 vs 4)→撤回恢复",
              cps[0]["feasible"]
              and not cps[1]["feasible"]
              and c2["inferred"] == 3 and c2["declared"] == 4
              and cps[2]["feasible"],
              json.dumps(c2, ensure_ascii=False))

    status, bad = http_post_json(
        "/api/audit",
        {"stations": "A B",
         "script": "REG r1 A B 1\nREG r1 A B 2\nREG r2 A Z 1\nCHECK"})
    codes = {e["code"] for e in bad.get("errors", [])}
    check("HTTP 错误输入：一次定位且无审计结论",
          status == 200 and not bad["ok"]
          and {"E_DUP_ID", "E_UNKNOWN_ENDPOINT"} <= codes
          and bad["checkpoints"] == [],
          f"codes={sorted(codes)}")


def main():
    print("=" * 64)
    print(" 深海观测缆检修台 · 一次性验收 verify")
    print("=" * 64, flush=True)
    check_artifacts()
    check_unit_tests()
    check_scenario_in_process()
    check_http()

    total = len(results)
    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 64)
    print(f" 验收结果：{total - len(failed)}/{total} 项通过")
    if failed:
        print(" 失败项：")
        for name in failed:
            print(f"  - {name}")
        print(" 结论：验收未通过")
        return 1
    print(" 结论：验收通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
