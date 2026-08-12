const state = {
  overview: null,
  papers: [],
  selectedId: null,
  detail: null,
  activeTab: "overview",
  view: "workspace",
  remoteResults: [],
  activeJob: null,
  jobTrace: null,
};

const statusLabels = {
  discovered: "已入库",
  screened: "已入库",
  venue_verified: "已入库",
  selected: "已入库",
  extracted: "已入库",
  drafted: "已入库",
  ready_for_review: "已生成内容",
  approved: "已通过审核",
  published: "已发布",
};

const viewLabels = {
  workspace: "内容工作台",
  review: "审核队列",
  published: "发布中心",
  runs: "运行记录",
};

const viewDescriptions = {
  workspace: "搜索论文，查看 AI 生成过程与完整内容",
  review: "逐项核对事实、文案与来源证据",
  published: "自动填充发布内容，并记录人工发布结果",
  runs: "查看扫描、筛选和生成任务的执行结果",
};

const eventLabels = {
  console_imported: "从搜索结果加入论文库",
  console_duplicate_selected: "选择论文库中的已有论文",
  manual_venue_verified: "人工补充并核验来源",
  llm_request_started: "模型请求已发送",
  llm_request_completed: "模型响应已完成",
  llm_request_failed: "模型请求未完成",
  llm_rule_fallback: "模型分类失败，使用本地规则",
  publication_package_ready: "审核通过，发布包已生成",
  platform_publication_confirmed: "编辑确认平台发布完成",
  xhs_login_required: "小红书登录已失效",
  xhs_fill_failed: "小红书内容自动填充未完成",
  xhs_content_filled: "小红书图片和文本已填入",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function toast(message, error = false) {
  const node = document.createElement("div");
  node.className = `toast${error ? " is-error" : ""}`;
  node.textContent = message;
  $("#toast-region").append(node);
  setTimeout(() => node.remove(), 4200);
}

function setWorking(working, text = "任务处理中") {
  const node = $("#sync-state");
  node.classList.toggle("is-working", working);
  node.lastChild.textContent = working ? text : "数据已同步";
}

function formatDate(value) {
  if (!value) return "日期未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toLocaleDateString("zh-CN", { year: "numeric", month: "short", day: "numeric" });
}

function formatEventLabel(value = "") {
  if (eventLabels[value]) return eventLabels[value];
  if (value.includes("->")) {
    const [from, to] = value.split("->");
    return `${statusLabels[from] || from} → ${statusLabels[to] || to}`;
  }
  return value.replaceAll("_", " ");
}

function compactAuthors(authors = []) {
  if (!authors.length) return "作者未知";
  return authors.length > 2 ? `${authors.slice(0, 2).join("、")} 等 ${authors.length} 人` : authors.join("、");
}

async function loadOverview() {
  state.overview = await api("/api/overview");
  $("#metric-total").textContent = state.overview.ingested;
  $("#metric-ready").textContent = state.overview.ready;
  $("#metric-generated").textContent = state.overview.approved;
  $("#metric-published").textContent = state.overview.published;
  $("#review-badge").textContent = state.overview.ready;
  $("#model-state").textContent = state.overview.settings.model_configured
    ? `${state.overview.settings.model_name || "OpenAI 兼容模型"} 已配置`
    : "规则模式";
  renderRunCard();
}

async function loadPapers({ preserveSelection = true } = {}) {
  const oldSelection = preserveSelection ? state.selectedId : null;
  state.papers = await api("/api/papers");
  state.selectedId = oldSelection && state.papers.some((paper) => paper.id === oldSelection) ? oldSelection : null;
  renderPapers();
}

function visiblePapers() {
  const needle = state.view === "workspace" ? $("#library-search").value.trim().toLowerCase() : "";
  const filter = $("#status-filter").value;
  if (state.view === "review") {
    return state.papers.filter((paper) => paper.status === "ready_for_review");
  }
  if (state.view === "published") {
    return state.papers.filter((paper) => ["approved", "published"].includes(paper.status) && (!needle || `${paper.title} ${paper.arxiv_id}`.toLowerCase().includes(needle)));
  }
  return state.papers.filter((paper) => (!filter || businessStatus(paper.status).key === filter) && (!needle || `${paper.title} ${paper.arxiv_id} ${paper.authors.join(" ")}`.toLowerCase().includes(needle)));
}

function businessStatus(status) {
  if (status === "published") return { key: "published", label: "已发布" };
  if (status === "approved") return { key: "approved", label: "已通过审核" };
  if (status === "ready_for_review") return { key: "generated", label: "已生成内容" };
  return { key: "ingested", label: "已入库" };
}

function renderPapers() {
  const papers = visiblePapers();
  $("#library-title").textContent = state.view === "review" ? "等待人工审核" : state.view === "published" ? "发布内容" : "候选论文";
  const root = $("#paper-list");
  if (!papers.length) {
    const emptyText = state.view === "review"
      ? "当前没有已生成、等待审核的内容。"
      : state.view === "published"
        ? "当前没有已通过审核或已发布的内容。"
        : "尝试搜索论文或切换状态。";
    root.innerHTML = `<div class="list-empty"><strong>这里暂时是空的</strong><span>${emptyText}</span></div>`;
    return;
  }
  root.innerHTML = papers.map((paper, index) => {
    const displayStatus = businessStatus(paper.status);
    return `
    <button class="paper-item ${paper.id === state.selectedId ? "is-selected" : ""}" data-paper-id="${paper.id}">
      <div class="paper-topline"><span class="paper-index">${String(index + 1).padStart(2, "0")} · ${escapeHtml(paper.arxiv_id)}</span><span class="status-tag status-${displayStatus.key}">${displayStatus.label}</span></div>
      <h3>${escapeHtml(paper.title)}</h3>
      <p>${escapeHtml(paper.abstract)}</p>
      <div class="paper-meta"><strong>${escapeHtml(paper.topic_label || paper.venue || "待分类")}</strong><span>${formatDate(paper.published_at)}</span><span class="score-line">内容分 <b>${Math.round(paper.score || 0)}</b></span></div>
    </button>
  `; }).join("");
  $$("[data-paper-id]", root).forEach((button) => button.addEventListener("click", () => selectPaper(Number(button.dataset.paperId))));
}

async function selectPaper(id) {
  state.selectedId = id;
  state.activeTab = ["review", "published"].includes(state.view) ? "xhs" : "overview";
  renderPapers();
  const panel = $("#detail-panel");
  panel.innerHTML = `<div class="empty-detail"><span class="empty-glyph skeleton"></span><h3>正在读取内容</h3></div>`;
  try {
    state.detail = await api(`/api/papers/${id}`);
    renderDetail();
  } catch (error) {
    toast(error.message, true);
  }
}

function actionLabel(status) {
  if (["ready_for_review", "approved", "published"].includes(status)) return "内容已生成";
  return "生成内容";
}

function firstText(value, keys = []) {
  if (!value) return "";
  if (typeof value === "string") return value;
  return keys.map((key) => value[key]).find(Boolean) || "";
}

function pageTags(pages = []) {
  if (!pages?.length) return "";
  return `<div class="source-pages">${pages.map((page) => `<span>PDF 第 ${escapeHtml(page)} 页</span>`).join("")}</div>`;
}

function processStep(label, note, stateName) {
  const mark = stateName === "done" || stateName === "warning" ? "✓" : "";
  return `<div class="process-step is-${stateName}"><span class="process-dot">${mark}</span><strong>${label}</strong><small>${note}</small></div>`;
}

const jobStageLabels = {
  queued: "任务排队",
  screening: "选题判断",
  source: "来源提醒",
  reading: "读取论文",
  facts: "事实抽取",
  writing: "正文生成",
  model: "模型实时响应",
  rendering: "配图导出",
  validation: "自动检查",
  review: "等待审核",
  discarded: "记录删除",
  launching: "启动发布浏览器",
  needs_login: "等待登录",
  uploading: "上传 6 张配图",
  filling: "填写标题和正文",
  filled: "内容已填入",
  published: "用户确认已发布",
};

function renderLiveGeneration(paperId) {
  const trace = state.jobTrace;
  if (!trace || trace.paperId !== paperId) return "";
  const compact = [];
  (trace.updates || []).forEach((update) => {
    const previous = compact[compact.length - 1];
    if (previous?.stage === update.stage) compact[compact.length - 1] = update;
    else compact.push(update);
  });
  const previewUpdate = [...(trace.updates || [])].reverse().find((item) => item.preview);
  const preview = previewUpdate?.preview || "";
  const finished = ["completed", "failed"].includes(trace.status);
  const publishing = trace.kind === "fill_xhs";
  const taskName = publishing ? "小红书内容填充" : "AI 生成内容";
  return `<section class="ai-live ${finished ? "is-finished" : ""}">
    <div class="ai-live-head"><div><i></i><strong>${finished ? (trace.status === "failed" ? `${taskName}未完成` : `${taskName}过程`) : `${taskName}进行中`}</strong></div><span>${escapeHtml(trace.message || "处理中")}</span><button id="dismiss-job-trace" aria-label="关闭任务过程">×</button></div>
    <div class="ai-live-steps">${compact.map((item, index) => `<div class="ai-live-step ${index === compact.length - 1 && !finished ? "is-current" : ""}"><b>${index + 1}</b><span><strong>${jobStageLabels[item.stage] || item.stage}</strong><small>${escapeHtml(item.message)}</small></span></div>`).join("")}</div>
    ${preview ? `<div class="ai-live-preview"><small>${publishing ? "待发布正文预览" : previewUpdate?.stage === "writing" ? "生成后的推文正文" : "AI 生成内容预览"}</small><pre>${escapeHtml(preview)}${finished ? "" : '<i class="typing-caret"></i>'}</pre></div>` : ""}
  </section>`;
}

function renderProcess(paper, artifacts, claims, events) {
  const hasFacts = Boolean(artifacts?.facts || claims.length);
  const hasContent = Boolean(artifacts?.caption || artifacts?.wechat_markdown);
  const validation = artifacts?.validation;
  const isReviewed = ["approved", "published"].includes(paper.status);
  const isWaitingReview = paper.status === "ready_for_review";
  const steps = [
    processStep("论文入库", paper.is_demo ? "示例数据" : "arXiv 元数据", "done"),
    processStep("内容生成", hasContent ? "正文与配图已完成" : hasFacts ? "正在生成正文" : "等待生成", hasContent ? "done" : "active"),
    processStep("内容审核", isReviewed ? "审核已通过" : isWaitingReview ? "等待人工确认" : "尚未开始", isReviewed ? "done" : isWaitingReview ? "active" : "pending"),
    processStep("内容发布", paper.status === "published" ? "发布已确认" : paper.status === "approved" ? "等待用户发布并反馈" : "尚未发布", paper.status === "published" ? "done" : paper.status === "approved" ? "active" : "pending"),
  ];
  return `<section class="process-panel"><div class="process-heading"><strong>AI 处理过程</strong><span>${events.length} 条审计记录 · 每一步均可回溯</span></div><div class="process-steps">${steps.join("")}</div></section>`;
}

function renderDetail() {
  const { paper, artifacts, claims, events } = state.detail;
  const generatingThisPaper = Boolean(
    state.activeJob && state.jobTrace?.paperId === paper.id
      && ["queued", "running"].includes(state.jobTrace?.status)
  );
  const canGenerate = !generatingThisPaper && !["ready_for_review", "approved", "published"].includes(paper.status);
  const canPublish = ["ready_for_review", "approved"].includes(paper.status);
  const canConfirm = paper.status === "approved";
  $("#detail-panel").innerHTML = `
    <div class="detail-content">
      <header class="detail-head">
        <div class="detail-kicker"><span>${escapeHtml(paper.arxiv_id)}</span><span class="status-tag status-${businessStatus(paper.status).key}">${businessStatus(paper.status).label}</span></div>
        <h2>${escapeHtml(paper.title)}</h2>
        <p class="detail-authors">${escapeHtml(compactAuthors(paper.authors))}</p>
        <div class="detail-meta"><span>发表 <b>${formatDate(paper.published_at)}</b></span><span>内容分 <b>${Math.round(paper.score || 0)}</b></span><span>来源 <b>${escapeHtml(paper.venue || "待核验")}</b></span></div>
        ${paper.venue_status !== "verified" && !paper.is_demo ? '<div class="source-reminder"><strong>来源尚未核验</strong><span>这不会阻止内容生成或审核，发布前建议补充官方来源。</span><button data-tab="evidence">查看来源</button></div>' : ""}
        <div class="detail-actions">
          <a class="button button-quiet" href="${escapeHtml(paper.pdf_url)}" target="_blank" rel="noreferrer">查看原文</a>
          ${artifacts?.caption ? '<button class="button button-quiet" id="quick-copy">复制小红书文案</button>' : ""}
          <button class="button ${canGenerate ? "button-dark" : "button-quiet"}" id="generate-button" ${canGenerate ? "" : "disabled"}>${generatingThisPaper ? "正在生成…" : actionLabel(paper.status)}</button>
        </div>
      </header>
      ${renderLiveGeneration(paper.id)}
      ${renderProcess(paper, artifacts, claims, events)}
      <div class="tabs" role="tablist">
        ${[["overview", "AI 解读"], ["xhs", `小红书全文 ${artifacts?.images?.length || 0}`], ["wechat", "公众号全文"], ["evidence", "证据与记录"]].map(([key, label]) => `<button class="tab ${state.activeTab === key ? "is-active" : ""}" data-tab="${key}">${label}</button>`).join("")}
      </div>
      <div class="tab-content" id="tab-content">${renderTab(paper, artifacts, claims, events)}</div>
    </div>`;
  $$("[data-tab]").forEach((button) => button.addEventListener("click", () => {
    state.activeTab = button.dataset.tab;
    renderDetail();
  }));
  const generate = $("#generate-button");
  if (generate && canGenerate) generate.addEventListener("click", () => generatePaper(paper.id));
  const quickCopy = $("#quick-copy");
  if (quickCopy) quickCopy.addEventListener("click", () => copyText(artifacts.caption));
  const copy = $("#copy-content");
  if (copy) copy.addEventListener("click", () => copyText(state.activeTab === "xhs" ? artifacts.caption : artifacts.wechat_markdown));
  const venueForm = $("#venue-form");
  if (venueForm) venueForm.addEventListener("submit", saveVenue);
  const publish = $("#open-publish");
  if (publish && canPublish) publish.addEventListener("click", openPublishModal);
  const confirm = $("#confirm-published");
  if (confirm && canConfirm) confirm.addEventListener("click", confirmPublished);
  const dismissTrace = $("#dismiss-job-trace");
  if (dismissTrace) dismissTrace.addEventListener("click", () => {
    state.jobTrace = null;
    renderDetail();
  });
}

function renderTab(paper, artifacts, claims, events) {
  if (state.activeTab === "xhs") {
    if (!artifacts) return emptyArtifact("生成后将在这里预览发布配图。");
    const pdfPages = artifacts.image_source === "pdf_first_pages";
    const imageLabel = pdfPages ? "PDF FIRST 6 PAGES" : "DEMO IMAGE FALLBACK";
    const imageNote = pdfPages ? "最终发布配图直接采用论文 PDF 的前六页。" : "构造示例没有真实 PDF，因此使用 6 张示例图完成离线流程。";
    return `
      <section class="content-section"><p class="summary-label">${imageLabel}</p><h3 class="section-title">发布配图</h3><p class="section-note">${imageNote}</p>
      <div class="card-strip">${artifacts.images.map((src, index) => `<a class="card-thumb" href="${src}" target="_blank" rel="noreferrer"><img src="${src}" alt="发布配图 ${index + 1}" loading="lazy" /><span>${index + 1}</span></a>`).join("")}</div></section>
      <section class="content-section"><h3 class="section-title">发布文案</h3><p class="section-note">以下为 AI 生成的完整文案，不折叠、不截断。</p><div class="content-tool"><div class="content-tool-head"><strong>小红书正文</strong><button class="copy-button" id="copy-content">复制全文</button></div><pre class="caption-preview">${escapeHtml(artifacts.caption || "")}</pre></div></section>
      ${renderPublishCallout(paper, artifacts)}`;
  }
  if (state.activeTab === "wechat") {
    if (!artifacts) return emptyArtifact("生成后将在这里预览公众号长文。");
    return `<section class="content-section"><h3 class="section-title">公众号完整稿件</h3><p class="section-note">保留完整段落结构，可直接复制到编辑器继续修改。</p><div class="content-tool"><div class="content-tool-head"><strong>公众号长文</strong><button class="copy-button" id="copy-content">复制全文</button></div><pre class="wechat-preview">${escapeHtml(artifacts.wechat_markdown || "")}</pre></div></section>${renderPublishCallout(paper, artifacts)}`;
  }
  if (state.activeTab === "evidence") {
    const canEdit = paper.status !== "published";
    return `
      <section class="content-section"><p class="summary-label">SOURCE OF TRUTH</p><h3 class="section-title">来源核验</h3>
      <div class="evidence-note">arXiv 只证明论文是预印本。标记为会议论文前，请使用会议官网或正式 proceedings 页面。</div>
      <form class="venue-form" id="venue-form">
        <div class="field"><label for="venue-name">会议名称</label><input id="venue-name" name="venue" value="${escapeHtml(paper.venue || "")}" placeholder="例如 USENIX Security Symposium" ${canEdit ? "" : "disabled"} /></div>
        <div class="field"><label for="venue-url">官方证据网址</label><input id="venue-url" name="evidence_url" type="url" value="${escapeHtml(paper.venue_evidence_url || "")}" placeholder="https://会议官网/accepted-papers/..." ${canEdit ? "" : "disabled"} /></div>
        ${canEdit ? '<button class="button button-dark" type="submit">保存核验证据</button>' : ""}
      </form></section>
      <section class="content-section"><p class="summary-label">TRACEABLE CLAIMS</p><h3 class="section-title">可追溯事实</h3><p class="section-note">AI 生成内容中的核心论断应能回到 PDF 页码或原文锚点。</p>
        ${claims.length ? `<div class="claim-list">${claims.map((claim) => `<div class="claim"><span>${escapeHtml(claim.claim_type || "事实")}</span><p>${escapeHtml(claim.claim_text)}</p><b>${claim.source_page ? `PDF 第 ${escapeHtml(claim.source_page)} 页` : escapeHtml(claim.source_anchor || "待补证据")}</b></div>`).join("")}</div>` : '<p class="abstract">尚未抽取可追溯事实。</p>'}
      </section>
      <section class="content-section"><p class="summary-label">AUDIT TRAIL</p><h3 class="section-title">处理记录</h3>
        <div class="event-list">${events.length ? events.map((event) => `<div class="event"><strong>${escapeHtml(formatEventLabel(event.event))}</strong><span>${formatDate(event.created_at)}${event.detail ? ` · ${escapeHtml(event.detail)}` : ""}</span></div>`).join("") : '<p class="abstract">暂无处理记录。</p>'}</div>
      </section>`;
  }
  const facts = artifacts?.facts;
  const validation = artifacts?.validation;
  const problem = firstText(facts?.problem, ["plain_cn", "summary", "description"]);
  const method = firstText(facts?.method, ["one_sentence", "summary", "description"]);
  const example = firstText(facts?.method, ["plain_example", "example"]);
  const results = facts?.results || [];
  const futureWork = facts?.authors_future_work || [];
  const extensions = facts?.editorial_extension || [];
  const uncertainties = facts?.uncertainties || [];
  return `
    <section class="content-section"><p class="summary-label">PAPER ABSTRACT</p><h3 class="section-title">论文摘要</h3><p class="abstract">${escapeHtml(paper.abstract)}</p></section>
    <section class="content-section"><p class="summary-label">AI FACT SHEET</p><h3 class="section-title">AI 事实底稿</h3><p class="section-note">以下信息从论文中抽取，是小红书与公众号内容的共同依据。</p>
      <div class="fact-grid">
        <div class="fact-box"><small>研究问题</small><strong>${escapeHtml(problem || paper.topic_label || "等待生成事实底稿")}</strong>${pageTags(facts?.problem?.source_pages)}</div>
        <div class="fact-box"><small>核心方法</small><strong>${escapeHtml(method || "生成后显示方法摘要")}</strong>${pageTags(facts?.method?.source_pages)}</div>
        ${example ? `<div class="fact-box is-wide"><small>通俗解释</small><strong>${escapeHtml(example)}</strong></div>` : ""}
      </div>
    </section>
    ${results.length ? `<section class="content-section"><p class="summary-label">RESULTS</p><h3 class="section-title">论文结果</h3><ul class="result-list">${results.map((result) => `<li class="result-item">${escapeHtml(result.claim || result.summary || "")}${result.value ? ` <b>${escapeHtml(result.value)}</b>` : ""}${result.baseline ? `，对照 ${escapeHtml(result.baseline)}` : ""}<small>${(result.source_pages || []).length ? `证据：PDF 第 ${result.source_pages.map(escapeHtml).join("、")} 页` : escapeHtml(result.source_anchor || "")}</small></li>`).join("")}</ul></section>` : ""}
    ${(futureWork.length || extensions.length) ? `<section class="content-section"><p class="summary-label">BOUNDARY</p><h3 class="section-title">作者观点与编辑推演</h3><div class="boundary-grid"><div class="boundary-card"><h4>作者原文展望</h4><p>${escapeHtml(futureWork.join("；") || "论文未明确给出后续方向。")}</p></div><div class="boundary-card is-editor"><h4>编辑延伸判断</h4><p>${escapeHtml(extensions.join("；") || "暂无编辑延伸。")}</p></div></div></section>` : ""}
    ${uncertainties.length ? `<section class="content-section"><p class="summary-label">UNCERTAINTIES</p><h3 class="section-title">不确定性与限制</h3><ul class="plain-list">${uncertainties.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
    ${validation ? `<section class="content-section"><p class="summary-label">VALIDATION</p><h3 class="section-title">自动校验</h3><div class="validation-box ${validation.passed ? "" : "is-warning"}">${validation.passed ? `自动校验已通过 · ${Object.keys(validation.checks || {}).length} 项检查` : `校验未通过 · ${(validation.errors || []).join("；")}`}</div><div class="check-grid">${Object.entries(validation.checks || {}).map(([name, passed]) => `<div class="check-item"><span>${escapeHtml(name.replaceAll("_", " "))}</span><b>${passed ? "通过" : "未通过"}</b></div>`).join("")}</div></section>` : ""}
    ${paper.rejection_reason ? `<div class="validation-box is-warning">停止原因：${escapeHtml(paper.rejection_reason)}</div>` : ""}
    ${renderPublishCallout(paper, artifacts)}`;
}

function emptyArtifact(text) {
  return `<div class="empty-detail"><span class="empty-glyph">✦</span><h3>内容尚未生成</h3><p>${escapeHtml(text)}</p></div>`;
}

function renderPublishCallout(paper, artifacts) {
  if (!artifacts || !["ready_for_review", "approved", "published"].includes(paper.status)) return "";
  const states = (state.detail.publications || []).map((item) => `<div class="publication-state"><span>${item.channel === "xhs" ? "小红书" : "微信公众号"}</span><b>${escapeHtml({ manual_ready: "发布包就绪", queued: "等待自动填充", launching: "启动浏览器", needs_login: "需要重新登录", uploading: "上传配图中", filling: "填写内容中", filled: "等待用户发布并反馈", delivered: "已发送连接器", submitted: "平台处理中", published: "用户确认已发布", failed: "填充或发送失败" }[item.status] || item.status)}</b></div>`).join("");
  const stateBlock = states ? `<div class="publication-states">${states}</div>` : "";
  if (paper.status === "published") return `<div class="publish-callout"><h4>内容已发布</h4><p>这篇内容已由发布连接器或编辑确认完成。</p>${stateBlock}${artifacts.export_url ? `<a class="button button-quiet" href="${artifacts.export_url}">重新下载发布包</a>` : ""}</div>`;
  if (paper.status === "approved") return `<div class="publish-callout"><h4>内容已通过审核</h4><p>浏览器模式只填充图片、标题和正文，不会点击发布。请在小红书页面人工核对并发布，再回到这里反馈结果。</p>${stateBlock}<div class="publish-actions">${artifacts.export_url ? `<a class="button button-primary" href="${artifacts.export_url}">下载发布包</a>` : ""}<button class="button button-quiet" id="open-publish">填充到发布页面</button><button class="button button-quiet" id="confirm-published">我已手动发布</button></div></div>`;
  return `<div class="publish-callout"><h4>草稿已准备好</h4><p>完成 ${state.detail.review_checklist.length} 项人工检查后，批准内容并生成发布包。</p><button class="button button-primary" id="open-publish">开始内容审核</button></div>`;
}

async function generatePaper(id) {
  if (state.activeJob && state.jobTrace?.paperId === id
      && ["queued", "running"].includes(state.jobTrace?.status)) return;
  try {
    setWorking(true, "正在生成内容");
    const job = await api(`/api/papers/${id}/generate`, { method: "POST", body: "{}" });
    state.activeJob = job.id;
    state.jobTrace = { ...job, paperId: id };
    renderDetail();
    toast(job.deduplicated ? "这篇论文已有生成任务，已继续显示其进度。" : "生成任务已开始，你可以继续浏览论文库。 ");
    pollJob(job.id, id);
  } catch (error) {
    setWorking(false);
    toast(error.message, true);
  }
}

async function pollJob(jobId, paperId = null) {
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (paperId) {
      state.jobTrace = { ...job, paperId };
      if (state.detail?.paper?.id === paperId) renderDetail();
    }
    if (["queued", "running"].includes(job.status)) {
      setTimeout(() => pollJob(jobId, paperId), 450);
      return;
    }
    state.activeJob = null;
    setWorking(false);
    await Promise.all([loadOverview(), loadPapers()]);
    if (paperId && state.papers.some((paper) => paper.id === paperId)) {
      await selectPaper(paperId);
    } else if (paperId) {
      state.selectedId = null;
      state.detail = null;
      renderPapers();
      $("#detail-panel").innerHTML = `<div class="empty-detail"><span class="empty-glyph">✓</span><h3>论文记录未保留</h3><p>${escapeHtml(job.result?.message || "这篇论文不符合当前选题范围。")}</p></div>`;
    }
    if (job.status === "failed") toast(job.error || "任务未完成", true);
    else toast(job.kind === "fill_xhs" ? "小红书内容已填充。请在浏览器中手动发布，再回到控制台反馈结果。" : "内容任务已完成。 ");
  } catch (error) {
    setWorking(false);
    toast(error.message, true);
  }
}

async function saveVenue(event) {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget));
  try {
    await api(`/api/papers/${state.selectedId}/venue`, { method: "POST", body: JSON.stringify(body) });
    toast("官方会议证据已保存。 ");
    await Promise.all([loadPapers(), selectPaper(state.selectedId)]);
  } catch (error) { toast(error.message, true); }
}

function openPublishModal() {
  const checklist = state.detail.review_checklist || [];
  const publishers = state.detail.publishers || {};
  $("#review-checklist").innerHTML = checklist.map((item, index) => `<label class="review-check"><input type="checkbox" data-check="${index}" /><span>${escapeHtml(item)}</span></label>`).join("");
  $("#publish-channels").innerHTML = Object.entries(publishers).map(([channel, config]) => {
    const checked = config.connected || channel === "xhs";
    const note = config.mode === "browser" ? "专用浏览器 · 仅填充内容，人工点击发布" : config.connected ? "连接器已就绪 · 将自动发送" : "未连接 · 生成手工发布包";
    return `<label class="publish-channel"><input type="checkbox" data-channel="${channel}" ${checked ? "checked" : ""} /><span><strong>${escapeHtml(config.label)}</strong><small>${note}</small></span></label>`;
  }).join("");
  const connected = Object.values(publishers).some((item) => item.connected);
  const sourcePending = state.detail.paper.venue_status !== "verified" && !state.detail.paper.is_demo;
  $(".modal-intro").textContent = sourcePending
    ? "来源尚未核验，仅作提醒；请重点核对正文事实后完成审核。"
    : "逐项核对后批准内容，并生成发布包。";
  $(".modal-warning span").textContent = sourcePending
    ? "来源核验不是强制项，但建议在正式发布前补充官方页面。"
    : (publishers.xhs?.mode === "browser" ? "专用浏览器会上传 6 张配图并填写标题、正文，但不会点击发布；请人工核对后发布。" : "此操作生成发布包，不会绕过平台登录或平台审核。");
  $("#publish-submit").textContent = publishers.xhs?.mode === "browser" ? "批准并填充到小红书" : connected ? "批准并发送发布包" : "批准并生成发布包";
  $("#publish-modal").classList.remove("is-hidden");
  $("#reviewer-name").focus();
}

function closePublishModal() { $("#publish-modal").classList.add("is-hidden"); }

async function preparePublication() {
  const checks = $$("[data-check]").map((input) => input.checked);
  const channels = $$("[data-channel]").filter((input) => input.checked).map((input) => input.dataset.channel);
  const reviewer = $("#reviewer-name").value.trim();
  const button = $("#publish-submit");
  button.disabled = true;
  button.textContent = "正在核验发布包…";
  try {
    const result = await api(`/api/papers/${state.selectedId}/publish-package`, { method: "POST", body: JSON.stringify({ reviewer, checks, channels }) });
    closePublishModal();
    if (result.publication_job) {
      state.activeJob = result.publication_job.id;
      state.jobTrace = { ...result.publication_job, paperId: state.selectedId };
      setWorking(true, "正在填充小红书内容");
      pollJob(result.publication_job.id, state.selectedId);
    }
    const sent = (result.outcomes || []).filter((item) => ["delivered", "submitted", "published"].includes(item.status)).length;
    toast(result.publication_job ? "审核已通过，正在把图片和文本填入小红书。" : sent ? `${sent} 个发布连接器已接收内容包。` : result.message);
    await Promise.all([loadOverview(), loadPapers(), selectPaper(state.selectedId)]);
    if (result.download_url && !result.publication_job) window.location.assign(result.download_url);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function confirmPublished() {
  if (!window.confirm("确认你已经在小红书或目标平台手动点击发布，并看到发布完成？")) return;
  try {
    await api(`/api/papers/${state.selectedId}/confirm-published`, { method: "POST", body: "{}" });
    toast("发布状态已记录。 ");
    await Promise.all([loadOverview(), loadPapers(), selectPaper(state.selectedId)]);
  } catch (error) { toast(error.message, true); }
}

async function copyText(value) {
  try { await navigator.clipboard.writeText(value || ""); toast("已复制到剪贴板。 "); }
  catch { toast("浏览器未允许剪贴板访问。", true); }
}

async function searchArxiv(event) {
  event.preventDefault();
  const query = $("#arxiv-search").value.trim();
  const results = $("#search-results");
  results.classList.remove("is-hidden");
  results.innerHTML = `<div class="search-empty">正在检索 arXiv…</div>`;
  try {
    state.remoteResults = await api(`/api/search/arxiv?q=${encodeURIComponent(query)}`);
    renderRemoteResults(query);
  } catch (error) {
    results.innerHTML = `<div class="search-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderRemoteResults(query) {
  const results = $("#search-results");
  if (!state.remoteResults.length) {
    results.innerHTML = `<div class="search-empty">没有找到与“${escapeHtml(query)}”匹配的论文。</div>`;
    return;
  }
  results.innerHTML = `<div class="search-result-head"><span>最新相关论文 · 按提交时间排序</span><span>${state.remoteResults.length} 篇</span></div>${state.remoteResults.map((paper, index) => `
    <article class="search-result"><div><h4>${escapeHtml(paper.title)}</h4><p>${escapeHtml(compactAuthors(paper.authors))} · ${escapeHtml(paper.arxiv_id)} · ${formatDate(paper.published_at)}</p></div><button class="button ${paper.existing_id ? "button-quiet" : "button-dark"}" data-import="${index}">${paper.existing_id ? "已在库中" : "加入选题"}</button></article>
  `).join("")}`;
  $$('[data-import]', results).forEach((button) => button.addEventListener("click", () => importRemote(Number(button.dataset.import))));
}

async function importRemote(index) {
  const paper = state.remoteResults[index];
  if (paper.existing_id) {
    $("#search-results").classList.add("is-hidden");
    await selectPaper(paper.existing_id);
    return;
  }
  try {
    const result = await api("/api/papers/import", { method: "POST", body: JSON.stringify(paper) });
    toast(result.created ? "论文已加入选题库。" : "论文已经在选题库中。 ");
    $("#search-results").classList.add("is-hidden");
    await Promise.all([loadOverview(), loadPapers()]);
    await selectPaper(result.paper_id);
  } catch (error) { toast(error.message, true); }
}

async function startRun(demo) {
  const button = demo ? $("#demo-run") : $("#live-run");
  button.disabled = true;
  try {
    const job = await api("/api/runs", { method: "POST", body: JSON.stringify({ demo, select_count: demo ? 3 : 1 }) });
    state.activeJob = job.id;
    setWorking(true, demo ? "正在运行示例" : "正在扫描论文");
    toast(demo ? "示例任务已启动。" : "论文扫描已启动。 ");
    pollJob(job.id);
  } catch (error) { setWorking(false); toast(error.message, true); }
  finally { button.disabled = false; }
}

function switchView(view) {
  const previousView = state.view;
  state.view = view;
  window.scrollTo({ top: 0, behavior: "auto" });
  document.body.dataset.view = view;
  $("#view-name").textContent = viewLabels[view];
  $("#view-description").textContent = viewDescriptions[view];
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === view));
  const runs = view === "runs";
  $("#workspace-view").classList.toggle("is-hidden", runs);
  $("#runs-view").classList.toggle("is-hidden", !runs);
  if (!runs) {
    const workspace = view === "workspace";
    $$(".workspace-only").forEach((node) => node.classList.toggle("is-hidden", !workspace));
    const workbench = $(".workbench");
    workbench.classList.toggle("mode-review", view === "review");
    workbench.classList.toggle("mode-published", view === "published");
    $("#status-filter").disabled = view !== "workspace";
    renderPapers();
    const papers = visiblePapers();
    const selectedVisible = papers.some((paper) => paper.id === state.selectedId);
    if (papers.length && (!selectedVisible || previousView !== view)) {
      void selectPaper((selectedVisible ? papers.find((paper) => paper.id === state.selectedId) : papers[0]).id);
    } else if (!papers.length) {
      state.selectedId = null;
      state.detail = null;
      $("#detail-panel").innerHTML = `<div class="empty-detail"><span class="empty-glyph">P→P</span><h3>暂无可展示内容</h3><p>返回内容工作台搜索和生成论文内容。</p></div>`;
    }
  } else renderRunCard();
  $(".sidebar").classList.remove("is-open");
}

