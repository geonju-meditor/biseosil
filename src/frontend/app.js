const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const AGENTS = {
  intake: { emoji: "🧭", name: "팀장", role: "입력 접수·분배" },
  collector: { emoji: "📥", name: "수집 비서", role: "메시지 정리·개인정보 가림·지시문 탐지" },
  "collector-kakao": { emoji: "💬", name: "카톡 수집 비서", role: "대화 내보내기 정리·개인정보 가림·지시문 탐지" },
  "collector-mail": { emoji: "📧", name: "메일 수집 비서", role: "메일 나누기·개인정보 가림·지시문 탐지" },
  "collector-calendar": { emoji: "🗓️", name: "캘린더 수집 비서", role: "기존 일정 읽기(충돌 확인용)" },
  lead: { emoji: "🧭", name: "팀장", role: "우선순위·작업 지시" },
  todo: { emoji: "✅", name: "할 일 비서", role: "내 할 일 + 업무 장부(누가 뭘 맡았나)" },
  schedule: { emoji: "📅", name: "일정 비서", role: "날짜 계산 도구로 일정 정리" },
  reply: { emoji: "✉️", name: "답장 비서", role: "답장 빚·초안" },
  review: { emoji: "🔍", name: "검토 비서", role: "근거 확인·중복 제거·충돌 계산" },
};
const CHANNEL = { kakao: "카카오톡", mail: "메일", calendar: "캘린더" };
const STATUS_TEXT = { wait: "대기", start: "작업 중", done: "완료", error: "오류", retry: "다시 시도" };
const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

let result = null;
let timerHandle = null;
let running = false;
const toolCalls = {};

// ---------- 입력 ----------
$("#sample-btn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/samples");
    const data = await res.json();
    await run({ me: data.me, inputs: data.samples.map((s) => ({ text: s.text, name: s.name, channel: s.channel })) });
  } catch {
    showFormError("샘플을 불러오지 못했습니다. 새로고침 후 다시 시도해 주세요.");
  }
});

$("#run-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inputs = [];
  const files = [...$("#files").files];
  if (files.length > 6) return showFormError("파일은 한 번에 6개까지 넣을 수 있습니다.");
  for (const f of files) inputs.push({ text: await f.text(), name: f.name.replace(/\.(txt|eml|ics)$/i, "") });
  const pasted = $("#paste").value.trim();
  if (pasted) inputs.push({ text: pasted, name: null });
  if (!inputs.length) return showFormError("파일을 고르거나 대화·메일을 붙여넣어 주세요.");
  if (inputs.length > 6) return showFormError("입력은 한 번에 6개까지 넣을 수 있습니다.");
  const total = inputs.reduce((n, r) => n + r.text.length, 0);
  if (total > 30000) return showFormError(`입력이 너무 깁니다. 합계 30,000자 이하로 줄여 주세요 (현재 ${total.toLocaleString()}자).`);
  await run({ me: $("#me").value.trim() || null, inputs });
});

$("#restart-btn").addEventListener("click", () => {
  $("#result-section").hidden = true;
  $("#board-section").hidden = true;
  $("#intro").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
});

function showFormError(msg) {
  const el = $("#form-error");
  el.textContent = msg;
  el.hidden = !msg;
}

// ---------- 실행 + SSE ----------
async function run(payload) {
  if (running) return;
  running = true;
  showFormError("");
  setBusy(true);
  $("#result-section").hidden = true;
  $("#board").innerHTML = "";
  $("#log").innerHTML = "";
  $("#board-section").hidden = false;
  $("#board-section").scrollIntoView({ behavior: "smooth", block: "start" });
  startTimer();
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let msg = "요청을 처리하지 못했습니다.";
      try { msg = (await res.json()).error.message; } catch { /* keep default */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let gotResult = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "RUN_ERROR") throw new Error(ev.message);
        if (ev.type === "CUSTOM" && ev.name === "workflow.result") { gotResult = true; showResult(ev.value); }
        else handleAgUi(ev);
      }
    }
    if (!gotResult) throw new Error("연결이 끊겼습니다. 다시 시도해 주세요.");
  } catch (err) {
    $("#intro").hidden = false;
    showFormError(`⚠️ ${err.message || "문제가 생겼습니다."}`);
    $("#board-status").textContent = "작업이 중단되었습니다.";
    $("#form-error").scrollIntoView({ behavior: "smooth", block: "center" });
  } finally {
    stopTimer();
    setBusy(false);
    running = false;
  }
}

