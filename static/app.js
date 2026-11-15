"use strict";

const $ = (selector) => document.querySelector(selector);
const activeStatuses = new Set(["queued", "running", "waiting_otp"]);
const stateNames = {
  queued: "排队中",
  running: "执行中",
  waiting_otp: "等待验证码",
  succeeded: "会话已验证",
  partial: "已创建 · 待确认",
  failed: "未完成",
  cancelled: "已停止",
  interrupted: "服务已中断",
};
const flowSteps = [
  ["初始化会话", "SESSION / CSRF"],
  ["验证邮箱", "EMAIL / OTP"],
  ["创建账号", "PROFILE / REGISTER"],
  ["引导与条款", "BOOTSTRAP / DISCLOSURE"],
  ["验证会话", "HATCH ACCESS"],
  ["完成与邀请码", "READY / INVITE"],
];
const stageIndex = {
  queued: -1,
  bootstrap: 0,
  send_otp: 1,
  otp: 1,
  confirm_otp: 1,
  completion: 2,
  activation: 3,
  disclosure: 3,
  validation: 4,
  invite: 5,
  done: 6,
};
let mode = "single";
let tasks = [];
let selectedId = null;
let loading = false;
let toastTimer;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function notify(message, error = false) {
  clearTimeout(toastTimer);
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "X-Muse-Client": "1",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
    signal: options.signal || AbortSignal.timeout(15000),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`服务响应无效（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail.map((item) => `${item.field}: ${item.message}`).join("；")
        : detail || `请求失败（HTTP ${response.status}）`,
    );
  }
  return data;
}

function attention(task) {
  return (
    ["failed", "partial", "interrupted"].includes(task.status) ||
    !["not_requested", "redeemed"].includes(task.invite_status)
  );
}

function dateLabel(value, timeOnly = false) {
  return new Date(value).toLocaleString(
    "zh-CN",
    timeOnly
      ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
      : {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        },
  );
}

function updateMode(value) {
  mode = value;
  const batch = mode === "batch";
  $("#single-fields").hidden = batch;
  $("#email").disabled = batch;
  $("#otp-url").disabled = batch || $("#otp-mode").value !== "auto";
  $("#batch-fields").hidden = !batch;
  $("#batch-accounts").required = batch;
  $("#proxy-pool-field").hidden = !batch;
  $("#submit-label").textContent = batch ? "创建批量注册任务" : "开始注册";
  for (const tab of document.querySelectorAll("[data-mode]")) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
}

function collectAccounts() {
  const common = {
    first_name: $("#first-name").value.trim(),
    last_name: $("#last-name").value.trim(),
    date_of_birth: $("#birthday").value,
    proxy: $("#proxy").value.trim(),
    invite_code: $("#invite-code").value.trim(),
    language: $("#language").value,
    otp_timeout: Number($("#otp-timeout").value),
    consent: $("#consent").checked,
  };
  if (mode === "single") {
    return [
      {
        ...common,
        email: $("#email").value.trim(),
        otp_url:
          $("#otp-mode").value === "auto" ? $("#otp-url").value.trim() : "",
      },
    ];
  }
  const lines = $("#batch-accounts")
    .value.split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length || lines.length > 50)
    throw new Error("每批请填写 1 至 50 个邮箱");
  const proxies = $("#proxy-pool")
    .value.split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.map((line, index) => {
    const separator = line.indexOf("----");
    const email = (separator === -1 ? line : line.slice(0, separator)).trim();
    const otp_url = separator === -1 ? "" : line.slice(separator + 4).trim();
    return {
      ...common,
      email,
      otp_url,
      proxy: proxies.length ? proxies[index % proxies.length] : common.proxy,
    };
  });
}

async function submitRegistration(event) {
  event.preventDefault();
  const button = $("#submit-register");
  const error = $("#form-error");
  error.hidden = true;
  button.disabled = true;
  try {
    const { tasks: created } = await api("/tasks", {
      method: "POST",
      body: JSON.stringify({ accounts: collectAccounts() }),
    });
    selectedId = created[0].id;
    $("#manual-otp").value = "";
    notify(`已创建 ${created.length} 个注册任务`);
    await refresh();
  } catch (reason) {
    error.textContent = reason.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
}

function renderMetrics() {
  const counts = {
    total: tasks.length,
    active: tasks.filter((task) => activeStatuses.has(task.status)).length,
    verified: tasks.filter((task) => task.verified).length,
    attention: tasks.filter(attention).length,
  };
  for (const [name, value] of Object.entries(counts))
    $(`#count-${name}`).textContent = String(value).padStart(2, "0");
  $("#history-count").textContent = String(tasks.length);
}