function renderRunCard() {
  const root = $("#run-card");
  if (!root || !state.overview) return;
  const run = state.overview.last_run;
  if (!run) { root.innerHTML = `<h3>尚无运行记录</h3><p class="abstract">从“扫描并生成”开始第一轮处理。</p>`; return; }
  root.innerHTML = `<h3>最近一次运行 · #${run.id}</h3><div class="run-grid"><div><small>运行模式</small><strong>${escapeHtml(run.mode)}</strong></div><div><small>发现候选</small><strong>${run.candidates}</strong></div><div><small>已选择</small><strong>${run.selected}</strong></div><div><small>进入审核</small><strong>${run.accepted}</strong></div></div>${run.error ? `<div class="validation-box is-warning">${escapeHtml(run.error)}</div>` : '<div class="validation-box">本次运行没有记录错误。</div>'}`;
}

function showSettings() {
  if (!state.overview) return;
  const settings = state.overview.settings;
  const model = settings.model_configured ? `${settings.model_name || "OpenAI 兼容模型"} 已配置` : "模型未配置（规则模式）";
  toast(`${model} · OpenAlex：${settings.openalex_configured ? "已配置" : "未配置"}`);
}

function bindEvents() {
  $("#arxiv-search-form").addEventListener("submit", searchArxiv);
  $("#library-search").addEventListener("input", renderPapers);
  $("#status-filter").addEventListener("change", renderPapers);
  $("#refresh-button").addEventListener("click", async () => { await Promise.all([loadOverview(), loadPapers()]); toast("论文库已刷新。 "); });
  $("#demo-run").addEventListener("click", () => startRun(true));
  $("#live-run").addEventListener("click", () => startRun(false));
  $("#modal-close").addEventListener("click", closePublishModal);
  $("#publish-modal").addEventListener("click", (event) => { if (event.target.id === "publish-modal") closePublishModal(); });
  $("#publish-submit").addEventListener("click", preparePublication);
  $("#settings-button").addEventListener("click", showSettings);
  $("#mobile-menu").addEventListener("click", () => $(".sidebar").classList.toggle("is-open"));
  $("#mobile-menu-close").addEventListener("click", () => $(".sidebar").classList.remove("is-open"));
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));
  $$(".status-link").forEach((item) => item.addEventListener("click", () => {
    switchView("workspace");
    $("#status-filter").value = item.dataset.status;
    renderPapers();
  }));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closePublishModal(); $("#search-results").classList.add("is-hidden"); } });
  document.addEventListener("click", (event) => { if (!event.target.closest(".search-station")) $("#search-results").classList.add("is-hidden"); });
}

async function init() {
  bindEvents();
  try {
    await Promise.all([loadOverview(), loadPapers({ preserveSelection: false })]);
    if (state.papers.length) await selectPaper(state.papers[0].id);
  } catch (error) {
    toast(`控制台初始化失败：${error.message}`, true);
  }
}

init();
