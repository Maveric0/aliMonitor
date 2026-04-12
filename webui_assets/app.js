const state = {
  overview: null,
  setup: null,
  busy: false,
  flash: { type: "", message: "" },
  domainModal: null,
  ieplRuleModal: null,
  expandedDomainCards: {},
  openDomainActionMenu: null,
  activeMainSection: "domains",
  activeDiagnosticsTab: "summary",
};

let flashTimer = null;

function $(id) {
  return document.getElementById(id);
}

function isSetupMode() {
  return !!state.setup && !state.setup.configured;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleString("zh-CN");
}

function formatAge(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "-";
  const sec = Number(seconds);
  if (!Number.isFinite(sec)) return "-";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

async function parseApiResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

async function apiGet(path) {
  const response = await fetch(path, { cache: "no-store" });
  return parseApiResponse(response);
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return parseApiResponse(response);
}

function setBusy(busy) {
  state.busy = !!busy;
  document.body.classList.toggle("busy", state.busy);
  render();
}

function showFlash(type, message) {
  state.flash = { type, message };
  renderFlash();
  if (flashTimer) {
    clearTimeout(flashTimer);
  }
  flashTimer = setTimeout(() => {
    state.flash = { type: "", message: "" };
    renderFlash();
  }, 6000);
}

function renderFlash() {
  const flash = $("flash");
  if (!flash) return;
  if (!state.flash.message) {
    flash.className = "flash";
    flash.textContent = "";
    return;
  }
  flash.className = `flash show ${state.flash.type}`;
  flash.textContent = state.flash.message;
}

function defaultDomainDraft() {
  return {
    record_name: "",
    enabled: true,
    preferred_primary_uuid: "",
    backup_uuids: [],
    forward_rules: [],
  };
}

function blankRuleEditor() {
  return {
    mode: "create",
    old_listen_port: null,
    listen_port: "",
    remote_host: "",
    remote_port: "",
  };
}

function domainByRecordName(recordName) {
  return state.overview?.domains?.find((item) => item.record_name === recordName) || null;
}

function frontendNodeByUuid(uuid) {
  return state.overview?.frontend_nodes?.find((item) => item.uuid === uuid) || null;
}

function availableDomainNodes() {
  if (!state.overview || !state.domainModal) return [];
  const ownerRecordName = state.domainModal.original_record_name || state.domainModal.owner_record_name || "";
  return state.overview.frontend_nodes.filter((node) => !node.owner_domain || node.owner_domain === ownerRecordName);
}

function syncDomainDraftFromInputs() {
  if (!state.domainModal) return;
  const draft = state.domainModal.draft;
  const recordInput = $("domain-record-name");
  const enabledInput = $("domain-enabled");
  const preferredInput = $("domain-preferred-primary");
  if (recordInput) draft.record_name = recordInput.value.trim();
  if (enabledInput) draft.enabled = !!enabledInput.checked;
  if (preferredInput) draft.preferred_primary_uuid = preferredInput.value.trim();
  draft.backup_uuids = draft.backup_uuids.filter((uuid) => uuid && uuid !== draft.preferred_primary_uuid);
}

function syncIeplDraftFromInputs() {
  if (!state.ieplRuleModal) return;
  const draft = state.ieplRuleModal.draft;
  const listenInput = $("iepl-rule-listen-port");
  const hostInput = $("iepl-rule-remote-host");
  const portInput = $("iepl-rule-remote-port");
  if (listenInput) draft.listen_port = listenInput.value.trim();
  if (hostInput) draft.remote_host = hostInput.value.trim();
  if (portInput) draft.remote_port = portInput.value.trim();
}

function syncSetupDraftFromInput() {
  const editor = $("setup-editor");
  if (!editor || !state.setup) return;
  state.setup.settingsText = editor.value;
}

function parseRequiredInt(value, label) {
  const parsed = Number.parseInt(String(value ?? "").trim(), 10);
  if (!Number.isInteger(parsed)) {
    throw new Error(`${label} 必须是整数`);
  }
  if (parsed < 1 || parsed > 65535) {
    throw new Error(`${label} 必须在 1-65535 之间`);
  }
  return parsed;
}

function ensureRulePayload(editor) {
  const listen_port = parseRequiredInt(editor.listen_port, "监听端口");
  const remote_port = parseRequiredInt(editor.remote_port, "远端端口");
  const remote_host = String(editor.remote_host || "").trim();
  if (!remote_host) {
    throw new Error("远端地址不能为空");
  }
  return { listen_port, remote_host, remote_port };
}

function openCreateDomainModal() {
  state.domainModal = {
    open: true,
    mode: "create",
    original_record_name: "",
    owner_record_name: "",
    draft: defaultDomainDraft(),
    ruleEditor: blankRuleEditor(),
  };
  renderModals();
}

function openEditDomainModal(recordName) {
  const domain = domainByRecordName(recordName);
  if (!domain) {
    showFlash("error", `domain not found: ${recordName}`);
    return;
  }
  state.domainModal = {
    open: true,
    mode: "edit",
    original_record_name: domain.record_name,
    owner_record_name: domain.record_name,
    draft: {
      record_name: domain.record_name,
      enabled: !!domain.enabled,
      preferred_primary_uuid: domain.editor_preferred_primary_uuid || domain.preferred_primary_uuid || "",
      backup_uuids: [...(domain.editor_backup_uuids || domain.backup_uuids || [])],
      forward_rules: (domain.forward_rules || []).map((item) => ({ ...item })),
    },
    ruleEditor: blankRuleEditor(),
  };
  renderModals();
}

function closeDomainModal() {
  state.domainModal = null;
  renderModals();
}

function addBackupToDomainDraft() {
  syncDomainDraftFromInputs();
  const select = $("domain-backup-candidate");
  if (!select || !select.value) return;
  const uuid = select.value;
  if (uuid === state.domainModal.draft.preferred_primary_uuid) return;
  if (state.domainModal.draft.backup_uuids.includes(uuid)) return;
  state.domainModal.draft.backup_uuids.push(uuid);
  renderModals();
}

function removeBackupFromDomainDraft(uuid) {
  syncDomainDraftFromInputs();
  state.domainModal.draft.backup_uuids = state.domainModal.draft.backup_uuids.filter((item) => item !== uuid);
  renderModals();
}

function moveBackup(uuid, direction) {
  syncDomainDraftFromInputs();
  const backups = [...state.domainModal.draft.backup_uuids];
  const index = backups.indexOf(uuid);
  if (index < 0) return;
  const target = index + direction;
  if (target < 0 || target >= backups.length) return;
  [backups[index], backups[target]] = [backups[target], backups[index]];
  state.domainModal.draft.backup_uuids = backups;
  renderModals();
}

function saveDomainDraftRule() {
  syncDomainDraftFromInputs();
  const editor = state.domainModal.ruleEditor;
  const payload = ensureRulePayload({
    listen_port: $("modal-rule-listen-port")?.value,
    remote_host: $("modal-rule-remote-host")?.value,
    remote_port: $("modal-rule-remote-port")?.value,
  });
  const rules = [...state.domainModal.draft.forward_rules];
  if (editor.mode === "edit") {
    const index = rules.findIndex((item) => Number(item.listen_port) === Number(editor.old_listen_port));
    if (index < 0) {
      throw new Error(`监听端口不存在: ${editor.old_listen_port}`);
    }
    const duplicate = rules.find((item, idx) => idx !== index && Number(item.listen_port) === payload.listen_port);
    if (duplicate) {
      throw new Error(`监听端口重复: ${payload.listen_port}`);
    }
    rules[index] = payload;
  } else {
    const duplicate = rules.find((item) => Number(item.listen_port) === payload.listen_port);
    if (duplicate) {
      throw new Error(`监听端口重复: ${payload.listen_port}`);
    }
    rules.push(payload);
  }
  rules.sort((a, b) => Number(a.listen_port) - Number(b.listen_port));
  state.domainModal.draft.forward_rules = rules;
  state.domainModal.ruleEditor = blankRuleEditor();
  renderModals();
}

function editDomainDraftRule(listenPort) {
  const rule = state.domainModal?.draft?.forward_rules?.find((item) => Number(item.listen_port) === Number(listenPort));
  if (!rule) return;
  state.domainModal.ruleEditor = {
    mode: "edit",
    old_listen_port: Number(rule.listen_port),
    listen_port: String(rule.listen_port),
    remote_host: String(rule.remote_host),
    remote_port: String(rule.remote_port),
  };
  renderModals();
}

function deleteDomainDraftRule(listenPort) {
  state.domainModal.draft.forward_rules = state.domainModal.draft.forward_rules.filter(
    (item) => Number(item.listen_port) !== Number(listenPort),
  );
  if (Number(state.domainModal.ruleEditor.old_listen_port) === Number(listenPort)) {
    state.domainModal.ruleEditor = blankRuleEditor();
  }
  renderModals();
}

function cancelDomainRuleEditor() {
  state.domainModal.ruleEditor = blankRuleEditor();
  renderModals();
}

async function submitDomainModal() {
  syncDomainDraftFromInputs();
  const draft = state.domainModal.draft;
  if (!draft.record_name) {
    throw new Error("域名不能为空");
  }
  if (!draft.preferred_primary_uuid) {
    throw new Error("必须选择首选主机");
  }
  const payload = {
    original_record_name: state.domainModal.original_record_name || null,
    domain: draft,
  };
  await runBusyAction(async () => {
    const response = await apiPost("/api/frontend-domain-save", payload);
    closeDomainModal();
    showFlash("success", response.result.message);
    await refreshOverview({ keepFlash: true });
  });
}

async function deleteDomain(recordName) {
  if (!window.confirm(`确认删除域名 ${recordName} 吗？`)) {
    return;
  }
  await runBusyAction(async () => {
    const response = await apiPost("/api/frontend-domain-delete", { record_name: recordName });
    showFlash("success", response.result.message);
    await refreshOverview({ keepFlash: true });
  });
}

function openCreateIeplRuleModal() {
  state.ieplRuleModal = {
    open: true,
    mode: "create",
    old_listen_port: null,
    draft: { listen_port: "", remote_host: "", remote_port: "" },
  };
  renderModals();
}

function openEditIeplRuleModal(listenPort) {
  const rule = state.overview?.iepl_rules?.find((item) => Number(item.listen_port) === Number(listenPort));
  if (!rule) {
    showFlash("error", `IEPL rule not found: ${listenPort}`);
    return;
  }
  state.ieplRuleModal = {
    open: true,
    mode: "edit",
    old_listen_port: Number(rule.listen_port),
    draft: {
      listen_port: String(rule.listen_port),
      remote_host: String(rule.remote_host),
      remote_port: String(rule.remote_port),
    },
  };
  renderModals();
}

function closeIeplRuleModal() {
  state.ieplRuleModal = null;
  renderModals();
}

async function submitIeplRuleModal() {
  syncIeplDraftFromInputs();
  const draft = ensureRulePayload(state.ieplRuleModal.draft);
  await runBusyAction(async () => {
    const path = state.ieplRuleModal.mode === "edit" ? "/api/update-iepl-forward" : "/api/add-iepl-forward";
    const payload = state.ieplRuleModal.mode === "edit"
      ? { ...draft, old_listen_port: state.ieplRuleModal.old_listen_port }
      : draft;
    const response = await apiPost(path, payload);
    closeIeplRuleModal();
    showFlash("success", response.result.message);
    await refreshOverview({ keepFlash: true });
  });
}

async function deleteIeplRule(listenPort) {
  if (!window.confirm(`确认删除 IEPL 规则 ${listenPort} 吗？`)) {
    return;
  }
  await runBusyAction(async () => {
    const response = await apiPost("/api/remove-iepl-forward", { listen_port: Number(listenPort) });
    showFlash("success", response.result.message);
    await refreshOverview({ keepFlash: true });
  });
}

async function runBusyAction(callback) {
  if (state.busy) return;
  setBusy(true);
  try {
    await callback();
  } catch (error) {
    showFlash("error", error.message || String(error));
  } finally {
    setBusy(false);
  }
}

async function refreshBootstrap() {
  const payload = await apiGet("/api/bootstrap");
  state.setup = {
    ...payload.data,
    settingsText: payload.data.settings_text || "",
  };
  if (isSetupMode()) {
    state.overview = null;
  }
  render();
}

async function refreshOverview(options = {}) {
  const payload = await apiGet("/api/overview");
  state.overview = payload.data;
  pruneDomainCardUi();
  render();
  if (!options.keepFlash) {
    renderFlash();
  }
}

async function refreshLogs() {
  const payload = await apiGet("/api/logs");
  if (!state.overview) {
    state.overview = { logs: payload.data };
  } else {
    state.overview.logs = payload.data;
  }
  renderLogs();
}

function metricTag(node) {
  if (!node) return '<span class="tag">-</span>';
  if (node.online && !node.stale) return '<span class="tag good">fresh</span>';
  if (node.online) return '<span class="tag warn">stale</span>';
  return '<span class="tag bad">offline</span>';
}

function pruneDomainCardUi() {
  const domains = state.overview?.domains || [];
  const validNames = new Set(domains.map((item) => item.record_name));
  state.expandedDomainCards = Object.fromEntries(
    Object.entries(state.expandedDomainCards).filter(([recordName, expanded]) => expanded && validNames.has(recordName)),
  );
  if (state.openDomainActionMenu && !validNames.has(state.openDomainActionMenu)) {
    state.openDomainActionMenu = null;
  }
}

function toggleDomainCardExpanded(recordName) {
  state.expandedDomainCards = {
    ...state.expandedDomainCards,
    [recordName]: !state.expandedDomainCards[recordName],
  };
  renderDomains();
}

function closeDomainActionMenu() {
  if (!state.openDomainActionMenu) return;
  state.openDomainActionMenu = null;
  renderDomains();
}

function toggleDomainActionMenu(recordName) {
  state.openDomainActionMenu = state.openDomainActionMenu === recordName ? null : recordName;
  renderDomains();
}

function formatRuleSummary(rules) {
  if (!rules?.length) return "无规则";
  const preview = rules
    .slice()
    .sort((a, b) => Number(a.listen_port) - Number(b.listen_port))
    .slice(0, 3)
    .map((rule) => String(rule.listen_port))
    .join(", ");
  const remaining = rules.length - 3;
  return remaining > 0 ? `${preview} +${remaining}` : preview;
}

function renderStats() {
  const root = $("stats");
  if (!root) return;
  if (isSetupMode()) {
    root.innerHTML = `
      <div class="stat">
        <div class="stat-label">状态</div>
        <div class="stat-value">初始化</div>
      </div>
      <div class="stat">
        <div class="stat-label">模板</div>
        <div class="stat-value">${escapeHtml(state.setup?.template_source || "-")}</div>
      </div>
      <div class="stat">
        <div class="stat-label">settings.json</div>
        <div class="stat-value">${state.setup?.settings_exists ? "已存在" : "未创建"}</div>
      </div>
    `;
    return;
  }
  if (!state.overview) {
    root.innerHTML = '<div class="empty">loading...</div>';
    return;
  }
  const stats = state.overview.stats;
  const cards = [
    ["域名", stats.domain_count],
    ["启用域名", stats.enabled_domain_count],
    ["前端节点", stats.frontend_node_count],
    ["已分配节点", stats.assigned_frontend_node_count],
    ["IEPL 节点", stats.iepl_node_count],
    ["IEPL 规则", stats.iepl_rule_count],
  ];
  root.innerHTML = cards
    .map(
      ([label, value]) => `
        <div class="stat">
          <div class="stat-label">${escapeHtml(label)}</div>
          <div class="stat-value">${escapeHtml(value)}</div>
        </div>
      `,
    )
    .join("");
}

function renderSetup() {
  const panel = $("setup-panel");
  const statusRoot = $("setup-status");
  const pathsRoot = $("setup-paths");
  const validationRoot = $("setup-validation");
  const editor = $("setup-editor");
  if (!panel || !statusRoot || !pathsRoot || !validationRoot || !editor) return;
  if (!state.setup) {
    panel.hidden = true;
    return;
  }

  panel.hidden = !isSetupMode();
  if (!isSetupMode()) {
    validationRoot.className = "flash";
    validationRoot.textContent = "";
    return;
  }

  statusRoot.innerHTML = `
    <div><strong>配置文件</strong> ${state.setup.settings_exists ? "已存在" : "未创建"}</div>
    <div><strong>模板来源</strong> ${escapeHtml(state.setup.template_source || "-")}</div>
    <div><strong>下一步</strong> 保存后自动尝试启动 aliMonitor.service</div>
  `;
  pathsRoot.innerHTML = `
    <div><strong>settings.json</strong> <span class="mono">${escapeHtml(state.setup.paths?.settings || "-")}</span></div>
    <div><strong>komari_state.json</strong> <span class="mono">${escapeHtml(state.setup.paths?.state || "-")}</span></div>
    <div><strong>forward_installed.json</strong> <span class="mono">${escapeHtml(state.setup.paths?.frontend_installed || "-")}</span></div>
  `;
  if (state.setup.validation_error) {
    validationRoot.className = "flash show error";
    validationRoot.textContent = state.setup.validation_error;
  } else {
    validationRoot.className = "flash";
    validationRoot.textContent = "";
  }
  if (editor.value !== state.setup.settingsText && document.activeElement !== editor) {
    editor.value = state.setup.settingsText || "";
  }
}

function renderAppMode() {
  $("workspace")?.toggleAttribute("hidden", isSetupMode());
  $("sync-all-btn")?.toggleAttribute("hidden", isSetupMode());
  $("add-domain-btn")?.toggleAttribute("hidden", isSetupMode());
}

function renderDomains() {
  const root = $("domains");
  if (!root) return;
  if (!state.overview) {
    root.innerHTML = '<div class="empty">loading...</div>';
    return;
  }
  if (!state.overview.domains.length) {
    root.innerHTML = '<div class="empty">no frontend domains</div>';
    return;
  }
  root.innerHTML = state.overview.domains
    .map((domain) => {
      const expanded = !!state.expandedDomainCards[domain.record_name];
      const menuOpen = state.openDomainActionMenu === domain.record_name;
      const dnsCurrentValue = domain.dns?.ok ? domain.dns.content || "-" : "-";
      const dnsSummary = domain.dns?.ok
        ? `${domain.record_name} -> ${dnsCurrentValue}`
        : `DNS error: ${domain.dns?.error || ""}`;
      const backupCount = Array.isArray(domain.backups) ? domain.backups.length : 0;
      const backupNames = backupCount ? domain.backups.map((item) => item?.name || "-").join(" -> ") : "-";
      const ruleSummary = formatRuleSummary(domain.forward_rules || []);
      const memberPills = domain.members.length
        ? `<div class="pill-row">${domain.members
            .map(
              (item) => `
                <span class="pill">
                  ${escapeHtml(item.name)}
                  <span class="muted">${escapeHtml(item.role)}</span>
                </span>
              `,
            )
            .join("")}</div>`
        : '<div class="empty">no assigned members</div>';
      const rulesTable = domain.forward_rules.length
        ? `
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>监听</th>
                  <th>远端地址</th>
                  <th>远端端口</th>
                </tr>
              </thead>
              <tbody>
                ${domain.forward_rules
                  .map(
                    (rule) => `
                      <tr>
                        <td class="mono">${escapeHtml(rule.listen_port)}</td>
                        <td class="mono">${escapeHtml(rule.remote_host)}</td>
                        <td class="mono">${escapeHtml(rule.remote_port)}</td>
                      </tr>
                    `,
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        `
        : '<div class="empty">no forward rules</div>';
      const detailsPanel = expanded
        ? `
          <div class="domain-details">
            <div class="split-grid">
              <div class="info-block">
                <h3>成员</h3>
                ${memberPills}
              </div>
              <div class="info-block">
                <h3>主备详情</h3>
                <div class="info-list">
                  <div><strong>首选主机</strong> ${escapeHtml(domain.preferred_primary?.name || "-")}</div>
                  <div><strong>备用顺序</strong> ${escapeHtml(backupNames)}</div>
                  <div><strong>域名状态</strong> ${domain.enabled ? "启用" : "停用"}</div>
                  <div><strong>legacy pool</strong> ${domain.legacy_pool ? "是" : "否"}</div>
                </div>
              </div>
            </div>
            <div class="subpanel">
              <h3>域名级前端规则</h3>
              ${rulesTable}
            </div>
          </div>
        `
        : "";
      return `
        <article class="domain-card">
          <header>
            <div>
              <div class="card-title">${escapeHtml(domain.record_name)}</div>
              <div class="meta">
                ${domain.enabled ? "enabled" : "disabled"}
                ${domain.legacy_pool ? " · migrated from legacy pool" : ""}
              </div>
            </div>
            <div class="domain-card-actions">
              <button class="btn btn-secondary" data-action="domain-sync" data-record-name="${escapeHtml(domain.record_name)}" ${state.busy ? "disabled" : ""}>立即同步</button>
              <button class="btn btn-ghost" data-action="domain-edit" data-record-name="${escapeHtml(domain.record_name)}" ${state.busy ? "disabled" : ""}>编辑</button>
              <button class="btn btn-ghost" data-action="domain-toggle-details" data-record-name="${escapeHtml(domain.record_name)}">${expanded ? "收起详情" : "查看详情"}</button>
              <div class="domain-card-menu-wrap">
                <button class="btn btn-ghost" data-action="domain-toggle-menu" data-record-name="${escapeHtml(domain.record_name)}" ${state.busy ? "disabled" : ""}>更多</button>
                ${menuOpen
                  ? `
                    <div class="domain-card-menu">
                      <button class="btn btn-secondary" data-action="domain-switch" data-record-name="${escapeHtml(domain.record_name)}" ${state.busy ? "disabled" : ""}>手动切换</button>
                      <button class="btn btn-secondary" data-action="domain-reinstall" data-record-name="${escapeHtml(domain.record_name)}" ${state.busy ? "disabled" : ""}>重装转发</button>
                      <button class="btn btn-danger" data-action="domain-delete" data-record-name="${escapeHtml(domain.record_name)}" ${state.busy ? "disabled" : ""}>删除</button>
                    </div>
                  `
                  : ""}
              </div>
            </div>
          </header>
          <div class="tag-row domain-card-tags">
            ${domain.current_primary ? metricTag(domain.current_primary) : '<span class="tag warn">primary pending</span>'}
            <span class="tag">${escapeHtml(dnsSummary)}</span>
            <span class="tag">规则 ${escapeHtml(domain.forward_rule_count)}</span>
            <span class="tag">备用 ${escapeHtml(backupCount)}</span>
          </div>
          <div class="domain-summary-grid">
            <div class="info-block">
              <h3>摘要</h3>
              <div class="info-list">
                <div><strong>当前主机</strong> ${escapeHtml(domain.current_primary?.name || "-")} <span class="mono">${escapeHtml(domain.current_primary_ip || "")}</span></div>
                <div><strong>DNS 当前值</strong> <span class="mono">${escapeHtml(dnsCurrentValue)}</span></div>
                <div><strong>规则数量</strong> ${escapeHtml(domain.forward_rule_count)}</div>
                <div><strong>规则摘要</strong> <span class="mono">${escapeHtml(ruleSummary)}</span></div>
              </div>
            </div>
            <div class="info-block">
              <h3>切换状态</h3>
              <div class="info-list">
                <div><strong>首选主机</strong> ${escapeHtml(domain.preferred_primary?.name || "-")}</div>
                <div><strong>备用数量</strong> ${escapeHtml(backupCount)}</div>
                <div><strong>上次原因</strong> ${escapeHtml(domain.last_switch_reason || "-")}</div>
                <div><strong>上次切换</strong> ${escapeHtml(formatDate(domain.last_switch_at))}</div>
              </div>
            </div>
          </div>
          ${detailsPanel}
        </article>
      `;
    })
    .join("");
}
function renderFrontendNodes() {
  const root = $("frontend-nodes");
  if (!root) return;
  if (!state.overview) {
    root.innerHTML = '<div class="empty">loading...</div>';
    return;
  }
  root.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>节点</th>
            <th>归属域名</th>
            <th>当前主域名</th>
            <th>状态</th>
            <th>流量 / 在线</th>
            <th>流量上限</th>
            <th>安装状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${state.overview.frontend_nodes
            .map((node) => `
              <tr>
                <td>
                  <div><strong>${escapeHtml(node.name)}</strong></div>
                  <div class="muted mono">${escapeHtml(node.uuid)}</div>
                  <div class="muted mono">${escapeHtml(node.ipv4 || "-")}</div>
                </td>
                <td>${escapeHtml(node.owner_domain || "-")}</td>
                <td>${escapeHtml((node.current_primary_domains || []).join(", ") || "-")}</td>
                <td>
                  ${metricTag(node)}
                  <div class="meta">age ${escapeHtml(formatAge(node.status_age_sec))}</div>
                  <div class="meta">${node.healthy ? "eligible" : node.fallback_healthy ? "fallback only" : "not eligible"}</div>
                </td>
                <td>
                  <div>${escapeHtml(node.traffic_gb)} GB</div>
                  <div class="meta">${escapeHtml(node.uptime_days)} d</div>
                  <div class="meta">${escapeHtml(node.over_limit_detail)}</div>
                </td>
                <td>
                  <input id="traffic-limit-${escapeHtml(node.uuid)}" class="input small-input" type="number" min="1" value="${escapeHtml(node.traffic_limit_gb)}" ${state.busy ? "disabled" : ""}>
                  <div class="meta">${node.traffic_limit_source === "custom" ? "自定义" : "默认"}</div>
                </td>
                <td>
                  <div>${escapeHtml(node.installed_profile || "-")}</div>
                  <div class="meta">${escapeHtml(node.installed_config || "-")}</div>
                  <div class="meta">${escapeHtml(formatDate(node.installed_at))}</div>
                </td>
                <td class="frontend-node-actions-cell">
                  <div class="frontend-node-actions">
                    <button class="btn btn-secondary" data-action="save-traffic-limit" data-uuid="${escapeHtml(node.uuid)}" ${state.busy ? "disabled" : ""}>保存上限</button>
                    <button class="btn btn-ghost" data-action="clear-traffic-limit" data-uuid="${escapeHtml(node.uuid)}" ${state.busy ? "disabled" : ""}>恢复默认</button>
                    ${node.owner_domain ? `<button class="btn btn-ghost" data-action="reinstall-node-forward" data-uuid="${escapeHtml(node.uuid)}" ${state.busy ? "disabled" : ""}>重装转发</button>` : ""}
                  </div>
                </td>
              </tr>
            `)
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderIeplRules() {
  const root = $("iepl-rules");
  if (!root) return;
  if (!state.overview) {
    root.innerHTML = '<div class="empty">loading...</div>';
    return;
  }
  if (!state.overview.iepl_rules.length) {
    root.innerHTML = '<div class="empty">no IEPL rules</div>';
    return;
  }
  root.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>监听</th>
            <th>远端地址</th>
            <th>远端端口</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${state.overview.iepl_rules
            .map(
              (rule) => `
                <tr>
                  <td class="mono">${escapeHtml(rule.listen_port)}</td>
                  <td class="mono">${escapeHtml(rule.remote_host)}</td>
                  <td class="mono">${escapeHtml(rule.remote_port)}</td>
                  <td>
                    <div class="button-row">
                      <button class="btn btn-ghost" data-action="iepl-rule-edit" data-listen-port="${escapeHtml(rule.listen_port)}" ${state.busy ? "disabled" : ""}>编辑</button>
                      <button class="btn btn-danger" data-action="iepl-rule-delete" data-listen-port="${escapeHtml(rule.listen_port)}" ${state.busy ? "disabled" : ""}>删除</button>
                    </div>
                  </td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderIeplNodes() {
  const root = $("iepl-nodes");
  if (!root) return;
  if (!state.overview) {
    root.innerHTML = '<div class="empty">loading...</div>';
    return;
  }
  if (!state.overview.iepl_nodes.length) {
    root.innerHTML = '<div class="empty">no IEPL nodes</div>';
    return;
  }
  root.innerHTML = `<div class="node-grid">
    ${state.overview.iepl_nodes
      .map(
        (node) => `
          <article class="node-card">
            <header>
              <div>
                <div class="card-title">${escapeHtml(node.name)}</div>
                <div class="meta mono">${escapeHtml(node.uuid)}</div>
                <div class="meta mono">agent ${escapeHtml(node.ipv4 || "-")} 路 target ${escapeHtml(node.target_ip || "-")}</div>
                ${node.target_ip_error ? `<div class="meta">${escapeHtml(node.target_ip_error)}</div>` : ""}
              </div>
              <div class="tag-row">
                ${metricTag(node)}
                <span class="tag">${escapeHtml(node.traffic_gb)} GB</span>
                <span class="tag">${escapeHtml(node.uptime_days)} d</span>
              </div>
            </header>
            <div class="button-row">
              <button class="btn btn-secondary" data-action="iepl-reinstall" data-uuid="${escapeHtml(node.uuid)}" ${state.busy ? "disabled" : ""}>重装 IEPL</button>
              <button class="btn btn-secondary" data-action="iepl-restart-realm" data-uuid="${escapeHtml(node.uuid)}" ${state.busy ? "disabled" : ""}>重启 realm</button>
              <button class="btn btn-ghost" data-action="iepl-cleanup-legacy" data-uuid="${escapeHtml(node.uuid)}" ${state.busy ? "disabled" : ""}>清理 legacy</button>
            </div>
          </article>
        `,
      )
      .join("")}
  </div>`;
}

function renderSummary() {
  const root = $("summary");
  const config = $("config-summary");
  if (!root || !config) return;
  if (!state.overview) {
    root.innerHTML = '<div class="empty">loading...</div>';
    config.textContent = "";
    return;
  }
  root.innerHTML = `
    <div class="info-list">
      <div><strong>生成时间</strong> ${escapeHtml(formatDate(state.overview.generated_at))}</div>
      <div><strong>默认域名</strong> ${escapeHtml(state.overview.default_domain_name || "-")}</div>
      <div><strong>settings.json</strong> <span class="mono">${escapeHtml(state.overview.paths.settings)}</span></div>
      <div><strong>komari_state.json</strong> <span class="mono">${escapeHtml(state.overview.paths.state)}</span></div>
      <div><strong>forward_installed.json</strong> <span class="mono">${escapeHtml(state.overview.paths.frontend_installed)}</span></div>
      <div><strong>legacy config.toml</strong> <span class="mono">${escapeHtml(state.overview.paths.legacy_frontend_config)}</span></div>
      <div><strong>iepl_config.toml</strong> <span class="mono">${escapeHtml(state.overview.paths.iepl_config)}</span></div>
    </div>
  `;
  config.textContent = JSON.stringify(state.overview.settings, null, 2);
}

function renderLogs() {
  const root = $("logs");
  if (!root) return;
  const logs = state.overview?.logs;
  if (!logs) {
    root.textContent = "loading...";
    return;
  }
  root.textContent = `[source] ${logs.source}\n\n${logs.content}`;
}

function renderDomainModal() {
  if (!state.domainModal?.open) return "";
  syncDomainDraftFromInputs();
  const draft = state.domainModal.draft;
  const ruleEditor = state.domainModal.ruleEditor;
  const nodes = availableDomainNodes();
  const candidateOptions = nodes
    .map(
      (node) => `<option value="${escapeHtml(node.uuid)}"${draft.preferred_primary_uuid === node.uuid ? " selected" : ""}>${escapeHtml(node.name)} (${escapeHtml(node.ipv4 || "-")})</option>`,
    )
    .join("");
  const backupCandidates = nodes.filter(
    (node) => node.uuid !== draft.preferred_primary_uuid && !draft.backup_uuids.includes(node.uuid),
  );
  const backupOptions = backupCandidates
    .map((node) => `<option value="${escapeHtml(node.uuid)}">${escapeHtml(node.name)} (${escapeHtml(node.ipv4 || "-")})</option>`)
    .join("");
  const backupList = draft.backup_uuids.length
    ? `<ul class="list-plain">
        ${draft.backup_uuids
          .map((uuid) => {
            const node = frontendNodeByUuid(uuid);
            return `
              <li class="info-block">
                <div class="section-head">
                  <div>
                    <strong>${escapeHtml(node?.name || uuid)}</strong>
                    <div class="meta mono">${escapeHtml(node?.ipv4 || uuid)}</div>
                  </div>
                  <div class="button-row">
                    <button class="btn btn-ghost" data-action="modal-backup-up" data-uuid="${escapeHtml(uuid)}">上移</button>
                    <button class="btn btn-ghost" data-action="modal-backup-down" data-uuid="${escapeHtml(uuid)}">下移</button>
                    <button class="btn btn-danger" data-action="modal-backup-remove" data-uuid="${escapeHtml(uuid)}">移除</button>
                  </div>
                </div>
              </li>
            `;
          })
          .join("")}
      </ul>`
    : '<div class="empty">no backups configured</div>';
  const rulesTable = draft.forward_rules.length
    ? `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>监听</th>
              <th>远端地址</th>
              <th>远端端口</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${draft.forward_rules
              .map(
                (rule) => `
                  <tr>
                    <td class="mono">${escapeHtml(rule.listen_port)}</td>
                    <td class="mono">${escapeHtml(rule.remote_host)}</td>
                    <td class="mono">${escapeHtml(rule.remote_port)}</td>
                    <td>
                      <div class="button-row">
                        <button class="btn btn-ghost" data-action="modal-rule-edit" data-listen-port="${escapeHtml(rule.listen_port)}">编辑</button>
                        <button class="btn btn-danger" data-action="modal-rule-delete" data-listen-port="${escapeHtml(rule.listen_port)}">删除</button>
                      </div>
                    </td>
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : '<div class="empty">no forward rules</div>';

  return `
    <div class="modal-backdrop" data-modal-kind="domain">
      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="modal-head">
          <div>
            <p class="section-kicker">Frontend Domain</p>
            <h2>${state.domainModal.mode === "edit" ? "编辑域名" : "新增域名"}</h2>
          </div>
          <button class="btn btn-ghost" data-action="modal-close">关闭</button>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="domain-record-name">域名</label>
            <input id="domain-record-name" class="input" type="text" value="${escapeHtml(draft.record_name)}" placeholder="speedtest.example.com">
          </div>
          <div class="checkbox-row">
            <input id="domain-enabled" type="checkbox" ${draft.enabled ? "checked" : ""}>
            <label for="domain-enabled">启用该域名</label>
          </div>
          <div class="field full">
            <label for="domain-preferred-primary">首选主机</label>
            <select id="domain-preferred-primary">
              <option value="">请选择主机</option>
              ${candidateOptions}
            </select>
          </div>
        </div>
        <div class="subpanel">
          <h3>备用顺序</h3>
          <div class="backup-editor">
            <div class="button-row">
              <select id="domain-backup-candidate">
                <option value="">选择备用节点</option>
                ${backupOptions}
              </select>
              <button class="btn btn-secondary" data-action="modal-backup-add">添加备用</button>
            </div>
            ${backupList}
          </div>
        </div>
        <div class="subpanel">
          <h3>域名级前端规则</h3>
          ${rulesTable}
          <div class="rule-editor">
            <div class="rule-grid">
              <div class="field">
                <label for="modal-rule-listen-port">监听端口</label>
                <input id="modal-rule-listen-port" class="input" type="number" min="1" max="65535" value="${escapeHtml(ruleEditor.listen_port)}">
              </div>
              <div class="field">
                <label for="modal-rule-remote-host">远端地址</label>
                <input id="modal-rule-remote-host" class="input" type="text" value="${escapeHtml(ruleEditor.remote_host)}" placeholder="1.2.3.4">
              </div>
              <div class="field">
                <label for="modal-rule-remote-port">远端端口</label>
                <input id="modal-rule-remote-port" class="input" type="number" min="1" max="65535" value="${escapeHtml(ruleEditor.remote_port)}">
              </div>
            </div>
            <div class="button-row">
              <button class="btn btn-secondary" data-action="modal-rule-save">${ruleEditor.mode === "edit" ? "保存规则" : "新增规则"}</button>
              ${ruleEditor.mode === "edit" ? '<button class="btn btn-ghost" data-action="modal-rule-cancel">取消编辑</button>' : ""}
            </div>
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn btn-ghost" data-action="modal-close">取消</button>
          <button class="btn btn-primary" data-action="modal-save-domain" ${state.busy ? "disabled" : ""}>${state.domainModal.mode === "edit" ? "保存域名" : "新增域名"}</button>
        </div>
      </div>
    </div>
  `;
}

function renderIeplRuleModal() {
  if (!state.ieplRuleModal?.open) return "";
  const draft = state.ieplRuleModal.draft;
  return `
    <div class="modal-backdrop" data-modal-kind="iepl">
      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="modal-head">
          <div>
            <p class="section-kicker">IEPL Rule</p>
            <h2>${state.ieplRuleModal.mode === "edit" ? "编辑 IEPL 规则" : "新增 IEPL 规则"}</h2>
          </div>
          <button class="btn btn-ghost" data-action="iepl-modal-close">关闭</button>
        </div>
        <div class="rule-grid">
          <div class="field">
            <label for="iepl-rule-listen-port">监听端口</label>
            <input id="iepl-rule-listen-port" class="input" type="number" min="1" max="65535" value="${escapeHtml(draft.listen_port)}">
          </div>
          <div class="field">
            <label for="iepl-rule-remote-host">远端地址</label>
            <input id="iepl-rule-remote-host" class="input" type="text" value="${escapeHtml(draft.remote_host)}">
          </div>
          <div class="field">
            <label for="iepl-rule-remote-port">远端端口</label>
            <input id="iepl-rule-remote-port" class="input" type="number" min="1" max="65535" value="${escapeHtml(draft.remote_port)}">
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn btn-ghost" data-action="iepl-modal-close">取消</button>
          <button class="btn btn-primary" data-action="iepl-modal-save" ${state.busy ? "disabled" : ""}>${state.ieplRuleModal.mode === "edit" ? "保存 IEPL 规则" : "新增 IEPL 规则"}</button>
        </div>
      </div>
    </div>
  `;
}

function renderModals() {
  const root = $("modal-root");
  if (!root) return;
  root.innerHTML = `${renderDomainModal()}${renderIeplRuleModal()}`;
}

function syncTabState() {
  const mainSections = ["domains", "frontend", "iepl"];
  for (const section of mainSections) {
    const button = $(`main-tab-${section}`);
    const panel = $(`main-section-${section}`);
    const active = state.activeMainSection === section;
    button?.classList.toggle("active", active);
    if (panel) {
      panel.hidden = !active;
    }
  }

  const diagnosticsTabs = ["summary", "config", "logs"];
  for (const tab of diagnosticsTabs) {
    const button = $(`diagnostics-tab-${tab}`);
    const panel = $(`diagnostics-panel-${tab}`);
    const active = state.activeDiagnosticsTab === tab;
    button?.classList.toggle("active", active);
    if (panel) {
      panel.hidden = !active;
    }
  }
}

function render() {
  renderFlash();
  renderAppMode();
  renderSetup();
  renderStats();
  renderDomains();
  renderFrontendNodes();
  renderIeplRules();
  renderIeplNodes();
  renderSummary();
  renderLogs();
  renderModals();
  syncTabState();
}

async function submitSetup() {
  syncSetupDraftFromInput();
  const settings_text = String(state.setup?.settingsText || "").trim();
  if (!settings_text) {
    throw new Error("settings.json 不能为空");
  }
  await runBusyAction(async () => {
    const response = await apiPost("/api/setup-save", { settings_text });
    showFlash("success", response.result.message);
    await refreshBootstrap();
    if (!isSetupMode()) {
      await refreshOverview({ keepFlash: true });
    }
  });
}

async function handleAction(action, button) {
  if (state.busy && !["modal-close", "iepl-modal-close"].includes(action)) return;
  if (action !== "domain-toggle-menu" && state.openDomainActionMenu) {
    state.openDomainActionMenu = null;
  }
  switch (action) {
    case "domain-toggle-details":
      toggleDomainCardExpanded(button.dataset.recordName);
      break;
    case "domain-toggle-menu":
      toggleDomainActionMenu(button.dataset.recordName);
      break;
    case "domain-edit":
      openEditDomainModal(button.dataset.recordName);
      break;
    case "domain-delete":
      await deleteDomain(button.dataset.recordName);
      break;
    case "domain-sync":
      await runBusyAction(async () => {
        const response = await apiPost("/api/domain-sync", { record_name: button.dataset.recordName });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "domain-switch":
      await runBusyAction(async () => {
        const response = await apiPost("/api/domain-switch-now", { record_name: button.dataset.recordName });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "domain-reinstall":
      await runBusyAction(async () => {
        const response = await apiPost("/api/domain-reinstall-forward", { record_name: button.dataset.recordName });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "save-traffic-limit":
      {
        const input = $(`traffic-limit-${button.dataset.uuid}`);
        const traffic_limit_gb = Number.parseInt(input?.value || "", 10);
        if (!Number.isInteger(traffic_limit_gb) || traffic_limit_gb <= 0) {
          throw new Error("流量上限必须是正整数 GB");
        }
        await runBusyAction(async () => {
          const response = await apiPost("/api/set-frontend-node-traffic-limit", { uuid: button.dataset.uuid, traffic_limit_gb });
          showFlash("success", response.result.message);
          await refreshOverview({ keepFlash: true });
        });
      }
      break;
    case "clear-traffic-limit":
      await runBusyAction(async () => {
        const response = await apiPost("/api/clear-frontend-node-traffic-limit", { uuid: button.dataset.uuid });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "reinstall-node-forward":
      await runBusyAction(async () => {
        const response = await apiPost("/api/reinstall-forward", { uuid: button.dataset.uuid });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "iepl-rule-edit":
      openEditIeplRuleModal(button.dataset.listenPort);
      break;
    case "iepl-rule-delete":
      await deleteIeplRule(button.dataset.listenPort);
      break;
    case "iepl-reinstall":
      await runBusyAction(async () => {
        const response = await apiPost("/api/reinstall-forward", { uuid: button.dataset.uuid });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "iepl-restart-realm":
      await runBusyAction(async () => {
        const response = await apiPost("/api/restart-iepl-realm", { uuid: button.dataset.uuid });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "iepl-cleanup-legacy":
      await runBusyAction(async () => {
        const response = await apiPost("/api/cleanup-iepl-legacy", { uuid: button.dataset.uuid });
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      });
      break;
    case "modal-close":
      closeDomainModal();
      break;
    case "modal-save-domain":
      await submitDomainModal();
      break;
    case "modal-rule-save":
      saveDomainDraftRule();
      break;
    case "modal-rule-edit":
      editDomainDraftRule(button.dataset.listenPort);
      break;
    case "modal-rule-delete":
      deleteDomainDraftRule(button.dataset.listenPort);
      break;
    case "modal-rule-cancel":
      cancelDomainRuleEditor();
      break;
    case "modal-backup-add":
      addBackupToDomainDraft();
      break;
    case "modal-backup-remove":
      removeBackupFromDomainDraft(button.dataset.uuid);
      break;
    case "modal-backup-up":
      moveBackup(button.dataset.uuid, -1);
      break;
    case "modal-backup-down":
      moveBackup(button.dataset.uuid, 1);
      break;
    case "iepl-modal-close":
      closeIeplRuleModal();
      break;
    case "iepl-modal-save":
      await submitIeplRuleModal();
      break;
    default:
      break;
  }
}

function handleDocumentInput(event) {
  if (event.target.id === "setup-editor") {
    syncSetupDraftFromInput();
    return;
  }
  if (event.target.id === "domain-preferred-primary") {
    syncDomainDraftFromInputs();
    renderModals();
  }
}

function handleDocumentKeydown(event) {
  if (event.key === "Escape") {
    if (state.domainModal?.open) {
      closeDomainModal();
      return;
    }
    if (state.ieplRuleModal?.open) {
      closeIeplRuleModal();
      return;
    }
    if (state.openDomainActionMenu) {
      closeDomainActionMenu();
    }
  }
  if (event.key !== "Enter" || event.shiftKey || event.target.tagName === "TEXTAREA") return;
  if (isSetupMode()) {
    return;
  }
  if (state.domainModal?.open) {
    event.preventDefault();
    try {
      const hasPendingRuleFields =
        $("modal-rule-listen-port")?.value ||
        $("modal-rule-remote-host")?.value ||
        $("modal-rule-remote-port")?.value;
      if (hasPendingRuleFields) {
        saveDomainDraftRule();
      } else {
        submitDomainModal();
      }
    } catch (error) {
      showFlash("error", error.message || String(error));
    }
  } else if (state.ieplRuleModal?.open) {
    event.preventDefault();
    submitIeplRuleModal().catch((error) => showFlash("error", error.message || String(error)));
  }
}

async function bootstrap() {
  document.querySelectorAll("[data-main-section]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeMainSection = button.dataset.mainSection || "domains";
      syncTabState();
    });
  });
  document.querySelectorAll("[data-diagnostics-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeDiagnosticsTab = button.dataset.diagnosticsTab || "summary";
      syncTabState();
    });
  });
  $("sync-all-btn")?.addEventListener("click", () =>
    runBusyAction(async () => {
      if (isSetupMode()) {
        await refreshBootstrap();
        showFlash("success", "setup state refreshed");
      } else {
        const response = await apiPost("/api/sync", {});
        showFlash("success", response.result.message);
        await refreshOverview({ keepFlash: true });
      }
    }),
  );
  $("refresh-btn")?.addEventListener("click", () =>
    runBusyAction(async () => {
      if (isSetupMode()) {
        await refreshBootstrap();
        showFlash("success", "setup draft reloaded");
      } else {
        await refreshOverview();
        showFlash("success", "overview refreshed");
      }
    }),
  );
  $("refresh-logs-btn")?.addEventListener("click", () =>
    runBusyAction(async () => {
      await refreshLogs();
      showFlash("success", "logs refreshed");
    }),
  );
  $("add-domain-btn")?.addEventListener("click", openCreateDomainModal);
  $("add-iepl-rule-btn")?.addEventListener("click", openCreateIeplRuleModal);
  $("setup-reload-btn")?.addEventListener("click", () =>
    runBusyAction(async () => {
      await refreshBootstrap();
      showFlash("success", "setup template reloaded");
    }),
  );
  $("setup-save-btn")?.addEventListener("click", () =>
    submitSetup().catch((error) => showFlash("error", error.message || String(error))),
  );
  $("cleanup-frontend-legacy-btn")?.addEventListener("click", () =>
    runBusyAction(async () => {
      const response = await apiPost("/api/cleanup-frontend-legacy", {});
      showFlash("success", response.result.message);
      await refreshOverview({ keepFlash: true });
    }),
  );
  document.addEventListener("click", async (event) => {
    const backdrop = event.target.closest(".modal-backdrop");
    if (backdrop && event.target === backdrop) {
      const modalKind = backdrop.dataset.modalKind;
      if (modalKind === "domain") {
        closeDomainModal();
      } else if (modalKind === "iepl") {
        closeIeplRuleModal();
      }
      return;
    }
    const clickedDomainMenu = event.target.closest(".domain-card-menu");
    const clickedDomainMenuToggle = event.target.closest('[data-action="domain-toggle-menu"]');
    if (state.openDomainActionMenu && !clickedDomainMenu && !clickedDomainMenuToggle) {
      closeDomainActionMenu();
    }
    const button = event.target.closest("[data-action]");
    if (!button) return;
    event.preventDefault();
    try {
      await handleAction(button.dataset.action, button);
    } catch (error) {
      showFlash("error", error.message || String(error));
    }
  });
  document.addEventListener("input", handleDocumentInput);
  document.addEventListener("keydown", handleDocumentKeydown);
  setBusy(true);
  try {
    await refreshBootstrap();
    if (!isSetupMode()) {
      await refreshOverview();
    }
  } catch (error) {
    showFlash("error", error.message || String(error));
  } finally {
    setBusy(false);
    syncTabState();
  }
}

document.addEventListener("DOMContentLoaded", bootstrap);