function renderRows() {
  const query = $("#search").value.trim().toLowerCase();
  const filter = $("#status-filter").value;
  const rows = tasks.filter(
    (task) =>
      `${task.email} ${task.id}`.toLowerCase().includes(query) &&
      (filter === "all" ||
        (filter === "active" && activeStatuses.has(task.status)) ||
        (filter === "verified" && task.verified) ||
        (filter === "attention" && attention(task))),
  );
  const fragment = document.createDocumentFragment();
  for (const task of rows) {
    const row = node("tr");
    const account = node("td");
    const email = node("span", "email-cell", task.email);
    email.title = task.email;
    account.append(email, node("span", "task-id", task.id.slice(0, 12)));
    const status = node("td");
    status.append(
      node(
        "span",
        `badge ${task.status}`,
        stateNames[task.status] || task.status,
      ),
    );
    const verification = node(
      "td",
      "",
      `${task.registered ? "已创建" : "未确认"} / ${task.verified ? "已验证" : "未验证"}`,
    );
    const date = node("td", "mono", dateLabel(task.created_at));
    const actions = node("td");
    const actionsWrap = node("div", "row-actions");
    const view = node(
      "button",
      "text-button",
      task.id === selectedId ? "查看中" : "查看进度",
    );
    view.type = "button";
    view.addEventListener("click", () => {
      selectedId = task.id;
      $("#manual-otp").value = "";
      renderDetail();
      renderRows();
      $(".progress-card").scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
    actionsWrap.append(view);
    if (task.has_credentials) {
      const download = node("button", "text-button", "导出 ↗");
      download.type = "button";
      download.addEventListener("click", () => exportTask(task.id));
      actionsWrap.append(download);
    }
    actions.append(actionsWrap);
    row.append(account, status, verification, date, actions);
    fragment.append(row);
  }
  $("#task-rows").replaceChildren(fragment);
  $("#empty-state").hidden = rows.length > 0;
  $("#empty-state h3").textContent = tasks.length
    ? "没有匹配的任务。"
    : "第一封邮件，从这里出发。";
  $("#empty-state p").textContent = tasks.length
    ? "试试其他邮箱或状态筛选。"
    : "还没有注册任务。填写上方资料，创建你的第一个账号。";
  $("#table-summary").textContent = `${rows.length} / ${tasks.length} 条记录`;
}

function renderDetail() {
  const task = tasks.find((item) => item.id === selectedId);
  const currentIndex = task ? (stageIndex[task.stage] ?? -1) : -1;
  const steps = document.createDocumentFragment();
  flowSteps.forEach(([label, caption], index) => {
    const item = node(
      "li",
      `step${index < currentIndex ? " done" : index === currentIndex ? " current" : ""}`,
    );
    const number = node(
      "span",
      "step-number",
      index < currentIndex ? "✓" : String(index + 1).padStart(2, "0"),
    );
    const copy = node("div", "step-copy");
    copy.append(node("strong", "", label), node("span", "", caption));
    item.append(number, copy);
    steps.append(item);
  });
  $("#steps").replaceChildren(steps);
  $("#otp-panel").hidden = !task || task.status !== "waiting_otp";
  $("#cancel-task").hidden = !task || !activeStatuses.has(task.status);
  $("#export-task").hidden = !task || !task.has_credentials;
  $("#progress-badge").textContent = task
    ? stateNames[task.status] || task.status
    : "就绪";
  $("#current-account").replaceChildren(
    document.createTextNode(task ? task.email : "等待新任务"),
    node(
      "span",
      "",
      task
        ? `TASK / ${task.id.slice(0, 12)} · ${task.otp_mode === "auto" ? "自动轮询" : "手动验证码"}`
        : "创建注册后，在这里跟踪每一步。",
    ),
  );
  $("#task-note").textContent = task
    ? task.message +
      (task.error_code ? ` [${task.error_code}]` : "") +
      (task.invite_status !== "not_requested"
        ? ` · 邀请码：${task.invite_status}`
        : "") +
      (task.own_invite ? ` · 自己的邀请码：${task.own_invite}` : "")
    : "任务开始后会实时显示服务端响应。";
  $("#task-note").classList.toggle("error", Boolean(task && attention(task)));
  $("#otp-hint").textContent =
    task?.otp_mode === "auto"
      ? "自动轮询中，也可手动提交"
      : "填写最新收到的 6 位验证码";
  $("#event-card").hidden = !task || !task.events.length;
  if (task) {
    const events = document.createDocumentFragment();
    for (const event of [...task.events].reverse()) {
      const item = node("li", event.kind);
      const time = node("time", "", dateLabel(event.at, true));
      time.dateTime = event.at;
      item.append(time, node("span", "", event.message));
      events.append(item);
    }
    $("#event-list").replaceChildren(events);
  }
}

async function refresh(manual = false) {
  if (loading) return;
  loading = true;
  try {
    const data = await api("/tasks");
    tasks = data.tasks;
    if (!selectedId && tasks.length) selectedId = tasks[0].id;
    renderMetrics();
    renderRows();
    renderDetail();
    $("#connection-text").textContent = "本地服务已连接";
    $("#connection-dot").className = "status-dot online";
    if (manual) notify("记录已更新");
  } catch (error) {
    $("#connection-text").textContent = "本地服务未连接";
    $("#connection-dot").className = "status-dot offline";
    if (manual) notify(error.message, true);
  } finally {
    loading = false;
  }
}

async function taskAction(action, button, body) {
  if (!selectedId) return;
  button.disabled = true;
  try {
    await api(`/tasks/${selectedId}/${action}`, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (action === "otp") $("#manual-otp").value = "";
    notify(
      {
        otp: "验证码已提交",
        resend: "已请求重发验证码",
        cancel: "已请求停止任务",
      }[action],
    );
    await refresh();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function downloadJson(data, name) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json;charset=utf-8",
    }),
  );
  const link = node("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportTask(taskId) {
  try {
    const data = await api(`/tasks/${taskId}/credentials`);
    downloadJson(data, `muse-account-${taskId.slice(0, 12)}.json`);
    notify("凭据已导出，请妥善保管下载文件");
  } catch (error) {
    notify(error.message, true);
  }
}

$("#register-form").addEventListener("submit", submitRegistration);
for (const tab of document.querySelectorAll("[data-mode]"))
  tab.addEventListener("click", () => updateMode(tab.dataset.mode));
$("#otp-mode").addEventListener("change", () => {
  const auto = $("#otp-mode").value === "auto";
  $("#otp-url-field").hidden = !auto;
  $("#otp-url").required = auto;
  $("#otp-url").disabled = !auto;
});
$("#submit-otp").addEventListener("click", () => {
  const code = $("#manual-otp").value.trim();
  if (!/^[0-9]{6}$/.test(code)) return notify("请输入 6 位数字验证码", true);
  taskAction("otp", $("#submit-otp"), { code });
});
$("#manual-otp").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    $("#submit-otp").click();
  }
});
$("#resend-otp").addEventListener("click", () =>
  taskAction("resend", $("#resend-otp")),
);
$("#cancel-task").addEventListener("click", () =>
  taskAction("cancel", $("#cancel-task")),
);
$("#export-task").addEventListener("click", () => exportTask(selectedId));
$("#search").addEventListener("input", renderRows);
$("#status-filter").addEventListener("change", renderRows);
$("#refresh").addEventListener("click", () => refresh(true));
$("#export-all").addEventListener("click", async () => {
  const exportable = tasks.filter((task) => task.has_credentials);
  if (!exportable.length) return notify("暂无可导出的账号凭据");
  const button = $("#export-all");
  button.disabled = true;
  try {
    const accounts = [];
    const errors = [];
    for (const task of exportable) {
      try {
        accounts.push(await api(`/tasks/${task.id}/credentials`));
      } catch (error) {
        errors.push({ task_id: task.id, message: error.message });
      }
    }
    downloadJson(
      { exported_at: new Date().toISOString(), accounts, errors },
      `muse-accounts-${new Date().toISOString().slice(0, 10)}.json`,
    );
    notify(
      `已导出 ${accounts.length} 个账号${errors.length ? `，${errors.length} 个未能解密` : ""}`,
      errors.length > 0,
    );
  } finally {
    button.disabled = false;
  }
});
$("#guide-open").addEventListener("click", () => $("#guide").showModal());
$("#guide-close").addEventListener("click", () => $("#guide").close());
$("#guide").addEventListener("click", (event) => {
  if (event.target === $("#guide")) {
    const rect = event.target.getBoundingClientRect();
    if (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    )
      event.target.close();
  }
});
for (const link of document.querySelectorAll("[data-nav]"))
  link.addEventListener("click", () => {
    for (const other of document.querySelectorAll("[data-nav]"))
      other.classList.toggle("active", other === link);
  });
const today = new Date();
const adultYear = today.getFullYear() - 18;
const adultDay = Math.min(
  today.getDate(),
  new Date(adultYear, today.getMonth() + 1, 0).getDate(),
);
$("#birthday").max =
  `${adultYear}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(adultDay).padStart(2, "0")}`;
updateMode("single");
renderDetail();
api("/health")
  .then((data) => {
    $("#worker-label").textContent = `纯 HTTP · ${data.workers} 个并发任务`;
  })
  .catch(() => {});
refresh();
setInterval(() => {
  if (!document.hidden) refresh();
}, 1800);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