function setBusy(busy) {
  for (const b of document.querySelectorAll("#sample-btn, #run-form button")) {
    b.disabled = busy;
    b.setAttribute("aria-busy", String(busy));
  }
  $("#sample-btn").textContent = busy ? "⏳ 비서들이 일하는 중…" : "✨ 샘플로 바로 해보기";
}

function startTimer() {
  const t0 = performance.now();
  clearInterval(timerHandle);
  timerHandle = setInterval(() => { $("#timer").textContent = `${((performance.now() - t0) / 1000).toFixed(1)}초`; }, 100);
}
function stopTimer() { clearInterval(timerHandle); }

// ---------- 작업 보드 ----------
// AG-UI 이벤트(RUN_*, STEP_*, CUSTOM, TOOL_CALL_*)를 진행판에 반영한다.
function handleAgUi(ev) {
  switch (ev.type) {
    case "CUSTOM":
      if (ev.name === "workflow.graph") buildBoard(ev.value);
      else if (ev.name === "agent.progress") handleEvent(ev.value);
      break;
    case "TOOL_CALL_START":
      toolCalls[ev.toolCallId] = { name: ev.toolCallName, node: ev.parentMessageId, args: "" };
      break;
    case "TOOL_CALL_ARGS":
      if (toolCalls[ev.toolCallId]) toolCalls[ev.toolCallId].args += ev.delta;
      break;
    case "TOOL_CALL_RESULT": {
      const call = toolCalls[ev.toolCallId];
      if (!call) break;
      let args = {};
      try { args = JSON.parse(call.args); } catch { /* ignore */ }
      handleEvent({ node: call.node, status: "tool", data: { tool: call.name, input: args.expression, output: ev.content } });
      break;
    }
    case "RUN_FINISHED":
      $("#board-status").textContent = "모든 비서가 작업을 마쳤습니다.";
      break;
  }
}

