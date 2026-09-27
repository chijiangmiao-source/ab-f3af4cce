"""脚本与站点清单的一次性校验解析。

脚本语法（每行一条，大小写不敏感关键字，支持中文别名）::

    REG  标识 起点 终点 整数   # 登记 x_终点 - x_起点 = 整数（别名 R / ADD / 登记）
    DEL  标识                 # 撤回关系（别名 D / REMOVE / WITHDRAW / 撤回 / 撤销）
    CHECK                     # 检查点（别名 C / CHK / 检查）
    # 注释、空行忽略

校验对整份输入只做一趟扫描，定位**全部**问题（行号 + 错误码），
不会在第一个错误处中断；任意错误存在时上层不得产出审计结论。
"""

import re

MAX_STATIONS = 64
MAX_LINES = 180
INT_MIN = -2147483648
INT_MAX = 2147483647

ID_RE = re.compile(r"^[^\W\d][\w.\-]{0,63}$", re.UNICODE)
NAME_RE = re.compile(r"^[^\W\d]\w{0,31}$", re.UNICODE)
INT_RE = re.compile(r"^[+-]?\d+$")

REG_KW = {"REG", "R", "ADD", "登记"}
DEL_KW = {"DEL", "D", "REMOVE", "WITHDRAW", "撤回", "撤销"}
CHK_KW = {"CHECK", "C", "CHK", "检查点", "检查"}


def _err(line, code, message, token=None):
    e = {"line": line, "code": code, "message": message}
    if token is not None:
        e["token"] = token
    return e


def parse_stations(text):
    """解析站点清单：逗号/空白分隔。返回 (名称列表, 错误列表)。"""
    errors = []
    names = []
    seen = set()
    tokens = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    if len(tokens) > MAX_STATIONS:
        errors.append(_err(None, "E_TOO_MANY_STATIONS",
                           f"站点最多 {MAX_STATIONS} 个，当前 {len(tokens)} 个"))
    for tok in tokens:
        if not tok:
            continue
        if not NAME_RE.match(tok):
            errors.append(_err(None, "E_BAD_STATION", f"无效站点名：{tok!r}", tok))
            continue
        if tok in seen:
            errors.append(_err(None, "E_DUP_STATION", f"站点重复：{tok!r}", tok))
            continue
        seen.add(tok)
        names.append(tok)
    return names, errors


def _strip_comment(line):
    idx = line.find("#")
    return line if idx < 0 else line[:idx]


def validate(stations_text, script_text):
    """一趟扫描完成全部校验。

    返回 ``{"errors", "names", "ops", "line_count"}``；
    ``ops`` 仅包含完全合法的行，供求解器使用。
    """
    names, errors = parse_stations(stations_text)
    known = set(names)

    lines = script_text.splitlines()
    line_count = len(lines)
    if line_count > MAX_LINES:
        errors.append(_err(MAX_LINES + 1, "E_TOO_MANY_LINES",
                           f"脚本最多 {MAX_LINES} 行，当前 {line_count} 行"))

    ops = []
    registry = {}  # id -> {"status": active|withdrawn, ...}

    for lineno, raw in enumerate(lines[:MAX_LINES], start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        tokens = line.split()
        kw = tokens[0]
        kw_up = kw.upper()

        if kw_up in REG_KW:
            line_errors = []
            if len(tokens) != 5:
                errors.append(_err(lineno, "E_BAD_FORM",
                                   "登记格式应为：REG 标识 起点 终点 整数"))
                continue
            _, rid, u, v, d_token = tokens

            id_ok = bool(ID_RE.match(rid))
            if not id_ok:
                line_errors.append(_err(lineno, "E_BAD_ID",
                                        f"无效标识：{rid!r}", rid))
            elif registry.get(rid, {}).get("status") == "active":
                line_errors.append(_err(
                    lineno, "E_DUP_ID",
                    f"标识 {rid!r} 已登记且未撤回，不能重复登记", rid))

            if not NAME_RE.match(u):
                line_errors.append(_err(lineno, "E_BAD_ENDPOINT",
                                        f"无效端点名：{u!r}", u))
            elif u not in known:
                line_errors.append(_err(lineno, "E_UNKNOWN_ENDPOINT",
                                        f"未知端点：站点 {u!r} 不在站点清单中", u))
            if not NAME_RE.match(v):
                line_errors.append(_err(lineno, "E_BAD_ENDPOINT",
                                        f"无效端点名：{v!r}", v))
            elif v not in known:
                line_errors.append(_err(lineno, "E_UNKNOWN_ENDPOINT",
                                        f"未知端点：站点 {v!r} 不在站点清单中", v))

            if not INT_RE.match(d_token):
                line_errors.append(_err(lineno, "E_BAD_INT",
                                        f"不是整数：{d_token!r}", d_token))
                d_value = None
            else:
                d_value = int(d_token)
                if d_value < INT_MIN or d_value > INT_MAX:
                    line_errors.append(_err(
                        lineno, "E_INT_RANGE",
                        f"整数超界：{d_value} 不在 [{INT_MIN}, {INT_MAX}] 内",
                        d_token))

            errors.extend(line_errors)
            if not line_errors and id_ok:
                ops.append({"kind": "reg", "line": lineno,
                            "id": rid, "u": u, "v": v, "d": d_value})
                registry[rid] = {"status": "active", "u": u,
                                 "v": v, "d": d_value, "line": lineno}

        elif kw_up in DEL_KW:
            if len(tokens) != 2:
                errors.append(_err(lineno, "E_BAD_FORM",
                                   "撤回格式应为：DEL 标识"))
                continue
            rid = tokens[1]
            if not ID_RE.match(rid):
                errors.append(_err(lineno, "E_BAD_ID",
                                   f"无效标识：{rid!r}", rid))
            elif rid not in registry:
                errors.append(_err(lineno, "E_UNKNOWN_ID",
                                   f"未知标识：{rid!r} 从未登记", rid))
            elif registry[rid]["status"] == "withdrawn":
                errors.append(_err(lineno, "E_WITHDRAWN_ID",
                                   f"标识 {rid!r} 已撤回，不能再次撤回", rid))
            else:
                ops.append({"kind": "del", "line": lineno, "id": rid})
                registry[rid]["status"] = "withdrawn"

        elif kw_up in CHK_KW:
            if len(tokens) != 1:
                errors.append(_err(lineno, "E_BAD_FORM", "检查点单独占一行：CHECK"))
                continue
            ops.append({"kind": "check", "line": lineno})

        else:
            errors.append(_err(lineno, "E_BAD_COMMAND",
                               f"无法识别的命令：{kw!r}", kw))

    return {"errors": errors, "names": names, "ops": ops,
            "line_count": line_count}
