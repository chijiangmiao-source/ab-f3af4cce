/* 深海观测缆检修台前端脚本 */
(function () {
  "use strict";

  var stationsEl = document.getElementById("stations");
  var scriptEl = document.getElementById("script");
  var statusEl = document.getElementById("status");
  var resultsEl = document.getElementById("results");
  var errorsBox = document.getElementById("errors-box");
  var errorsList = document.getElementById("errors");
  var stationCount = document.getElementById("station-count");
  var lineCount = document.getElementById("line-count");

  var EXAMPLE_STATIONS = "A, B, C";
  var EXAMPLE_SCRIPT = [
    "REG r1 A B 5",
    "REG r2 B C -2",
    "CHECK",
    "",
    "REG r3 A C 4",
    "CHECK",
    "",
    "DEL r3",
    "CHECK",
  ].join("\n");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fmtInt(n) {
    return String(n);
  }

  function updateCounts() {
    var s = stationsEl.value.split(/[,\s]+/).filter(Boolean);
    stationCount.textContent = s.length + " / 64";
    lineCount.textContent = scriptEl.value.split(/\n/).length + " / 180";
  }

  stationsEl.addEventListener("input", updateCounts);
  scriptEl.addEventListener("input", updateCounts);

  document.getElementById("btn-example").addEventListener("click", function () {
    stationsEl.value = EXAMPLE_STATIONS;
    scriptEl.value = EXAMPLE_SCRIPT;
    clearResults();
    updateCounts();
  });
  document.getElementById("btn-clear").addEventListener("click", function () {
    stationsEl.value = "";
    scriptEl.value = "";
    clearResults();
    updateCounts();
  });
  document.getElementById("btn-run").addEventListener("click", submitAudit);

  function clearResults() {
    resultsEl.innerHTML = "";
    errorsBox.classList.add("hidden");
    errorsList.innerHTML = "";
    statusEl.textContent = "";
  }

  // ---- 错误定位展示 -----------------------------------------------------

  var ERROR_CODE_LABELS = {
    E_TOO_MANY_STATIONS: "超界（站点数）",
    E_BAD_STATION: "无效标识/名称",
    E_DUP_STATION: "站点重复",
    E_TOO_MANY_LINES: "超界（行数）",
    E_BAD_FORM: "格式错误",
    E_BAD_ID: "无效标识",
    E_DUP_ID: "重复登记",
    E_BAD_ENDPOINT: "无效端点",
    E_UNKNOWN_ENDPOINT: "未知端点",
    E_BAD_INT: "不是整数",
    E_INT_RANGE: "整数超界",
    E_UNKNOWN_ID: "未知标识",
    E_WITHDRAWN_ID: "已撤回标识",
    E_BAD_COMMAND: "无法识别的命令"
  };

  function renderErrors(errors) {
    errors.forEach(function (err) {
      var li = el("li");
      var where = err.line
        ? "第 " + err.line + " 行"
        : "站点清单";
      var label = ERROR_CODE_LABELS[err.code] || err.code;
      li.appendChild(el("span", "ln", "[" + where + "][" + label + "] "));
      li.appendChild(document.createTextNode(err.message));
      errorsList.appendChild(li);
    });
    errorsBox.classList.remove("hidden");
  }

  // ---- 检查点结论 -------------------------------------------------------

  function signed(n) {
    return (n > 0 ? "+" : "") + fmtInt(n);
  }

  function renderCycle(c) {
    var box = el("div", "cycle");
    box.appendChild(el("div", "cycle-title",
      "矛盾环（按关系标识稳定选出；沿森林路径各式相加闭合到冲突关系）"));

    var table = el("table", "terms");
    var thead = el("thead");
    var hr = el("tr");
    ["关系标识", "行走", "等式（该项取值）", "取值"].forEach(function (h, idx) {
      var th = el("th", null, h);
      if (idx === 3) th.className = "num";
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    c.terms.forEach(function (t) {
      var tr = el("tr");
      tr.appendChild(el("td", null, t.id));
      tr.appendChild(el("td", null, t.from + " → " + t.to));
      tr.appendChild(el("td", null, t.expr));
      var td = el("td", "num", signed(t.value));
      tr.appendChild(td);
      tbody.appendChild(tr);
    });

    // 闭合边：冲突关系本身
    var trClose = el("tr");
    trClose.appendChild(el("td", null, c.conflict_id));
    trClose.appendChild(el("td", null, c.u + " → " + c.v));
    trClose.appendChild(el("td", null,
      c.closing.expr + "（登记值，作为闭合项）"));
    trClose.appendChild(el("td", "num", signed(c.declared)));
    tbody.appendChild(trClose);
    table.appendChild(tbody);
    box.appendChild(table);

    var sum = el("div", "cycle-summary");
    var s1 = el("span");
    s1.appendChild(document.createTextNode("各式相加推导值 = "));
    s1.appendChild(el("span", "v-inferred", fmtInt(c.inferred)));
    var s2 = el("span");
    s2.appendChild(document.createTextNode("冲突值（登记） = "));
    s2.appendChild(el("span", "v-declared", fmtInt(c.declared)));
    var s3 = el("span");
    s3.appendChild(document.createTextNode("残差 = 推导值 − 冲突值 = "));
    s3.appendChild(el("span", "v-residual", fmtInt(c.residual)));
    var s4 = el("span");
    s4.appendChild(document.createTextNode("（逐项求和验算 = "));
    s4.appendChild(el("span", "v-inferred", fmtInt(c.sum)));
    s4.appendChild(document.createTextNode("）"));
    sum.appendChild(s1);
    sum.appendChild(s2);
    sum.appendChild(s3);
    sum.appendChild(s4);
    box.appendChild(sum);
    return box;
  }

  function renderComponents(comps) {
    var wrap = el("div", "comps");
    wrap.appendChild(el("b", null, "可行读数分配（各组首站点取 0）："));
    var ul = el("ul");
    comps.forEach(function (g) {
      var parts = g.members.map(function (m) {
        return m.name + "=" + signed(m.potential);
      });
      ul.appendChild(el("li", null, parts.join("，")));
    });
    wrap.appendChild(ul);
    return wrap;
  }

  function renderCheckpoint(cp) {
    var card = el("div", "cp " + (cp.feasible ? "ok" : "bad"));
    var head = el("div", "cp-head");
    head.appendChild(el("span",
      "badge " + (cp.feasible ? "ok" : "bad"),
      cp.feasible ? "可行" : "冲突"));
    head.appendChild(el("span", "cp-title", "检查点 #" + cp.index));
    head.appendChild(el("span", "cp-line", "脚本第 " + cp.line + " 行"));
    card.appendChild(head);

    var body = el("div", "cp-body");
    if (cp.feasible) {
      body.appendChild(el("div", "verdict-ok",
        "当前仍活动的读数关系可以共同成立。"));
      if (cp.components) body.appendChild(renderComponents(cp.components));
    } else {
      var c = cp.conflict;
      var line = el("div", "verdict-bad");
      line.textContent = "关系 " + c.conflict_id +
        "（x_" + c.v + " − x_" + c.u + " = " + fmtInt(c.declared) +
        "）与既有活动关系矛盾：";
      body.appendChild(line);
      body.appendChild(renderCycle(c));
    }
    card.appendChild(body);
    return card;
  }

  // ---- 提交 -------------------------------------------------------------

  function submitAudit() {
    clearResults();
    statusEl.textContent = "审计中…";
    fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stations: stationsEl.value,
        script: scriptEl.value
      })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        statusEl.textContent = "";
        if (!data.ok) {
          // 校验失败：明确清除旧审计结果，只列错误
          renderErrors(data.errors);
          return;
        }
        if (!data.checkpoints.length) {
          statusEl.textContent = "脚本中没有 CHECK 检查点。";
          return;
        }
        data.checkpoints.forEach(function (cp) {
          resultsEl.appendChild(renderCheckpoint(cp));
        });
      })
      .catch(function () {
        statusEl.textContent = "请求失败，请稍后重试。";
      });
  }

  updateCounts();
})();