function buildBoard(graph) {
  const board = $("#board");
  board.innerHTML = "";
  const stages = {};
  for (const n of graph.nodes) (stages[n.stage] ||= []).push(n);
  const stageNames = ["접수", "수집 (동시)", "지시", "전문 비서 (동시)", "검토"];
  Object.keys(stages).sort().forEach((s) => {
    const li = document.createElement("li");
    li.className = "stage";
    li.innerHTML = `<p class="stage-name">${esc(stageNames[s] || "")}</p><div class="stage-cards"></div>`;
    for (const n of stages[s]) {
      const kind = n.id.startsWith("collect-") ? `collector-${n.channel || "kakao"}` : n.id;
      const a = AGENTS[kind];
      const card = document.createElement("article");
      card.className = "agent wait";
      card.id = `node-${n.id}`;
      card.innerHTML = `
        <header><span class="emoji" aria-hidden="true">${a.emoji}</span>
          <div><h3>${esc(n.id.startsWith("collect-") ? `${a.name} #${n.id.split("-")[1]}` : a.name)}</h3><p class="role">${esc(a.role)}</p></div>
          <span class="pill">${STATUS_TEXT.wait}</span></header>
        <p class="detail"></p><ul class="tools"></ul><pre class="tail" hidden></pre>`;
      li.querySelector(".stage-cards").append(card);
    }
    board.append(li);
  });
  $("#mode-badge").hidden = graph.mode !== "deterministic";
}

function setStatus(card, status) {
  card.classList.remove("wait", "start", "done", "error", "retry");
  card.classList.add(status);
  $(".pill", card).textContent = STATUS_TEXT[status] || status;
}

function log(text) {
  const li = document.createElement("li");
  li.textContent = `${$("#timer").textContent} · ${text}`;
  $("#log").append(li);
}

function handleEvent(ev) {
  const id = ev.node;
  if (ev.agent === "collector" && ev.status === "start") {
    const intake = document.getElementById("node-intake");
    if (intake && !intake.classList.contains("done")) { setStatus(intake, "done"); $(".detail", intake).textContent += " 분배 완료."; }
  }
  const card = document.getElementById(`node-${id}`);
  if (!card) return;
  const name = $("h3", card).textContent;
  const d = ev.data || {};
  const detail = $(".detail", card);
  const tail = $(".tail", card);
  switch (ev.status) {
    case "start":
      setStatus(card, "start");
      if (id === "intake") detail.textContent = `입력 ${d.inputs}개를 받아 채널별 수집 비서에게 나눠 줍니다.`;
      else if (id === "lead") detail.textContent = `'${d.me || "나"}' 기준, ${d.reference_date} 시점으로 전체를 훑는 중…`;
      else if (ev.agent === "collector") detail.textContent = `${CHANNEL[d.channel] || ""} '${d.room}' 정리 중…`;
      else if (d.brief) detail.textContent = `팀장 지시: ${d.brief}`;
      else detail.textContent = "작업 중…";
      $("#board-status").textContent = `${name} 작업 시작`;
      log(`${name} 시작`);
      break;
    case "delta":
      tail.hidden = false;
      tail.textContent = `…${d.tail}`;
      $(".pill", card).textContent = `작성 중 ${d.chars}자`;
      break;
    case "tool": {
      const li = document.createElement("li");
      li.innerHTML = `🛠️ <code>${esc(d.tool)}</code>("${esc(d.input)}") → <b>${esc(d.output)}</b>`;
      $(".tools", card).append(li);
      log(`${name} 도구 호출: ${d.tool}("${d.input}") → ${d.output}`);
      break;
    }
    case "retry":
      setStatus(card, "retry");
      detail.textContent = d.reason;
      log(`${name}: ${d.reason}`);
      break;
    case "done":
      setStatus(card, "done");
      tail.hidden = true;
      if (id === "intake") { detail.textContent += " 분배 완료."; break; }
      if (ev.agent === "collector") {
        const pii = Object.values(d.pii || {}).reduce((a, b) => a + b, 0);
        detail.innerHTML = (d.channel === "calendar"
          ? `'${esc(d.room)}' 기존 일정 ${d.events}개 (충돌 확인용)`
          : `'${esc(d.room)}' ${d.channel === "mail" ? "메일" : "메시지"} ${d.messages}개 · 보낸 사람 ${d.participants.length}명`)
          + (pii ? ` · <span class="tag">개인정보 ${pii}건 가림</span>` : "")
          + (d.flagged.length ? ` · <span class="tag warn">🛡️ 지시문 의심 ${d.flagged.length}건 격리</span>` : "");
      } else if (id === "lead") {
        detail.textContent = d.summary;
      } else if (id === "review") {
        detail.textContent = `근거 없는 항목·중복 ${d.removed}건 제거, 일정 충돌 ${d.conflicts}건 확인`;
      } else {
        detail.textContent = `${d.count}건 정리 완료`;
      }
      $("#board-status").textContent = `${name} 완료`;
      log(`${name} 완료`);
      break;
    case "error":
      setStatus(card, "error");
      tail.hidden = true;
      detail.textContent = `⚠️ ${d.message || "이 비서는 결과를 내지 못했습니다."} 나머지 결과는 그대로 보여 드립니다.`;
      log(`${name} 오류`);
      break;
  }
}

// ---------- 결과 ----------
function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const w = WEEK[new Date(y, m - 1, d).getDay()];
  return `${m}/${d}(${w})`;
}
function dday(iso) {
  if (!iso || !result) return "";
  const ref = new Date(result.referenceDate);
  const diff = Math.round((new Date(iso) - ref) / 86400000);
  if (diff < 0) return `<span class="dday late">지남</span>`;
  if (diff === 0) return `<span class="dday today">오늘</span>`;
  return `<span class="dday${diff <= 2 ? " soon" : ""}">D-${diff}</span>`;
}
function evidenceHtml(ev) {
  if (!ev) return "";
  return `<details class="evidence"><summary><span class="q">“${esc(ev.quote)}”</span>
    <span class="meta">${esc(ev.room || "")} · ${esc(ev.sender || "")} · ${esc(ev.when || "")}</span></summary>
    <p class="orig"><span class="muted">원문 (${esc(ev.message_id)})</span><br>${esc(ev.text || "")}</p></details>`;
}

function showResult(data) {
  result = data;
  for (const t of result.todos) t.done = false;
  for (const e of result.events) e.selected = true;
  $("#board-status").textContent = "모든 비서가 작업을 마쳤습니다.";
  const s = data.stats;
  $("#stats").innerHTML = `
    <div class="stat"><b>${s.manualMin}분</b><span>직접 읽고 정리하면 (추정)</span></div>
    <div class="stat arrow" aria-hidden="true">→</div>
    <div class="stat good"><b>${s.elapsedSec}초</b><span>비서실 처리 시간</span></div>
    <div class="stat"><b>${s.messages}개</b><span>읽은 메시지·메일 · 출처 ${data.inputs.length}곳</span></div>
    <p class="muted small formula">추정식: ${esc(s.formula)}</p>`;
  $("#result-mode").hidden = data.mode !== "deterministic";
  const lead = data.lead;
  $("#summary").innerHTML = lead
    ? `<p><b>🧭 팀장 요약 (${esc(data.me || "나")} 기준 · ${fmtDate(data.referenceDate)})</b></p><p>${esc(lead.summary)}</p>`
    : `<p class="muted">팀장 요약을 만들지 못했습니다. 아래 결과는 각 비서가 만든 것입니다.</p>`;
  const alerts = [];
  for (const c of data.review.conflicts) alerts.push(`<li>⏰ ${esc(c)}</li>`);
  for (const c of data.review.crunch) alerts.push(`<li>🔥 ${fmtDate(c.date)}에 할 일 ${c.count}개가 몰려 있습니다: ${c.items.map(esc).join(", ")}</li>`);
  for (const w of lead?.warnings || []) alerts.push(`<li>💡 ${esc(w)}</li>`);
  const errs = Object.entries(data.errors || {});
  for (const [k, m] of errs) alerts.push(`<li class="err">⚠️ ${esc(AGENTS[k]?.name || k)}: ${esc(m)}</li>`);
  $("#alerts").innerHTML = alerts.length ? `<ul class="alert-list">${alerts.join("")}</ul>` : "";
  renderAll();
  $("#result-section").hidden = false;
  $("#intro").hidden = true;
  selectTab("tab-todos");
  $("#result-section").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#result-title").focus({ preventScroll: true });
}

function renderAll() {
  renderTodos(); renderEvents(); renderReplies(); renderLedger(); renderReview();
  const counts = {
    todos: result.todos.length, events: result.events.length,
    replies: result.replies.length, ledger: result.ledger.assignments.length + result.ledger.decisions.length,
    review: result.review.removed.length + result.inputs.reduce((n, r) => n + r.flagged.length, 0),
  };
  for (const [k, v] of Object.entries(counts)) $(`[data-count="${k}"]`).textContent = v;
}

const PRI = { high: "급함", mid: "보통", low: "여유" };
function renderTodos() {
  const pane = $("#pane-todos");
  if (!result.todos.length) { pane.innerHTML = `<p class="empty">내 할 일을 찾지 못했습니다.</p>`; return; }
  pane.innerHTML = `<div class="pane-actions"><button class="btn small" data-act="copy-todos">📋 할 일 목록 복사</button></div>
    <ul class="items">${result.todos.map((t, i) => `
    <li class="item ${t.done ? "is-done" : ""}">
      <div class="row"><input type="checkbox" id="todo-${i}" data-act="todo-done" data-i="${i}" ${t.done ? "checked" : ""}>
        <label for="todo-${i}" class="title">${esc(t.title)}</label>
        <span class="pri ${t.priority}">${PRI[t.priority]}</span>
        <button class="icon" data-act="del" data-kind="todos" data-i="${i}" aria-label="${esc(t.title)} 빼기">✕</button></div>
      <p class="sub">${t.due ? `${dday(t.due)} ${fmtDate(t.due)} 까지` : "기한 없음"} ${t.due_text ? `<span class="muted">(“${esc(t.due_text)}”)</span>` : ""} · ${esc(t.room)}</p>
      ${evidenceHtml(t.evidence)}</li>`).join("")}</ul>`;
}

const ATTEND = { confirmed: "참석 확정", requested: "참석 요청 받음", unknown: "참석 미정" };
function renderEvents() {
  const pane = $("#pane-events");
  if (!result.events.length) { pane.innerHTML = `<p class="empty">일정을 찾지 못했습니다.</p>`; return; }
  const n = result.events.filter((e) => e.selected).length;
  pane.innerHTML = `<div class="pane-actions"><button class="btn primary small" data-act="ics" ${n ? "" : "disabled"}>📅 선택한 일정 ${n}개 캘린더 파일(.ics)로 받기</button></div>
    <ul class="items">${result.events.map((e, i) => `
    <li class="item">
      <div class="row"><input type="checkbox" id="ev-${i}" data-act="ev-sel" data-i="${i}" ${e.selected ? "checked" : ""}>
        <label for="ev-${i}" class="title">${esc(e.title)}</label>
        <span class="pri ${e.my_attendance === "confirmed" ? "low" : "mid"}">${ATTEND[e.my_attendance]}</span>
        <button class="icon" data-act="del" data-kind="events" data-i="${i}" aria-label="${esc(e.title)} 빼기">✕</button></div>
      <p class="sub">${dday(e.date)} <b>${fmtDate(e.date)}</b> ${esc(e.start || "시간 미정")}${e.end ? `~${esc(e.end)}` : ""}${e.location ? ` · 📍${esc(e.location)}` : ""} · ${esc(e.room)}</p>
      ${evidenceHtml(e.evidence)}</li>`).join("")}</ul>`;
}

function renderReplies() {
  const pane = $("#pane-replies");
  const mine = result.replies.map((r, i) => ({ ...r, i })).filter((r) => r.direction === "i_owe");
  const theirs = result.replies.map((r, i) => ({ ...r, i })).filter((r) => r.direction === "they_owe");
  const card = (r) => `
    <li class="item ${r.sent ? "is-done" : ""}">
      <div class="row"><span class="title">${esc(r.counterpart)} — ${esc(r.topic)}</span>
        ${r.sent ? `<span class="pri low">답장함</span>` : ""}
        <button class="icon" data-act="del" data-kind="replies" data-i="${r.i}" aria-label="${esc(r.topic)} 빼기">✕</button></div>
      <p class="sub">${esc(r.room)}</p>
      ${evidenceHtml(r.evidence)}
      ${r.draft != null ? `<label class="draft-label" for="draft-${r.i}">답장 초안 <span class="badge ai small">AI 생성</span> <span class="muted">[ ]는 직접 채워 주세요</span></label>
      <textarea id="draft-${r.i}" class="draft" data-act="draft" data-i="${r.i}" rows="3">${esc(r.draft)}</textarea>
      <div class="cta-row"><button class="btn small" data-act="copy-draft" data-i="${r.i}">📋 복사</button>
        ${r.direction === "i_owe" && !r.sent ? `<button class="btn small" data-act="mark-sent" data-i="${r.i}">✔ 답장했어요</button>` : ""}</div>` : ""}
    </li>`;
  pane.innerHTML = `<p class="muted small">비서실은 메시지를 직접 보내지 않습니다. 초안을 고친 뒤 복사해서 카카오톡이나 메일에 붙여넣으세요.</p>
    <h3 class="group">내가 답해야 할 것 (${mine.length})</h3>${mine.length ? `<ul class="items">${mine.map(card).join("")}</ul>` : `<p class="empty">없음</p>`}
    <h3 class="group">내가 받아야 할 것 (${theirs.length})</h3>${theirs.length ? `<ul class="items">${theirs.map(card).join("")}</ul>` : `<p class="empty">없음</p>`}`;
}

const ASTATUS = { assigned: "진행 중", done: "완료", unassigned: "담당 없음" };
function renderLedger() {
  const L = result.ledger;
  const pane = $("#pane-ledger");
  pane.innerHTML = `
    ${L.open_questions.length ? `<div class="open"><h3 class="group">🙋 아직 아무도 정하지 않은 것</h3><ul>${L.open_questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></div>` : ""}
    <h3 class="group">누가 뭘 맡았나 (${L.assignments.length})</h3>
    ${L.assignments.length ? `<div class="table-wrap"><table><thead><tr><th scope="col">담당</th><th scope="col">할 일</th><th scope="col">상태</th><th scope="col">근거</th></tr></thead><tbody>
      ${L.assignments.map((a) => `<tr class="${a.owner === result.me ? "me" : ""}"><td>${esc(a.owner)}</td><td>${esc(a.task)}<br><span class="muted small">${esc(a.room)}</span></td><td><span class="st ${a.status}">${ASTATUS[a.status]}</span></td><td>${evidenceHtml(a.evidence)}</td></tr>`).join("")}
    </tbody></table></div>` : `<p class="empty">없음</p>`}
    <h3 class="group">정해진 것 (${L.decisions.length})</h3>
    ${L.decisions.length ? `<ul class="items">${L.decisions.map((d) => `<li class="item"><p class="title">${esc(d.decision)}</p><p class="sub">${esc(d.room)}</p>${evidenceHtml(d.evidence)}</li>`).join("")}</ul>` : `<p class="empty">없음</p>`}`;
}

function renderReview() {
  const pane = $("#pane-review");
  const flagged = result.inputs.flatMap((r) => r.flagged.map((id) => ({ id, ...(result.source?.[id] || {}) })));
  const pii = result.inputs.reduce((n, r) => n + Object.values(r.pii).reduce((a, b) => a + b, 0), 0);
  pane.innerHTML = `
    <p>검토 비서는 모든 항목의 인용문을 원문과 대조해, 근거가 없거나 지시문 의심 메시지에서 나온 항목을 뺐습니다.</p>
    <h3 class="group">🛡️ 지시문 의심 메시지 (${flagged.length})</h3>
    ${flagged.length ? `<ul class="items">${flagged.map((f) => `<li class="item warn"><p class="sub">${esc(f.room)} · ${esc(f.sender)} · ${esc(f.when)}</p><p>“${esc(f.text)}”</p><p class="muted small">→ 비서들은 이 문장을 지시로 따르지 않고 데이터로만 다뤘습니다.</p></li>`).join("")}</ul>` : `<p class="empty">없음</p>`}
    <h3 class="group">🗑️ 검토에서 뺀 항목 (${result.review.removed.length})</h3>
    ${result.review.removed.length ? `<ul class="items">${result.review.removed.map((r) => `<li class="item"><p class="title">${esc(r.title)}</p><p class="sub">${esc(r.reason)}</p></li>`).join("")}</ul>` : `<p class="empty">모든 항목이 원문 근거 확인을 통과했습니다.</p>`}
    <h3 class="group">🔒 개인정보</h3><p>${pii ? `전화번호·이메일·계좌번호 등 ${pii}건을 AI에 보내기 전에 가렸습니다.` : "가릴 개인정보가 없었습니다."} 입력 원문은 서버에 저장하지 않습니다.</p>`;
}

// ---------- 동작 ----------
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.dataset.act === "todo-done") { result.todos[t.dataset.i].done = t.checked; t.closest(".item").classList.toggle("is-done", t.checked); }
  if (t.dataset.act === "ev-sel") { result.events[t.dataset.i].selected = t.checked; renderEvents(); $(`#ev-${t.dataset.i}`).focus(); }
});
document.addEventListener("input", (e) => {
  if (e.target.dataset.act === "draft") result.replies[e.target.dataset.i].draft = e.target.value;
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || b.tagName === "INPUT" || b.tagName === "TEXTAREA") return;
  const i = Number(b.dataset.i);
  switch (b.dataset.act) {
    case "del":
      result[b.dataset.kind].splice(i, 1);
      renderAll();
      toast("목록에서 뺐습니다.");
      break;
    case "copy-todos": {
      const text = result.todos.map((t) => `${t.done ? "☑" : "☐"} ${t.title}${t.due ? ` (~${fmtDate(t.due)})` : ""}`).join("\n");
      await copy(text);
      break;
    }
    case "copy-draft":
      await copy(result.replies[i].draft || "");
      break;
    case "mark-sent": {
      const r = result.replies[i];
      if (await confirmDialog("답장했다고 표시할까요?", `<p><b>${esc(r.counterpart)}</b>에게 “${esc(r.topic)}” 답장을 보냈다고 표시합니다. 비서실은 메시지를 직접 보내지 않습니다.</p>`)) {
        r.sent = true;
        renderReplies();
        toast("답장함으로 표시했습니다.");
      }
      break;
    }
    case "ics": await exportIcs(); break;
  }
});

async function exportIcs() {
  const chosen = result.events.filter((e) => e.selected);
  const body = `<p>아래 일정 ${chosen.length}개로 캘린더 파일을 만듭니다. 날짜와 시간이 맞는지 확인해 주세요.</p>
    <ul class="confirm-list">${chosen.map((e) => `<li><b>${fmtDate(e.date)} ${esc(e.start || "종일")}</b> ${esc(e.title)}</li>`).join("")}</ul>`;
  if (!(await confirmDialog("캘린더 파일 만들기", body))) return;
  try {
    const res = await fetch("/api/ics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: chosen.map((e) => ({ title: e.title, date: e.date, start: e.start, end: e.end, location: e.location, note: e.evidence?.quote })) }),
    });
    if (!res.ok) throw new Error((await res.json()).error.message);
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "biseosil.ics" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("캘린더 파일을 받았습니다. 휴대폰·구글 캘린더에서 열어 추가하세요.");
  } catch (err) {
    toast(`⚠️ ${err.message || "파일을 만들지 못했습니다."}`);
  }
}

function confirmDialog(title, html) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-body").innerHTML = html;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("복사했습니다. 카카오톡에 붙여넣으세요."); }
  catch { toast("복사하지 못했습니다. 직접 선택해서 복사해 주세요."); }
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

// ---------- 탭 ----------
const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(id) {
  for (const t of tabs) {
    const on = t.id === id;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  }
}
for (const t of tabs) {
  t.addEventListener("click", () => selectTab(t.id));
  t.addEventListener("keydown", (e) => {
    const i = tabs.indexOf(t);
    const next = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : null;
    if (next !== null) { e.preventDefault(); selectTab(tabs[next].id); tabs[next].focus(); }
  });
}
