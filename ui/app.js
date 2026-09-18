'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const api = window.fusion;
  const state = {config: null, status: null, runtime: null, messages: [], turns: new Map(), conversationId: null,
    activeProvider: null, busy: false, settings: false, settingsSection: 'models', dirty: false,
    started: 0, loadingHistory: false, saving: false, pollPending: false, statusErrors: 0,
    browserProfiles: [], browserDetecting: false, browserImporting: false, browserMaintenance: false,
    layout: {mode: 'split', providers: [], chatShare: 36, paneShare: 50}, layoutUpdating: false, dragging: null,
    run: null,
    diagnosticPending: false, diagnosticOpen: false, diagnostic: null, focusedPane: 0, inputLeaseActive: false, recoveryOpening: false};
  const layoutStorageKey = 'multillm-fusion.layout.v2';
  let layoutTail = Promise.resolve();
  try {
    const current = JSON.parse(localStorage.getItem(layoutStorageKey));
    const saved = current || JSON.parse(localStorage.getItem('multillm-fusion.layout.v1'));
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
      // Start this upgrade side by side once; retain pane choices and sizes.
      // Subsequent explicit layout choices use v2 and remain unchanged on restart.
      if (current && ['tabs', 'split', 'windows'].includes(current.mode)) state.layout.mode = current.mode;
      state.layout.providers = Array.isArray(saved.providers) ? saved.providers.filter((id) => typeof id === 'string').slice(0, 2) : [];
      for (const key of ['chatShare', 'paneShare']) if (Number.isFinite(saved[key])) state.layout[key] = Math.max(20, Math.min(80, saved[key]));
      if (typeof saved.activeProvider === 'string') state.activeProvider = saved.activeProvider;
    }
  } catch {}
  let boundsFrame = null;
  let toastTimer = null;
  let statusTimer = null;
  let progressTimer = null;
  const providerStates = {};
  const stateNames = {ready: '就绪', idle: '待命', loading: '加载中', generating: '生成中', submitting: '正在提交',
    verification_required: '等待人工验证', verification_cleared: '验证已解除', rate_limited: '访问间隔等待',
    extracting: '提取中', waiting: '等待中', busy: '工作中', retrying: '网页重试中', manual_retry_required: '请在网页重试', recovering: '恢复采集中', error: '需要处理', disconnected: '连接中断',
    login_required: '请先登录', logged_out: '请先登录', connected: '已连接', unknown: '待确认'};
  const activeStates = new Set(['loading', 'generating', 'submitting', 'extracting', 'waiting', 'busy', 'retrying', 'manual_retry_required', 'recovering', 'verification_required', 'rate_limited']);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const text = (tag, content, className) => {const el = document.createElement(tag); if (content !== undefined) el.textContent = content; if (className) el.className = className; return el;};
  const providerName = (id) => state.config?.providers.find((p) => p.id === id)?.name || id;
  const enabledProviders = () => state.config?.providers.filter((p) => p.enabled) || [];
  const errorMessage = (error) => error?.message || String(error || '发生未知错误');
  const loginProviders = new Set(['chatgpt', 'deepseek', 'qwen', 'claude', 'grok', 'glm', 'kimi']);
  const isImporting = () => state.browserImporting || state.browserMaintenance || !!state.status?.browser_maintenance;
  const inspectedProvider = () => state.layout.mode === 'split' ? state.layout.providers[state.focusedPane] || state.activeProvider : state.activeProvider;
  const canReopenWindows = () => !!state.config && state.layout.mode === 'windows' && !state.settings && !isImporting() && !state.layoutUpdating && !state.diagnosticPending && !state.diagnosticOpen && !state.dragging && !state.saving;
  const isLocked = () => state.busy || state.loadingHistory || state.saving || state.layoutUpdating || state.diagnosticPending || state.diagnosticOpen || !!state.dragging || isImporting() || !!state.status?.busy;
  const canOpenRecovery = () => !!api?.openRecoveryProvider && !!state.config && !state.settings && !isImporting() && !state.inputLeaseActive &&
    !state.layoutUpdating && !state.recoveryOpening && !state.saving && !state.loadingHistory && !state.diagnosticPending && !state.diagnosticOpen && !state.dragging;

  function toast(message) {clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false; toastTimer = setTimeout(() => {$('toast').hidden = true;}, 3600);}
  function setError(id, message) {$(id).textContent = message || ''; $(id).hidden = !message;}
  function safeMarkdown(markdown) {
    const container = text('div', undefined, 'markdown');
    if (window.marked && window.DOMPurify) {
      container.innerHTML = window.DOMPurify.sanitize(window.marked.parse(String(markdown || ''), {gfm: true, breaks: false}), {
        USE_PROFILES: {html: true}, FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select', 'iframe', 'video', 'audio'],
        FORBID_ATTR: ['style', 'srcset', 'id', 'name'], ALLOW_DATA_ATTR: false
      });
    } else {container.textContent = markdown; container.classList.add('plain-markdown');}
    return container;
  }

  function renderMessages() {
    $('messages').replaceChildren();
    $('empty-state').hidden = state.messages.length > 0 || state.busy;
    state.messages.forEach((message, index) => {
      if (!['user', 'assistant', 'system'].includes(message.role)) return;
      const section = text('article', undefined, `message ${message.role}`);
      const heading = text('div', undefined, 'message-heading');
      heading.append(text('span', message.role === 'assistant' ? '✦' : message.role === 'system' ? 'S' : '你', 'avatar'));
      heading.append(text('span', message.role === 'assistant' ? 'MultiLLM Fusion' : message.role === 'system' ? '系统' : '你'));
      if (message.role === 'assistant') heading.append(text('span', '融合结果', 'message-label-note'));
      section.append(heading);
      const body = text('div', undefined, 'message-body');
      if (message.role === 'assistant') body.append(safeMarkdown(message.content)); else body.textContent = message.content;
      section.append(body);
      if (message.role === 'assistant') {
        const turn = state.turns.get(index);
        const actions = text('div', undefined, 'message-tools');
        const copy = text('button', '复制 Markdown', 'button subtle'); copy.type = 'button';
        copy.addEventListener('click', () => copyText(message.content));
        const save = text('button', '导出 .md ↗', 'button subtle'); save.type = 'button';
        save.addEventListener('click', () => saveMarkdown(`fusion-${turn?.request_id || index}.md`, message.content));
        actions.append(copy, save);
        if (turn?.mode) actions.append(text('span', modeName(turn.mode), 'mode-label'));
        section.append(actions);
        if (turn?.sources?.length) {
          const sources = text('div', undefined, 'source-list');
          turn.sources.forEach((source) => {
            const item = text('details', undefined, 'source-item');
            const summary = text('summary'); summary.append(text('strong', providerName(source.provider)), text('span', '查看原始回答'));
            const content = text('div', undefined, 'source-content'); content.append(safeMarkdown(source.markdown));
            const exportButton = text('button', '导出原始 Markdown ↗', 'button subtle'); exportButton.type = 'button';
            exportButton.addEventListener('click', () => saveMarkdown(`${source.provider}-${turn.request_id || index}.md`, source.markdown));
            content.append(exportButton); item.append(summary, content); sources.append(item);
          });
          section.append(sources);
        }
        if (turn?.errors?.length) {
          const errors = turn.errors.map((error) => typeof error === 'string' ? error : `${error.provider ? providerName(error.provider) + '：' : ''}${error.message || error.code || '未能返回结果'}`);
          section.append(text('div', `本轮提示\n${errors.join('\n')}`, 'run-error'));
        }
      }
      $('messages').append(section);
    });
    if (state.busy) {
      const placeholder = text('div', undefined, 'busy-placeholder');
      placeholder.append(text('span', undefined, 'spinner'), text('span', '正在收集回答并整合，请稍候…')); $('messages').append(placeholder);
    }
  }
  function modeName(mode) {return ({web: '网页语义整合', api: 'API 语义整合', single: '单模型结果', passthrough: '单模型结果', partial: '部分结果'})[mode] || mode;}
  function scrollBottom() {requestAnimationFrame(() => {$('conversation-scroll').scrollTop = $('conversation-scroll').scrollHeight;});}
  async function copyText(value) {try {await api.copyText(String(value)); toast('已复制');} catch (error) {toast(`复制失败：${errorMessage(error)}`);}}
  async function saveMarkdown(name, content) {try {const result = await api.saveMarkdown({name: name.replace(/[^a-zA-Z0-9._-]/g, '_'), content: String(content || '')}); if (!result.canceled) toast('Markdown 已导出');} catch (error) {toast(`导出失败：${errorMessage(error)}`);}}

  function saveLayoutPreference() {
    try {localStorage.setItem(layoutStorageKey, JSON.stringify({...state.layout, activeProvider: state.activeProvider}));} catch {}
  }
  function reconcileLayout() {
    const ids = enabledProviders().map((provider) => provider.id);
    if (!ids.includes(state.activeProvider)) state.activeProvider = ids[0] || null;
    state.layout.providers = [...new Set(state.layout.providers.filter((id) => ids.includes(id)))];
    for (const id of ids) if (state.layout.providers.length < 2 && !state.layout.providers.includes(id)) state.layout.providers.push(id);
    if (state.layout.mode === 'split' && ids.length < 2) state.layout.mode = 'tabs';
    if (!api?.setLayout) state.layout.mode = 'tabs';
  }
  function applyRatios() {
    const {chatShare, paneShare} = state.layout;
    $('workspace').style.setProperty('--chat-share', `${chatShare}fr`);
    $('workspace').style.setProperty('--web-share', `${100 - chatShare}fr`);
    $('webview-group').style.setProperty('--left-share', `${paneShare}fr`);
    $('webview-group').style.setProperty('--right-share', `${100 - paneShare}fr`);
    $('workspace-splitter').setAttribute('aria-valuenow', String(Math.round(chatShare)));
    $('panes-splitter').setAttribute('aria-valuenow', String(Math.round(paneShare)));
  }
  function renderLayout() {
    reconcileLayout();
    const split = state.layout.mode === 'split', windows = state.layout.mode === 'windows';
    $('layout-mode').value = state.layout.mode;
    $('layout-mode').querySelector('[value="split"]').disabled = enabledProviders().length < 2 || !api?.setLayout;
    $('layout-mode').querySelector('[value="windows"]').disabled = !api?.setLayout;
    document.querySelector('.browser-panel').dataset.layout = state.layout.mode;
    $('provider-tabs').hidden = split;
    $('pane-heading-0').hidden = !split;
    $('model-pane-1').hidden = !split;
    $('panes-splitter').hidden = !split;
    $('webview-group').hidden = windows;
    $('webview-group').classList.toggle('split', split);
    $('windows-placeholder').hidden = !windows;
    $('show-model-windows').hidden = !windows;
    for (let index = 0; index < 2; index++) {
      const select = $(`pane-provider-${index}`);
      const ids = enabledProviders().map((provider) => provider.id).join(',');
      if (select.dataset.options !== ids) {
        select.replaceChildren(...enabledProviders().map((provider) => new Option(provider.name, provider.id)));
        select.dataset.options = ids;
      }
      select.value = state.layout.providers[index] || '';
      for (const option of select.options) option.disabled = option.value === state.layout.providers[1 - index];
      const status = providerStates[state.layout.providers[index]] || {};
      $(`pane-status-${index}`).textContent = stateNames[status.state] || status.state || '待确认';
      $(`model-pane-${index}`).classList.toggle('active-pane', state.layout.providers[index] === inspectedProvider());
    }
    $('layout-active-provider').textContent = inspectedProvider() ? `当前：${providerName(inspectedProvider())}` : '';
    applyRatios();
  }
  function measuredBounds(id) {
    const rect = $(id).getBoundingClientRect();
    return {x: Math.max(0, Math.round(rect.left + 1)), y: Math.max(0, Math.round(rect.top + 1)),
      width: Math.max(1, Math.round(rect.width - 2)), height: Math.max(1, Math.round(rect.height - 2))};
  }
  function layoutPacket(extra = {}) {
    const mode = state.layout.mode;
    const ids = mode === 'split' ? state.layout.providers.slice(0, 2) : mode === 'tabs' ? [state.activeProvider].filter(Boolean) : [];
    return {mode, active_provider: state.activeProvider || undefined, panes: ids.map((id, index) => ({provider_id: id,
      bounds: measuredBounds(index === 0 ? 'webview-placeholder' : 'webview-placeholder-1')})),
      hidden: state.settings || isImporting() || !!state.dragging || state.diagnosticOpen, ...extra};
  }
  function publishLayout(extra = {}) {
    const packet = layoutPacket(extra);
    const operation = layoutTail.catch(() => {}).then(async () => {
      if (api?.setLayout) {
        const result = await api.setLayout(packet);
        if (result?.ok === false) throw new Error(result.error?.message || result.message || '原生网页布局未被接受');
        return result;
      }
      return api?.setBounds(packet.hidden ? null : packet.panes[0]?.bounds || null);
    });
    layoutTail = operation;
    return operation;
  }
  function scheduleBounds() {
    if (boundsFrame !== null) cancelAnimationFrame(boundsFrame);
    boundsFrame = requestAnimationFrame(() => {
      boundsFrame = null;
      if (api && state.config && !state.recoveryOpening) publishLayout().catch((error) => {console.warn('浏览器区域布局失败', errorMessage(error));});
    });
  }
  function applyRecoveryLayout(layout) {
    if (!layout?.ok || !['tabs', 'split', 'windows'].includes(layout.mode) || !enabledProviders().some(provider => provider.id === layout.active_provider)) return;
    state.layout.mode = layout.mode;
    state.activeProvider = layout.active_provider;
    if (layout.mode === 'split' && Array.isArray(layout.panes)) state.layout.providers = layout.panes.map(pane => pane.provider_id);
    state.focusedPane = Math.max(0, state.layout.providers.indexOf(state.activeProvider));
    renderProviderTabs(); renderLayout(); updateProviderStatus(); saveLayoutPreference();
  }
  async function openRecoveryProvider(id) {
    if (!canOpenRecovery() || !['manual_retry_required', 'verification_required'].includes(providerStates[id]?.state)) return;
    state.recoveryOpening = true; state.layoutUpdating = true; updateControls();
    if (boundsFrame !== null) {cancelAnimationFrame(boundsFrame); boundsFrame = null;}
    const operation = layoutTail.catch(() => {}).then(() => api.openRecoveryProvider(id));
    layoutTail = operation;
    try {
      const result = await operation;
      if (result?.ok !== true) throw new Error(result?.message || '网页重试入口暂不可用');
      applyRecoveryLayout(result);
      toast(providerStates[id]?.state === 'verification_required' ? `已打开 ${providerName(id)} 验证页面，请手动完成验证；程序不会刷新或自动重复发送。` : `已打开 ${providerName(id)} 原会话，请在网页点击重试；程序会继续等待本轮回答。`);
    } catch (error) {toast(`无法处理网页重试：${errorMessage(error)}`);}
    finally {state.recoveryOpening = false; state.layoutUpdating = false; updateControls(); scheduleBounds();}
  }
  function renderRecoveryActions() {
    const container = $('recovery-actions');
    const targets = enabledProviders().filter(provider => ['manual_retry_required', 'verification_required'].includes(providerStates[provider.id]?.state));
    container.hidden = !targets.length || !api?.openRecoveryProvider;
    container.replaceChildren(...targets.map(provider => {
      const button = text('button', `${providerStates[provider.id]?.state === 'verification_required' ? '完成人工验证' : '处理网页重试'} · ${provider.name}`, 'button subtle');
      button.type = 'button'; button.dataset.recoveryProvider = provider.id; button.disabled = !canOpenRecovery();
      button.addEventListener('click', () => openRecoveryProvider(provider.id));
      return button;
    }));
  }
  async function selectProvider(id) {
    if (isLocked() || state.settings) return;
    if (!enabledProviders().some((provider) => provider.id === id)) return;
    const previous = state.activeProvider;
    state.activeProvider = id; state.layoutUpdating = true; renderProviderTabs(); renderLayout(); updateProviderStatus(); updateControls();
    try {
      if (!api.setLayout || state.layout.mode === 'windows') await api.showProvider(id);
      await publishLayout(); saveLayoutPreference();
    } catch (error) {state.activeProvider = previous; renderLayout(); scheduleBounds(); toast(`无法打开模型：${errorMessage(error)}`);}
    finally {state.layoutUpdating = false; renderProviderTabs(); updateProviderStatus(); updateControls();}
  }
  async function changeLayout(mode, index, providerId) {
    if (isLocked() || state.settings) {renderLayout(); return;}
    if (!['tabs', 'split', 'windows'].includes(mode)) return;
    if (mode === 'split' && enabledProviders().length < 2) return;
    const previous = clone(state.layout), previousActive = state.activeProvider, previousFocus = state.focusedPane;
    if (index !== undefined) {
      if (!enabledProviders().some((provider) => provider.id === providerId) || state.layout.providers[1 - index] === providerId) {renderLayout(); return;}
      state.layout.providers[index] = providerId; state.activeProvider = providerId; state.focusedPane = index;
    }
    state.layout.mode = mode;
    if (mode === 'split' && index === undefined) state.focusedPane = Math.max(0, state.layout.providers.indexOf(state.activeProvider));
    renderLayout(); state.layoutUpdating = true; updateControls();
    try {await publishLayout(); saveLayoutPreference();}
    catch (error) {state.layout = previous; state.activeProvider = previousActive; state.focusedPane = previousFocus; renderLayout(); scheduleBounds(); toast(`切换布局失败：${errorMessage(error)}`);}
    finally {state.layoutUpdating = false; renderProviderTabs(); updateProviderStatus(); updateControls();}
  }
  function resizeShare(kind, proposed) {
    const container = $(kind === 'workspace' ? 'workspace' : 'webview-group');
    const rect = container.getBoundingClientRect();
    const padding = kind === 'workspace' ? 40 : 0;
    const width = Math.max(1, rect.width - padding - 12);
    const minimum = kind === 'workspace' ? 240 : 190;
    const minShare = Math.min(45, Math.max(20, minimum / width * 100));
    const value = Math.max(minShare, Math.min(100 - minShare, proposed));
    state.layout[kind === 'workspace' ? 'chatShare' : 'paneShare'] = value;
    $(kind === 'workspace' ? 'workspace-splitter' : 'panes-splitter').setAttribute('aria-valuemin', String(Math.ceil(minShare)));
    $(kind === 'workspace' ? 'workspace-splitter' : 'panes-splitter').setAttribute('aria-valuemax', String(Math.floor(100 - minShare)));
    applyRatios();
  }
  function finishResize(event) {
    if (!state.dragging || (event?.pointerId !== undefined && event.pointerId !== state.dragging.pointerId)) return;
    state.dragging = null; $('resize-overlay').hidden = true; document.body.classList.remove('resizing');
    saveLayoutPreference(); updateControls(); scheduleBounds();
  }
  function setupSplitter(id, kind) {
    $(id).addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || isLocked() || state.settings) return;
      event.preventDefault();
      state.dragging = {kind, pointerId: event.pointerId};
      $('resize-overlay').hidden = false; document.body.classList.add('resizing'); updateControls();
      // Hide BrowserViews before the pointer moves over them; they live above renderer DOM.
      publishLayout().catch((error) => {finishResize(); toast(`无法开始调整：${errorMessage(error)}`);});
      $(id).focus();
    });
    $(id).addEventListener('keydown', (event) => {
      if (isLocked() || state.settings || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const key = kind === 'workspace' ? 'chatShare' : 'paneShare';
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 100 : state.layout[key] + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 10 : 2);
      resizeShare(kind, next); saveLayoutPreference(); scheduleBounds();
    });
  }
  async function diagnoseSend() {
    const providerId = inspectedProvider();
    if (isLocked() || state.settings || !providerId || !api?.diagnoseSend) return;
    state.diagnosticPending = true; updateControls();
    try {
      // Capture the live page first. Hiding it first would falsify visibility and hit tests.
      state.diagnostic = await api.diagnoseSend(providerId);
      $('send-diagnostic-title').textContent = `${providerName(providerId)} 发送按钮检测`;
      const report = state.diagnostic || {}, target = report.sendTarget;
      $('send-diagnostic-summary').textContent = [
        `输入框：${report.input ? '已找到' : '未找到'}`,
        `发送按钮：${target ? '已找到' : '未匹配'}`,
        target ? `禁用：${target.disabled ? '是' : '否'}` : '',
        target ? `遮挡：${target.obscured === null || target.obscured === undefined ? '未判断' : target.obscured ? '是' : '否'}` : '',
        report.page ? `页面：${report.page.visibilityState || '未知'} / 焦点 ${report.page.hasFocus ? '有' : '无'}` : ''
      ].filter(Boolean).join(' · ');
      $('send-diagnostic-json').textContent = JSON.stringify(report, null, 2);
      state.diagnosticOpen = true; await publishLayout(); $('send-diagnostic-dialog').hidden = false;
      $('close-send-diagnostic').focus();
    } catch (error) {state.diagnosticOpen = false; toast(`按钮检测失败：${errorMessage(error)}`); scheduleBounds();}
    finally {state.diagnosticPending = false; updateControls();}
  }
  function closeDiagnostic() {
    state.diagnosticOpen = false; $('send-diagnostic-dialog').hidden = true; scheduleBounds(); updateControls(); $('diagnose-send').focus();
  }
  function renderProviderTabs() {
    const providers = enabledProviders(); $('enabled-count').textContent = providers.length;
    $('provider-tabs').replaceChildren();
    providers.forEach((provider) => {
      const tab = text('button', undefined, `provider-tab${provider.id === state.activeProvider ? ' active' : ''}`);
      tab.type = 'button'; tab.dataset.provider = provider.id; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(provider.id === state.activeProvider));
      tab.disabled = isLocked() || state.settings;
      tab.append(text('span', provider.name.slice(0, 1).toUpperCase(), 'provider-initial'), text('span', provider.name), text('span', undefined, dotClass(providerStates[provider.id]?.state)));
      tab.title = providerStates[provider.id]?.message || provider.url;
      tab.addEventListener('click', () => selectProvider(provider.id)); $('provider-tabs').append(tab);
    });
  }
  function dotClass(value) {return `status-dot ${activeStates.has(value) ? 'busy' : ['ready', 'idle', 'connected'].includes(value) ? 'ready' : ['error', 'login_required', 'logged_out', 'disconnected'].includes(value) ? 'error' : ''}`;}
  function updateProviderStatus() {
    const providerId = inspectedProvider();
    const provider = state.config?.providers.find((item) => item.id === providerId);
    const status = providerStates[providerId] || {};
    $('provider-address').textContent = provider?.url || '尚未选择模型';
    $('provider-state-label').textContent = stateNames[status.state] || status.state || '待确认';
    $('provider-state-dot').className = dotClass(status.state);
    $('provider-message').textContent = status.message || '首次使用请在网页中登录';
    $('provider-message').title = status.message || '';
  }
  const progressNames = {queued: '等待调度', preparing: '准备网页', rate_limited: '等待访问间隔',
    send_dispatched: '已发送，等待接收', accepted: '已接收', server_responded: '服务器已响应',
    waiting_response: '等待回复', generating: '生成中', collecting: '提取并检查完整性',
    provider_completed: '已获取回复，保存原文中', candidate_saved: '完成（原文已保存）',
    provider_failed: '请求失败', candidate_failed: '失败', candidate_timed_out: '超时',
    candidate_cancelled: '已取消', retrying: '恢复重试中', manual_retry_required: '等待网页手动处理',
    retry_unavailable: '未启动重试', recovering: '恢复采集中', verification_required: '等待人工验证', verification_cleared: '验证已解除'};
  function applyRunProgress(run, report) {
    if (state.run !== run || !report || !Array.isArray(report.events)) return;
    const terminalPhase = run.finalized ? run.phase : null;
    for (const row of report.events) {
      if (!Number.isInteger(row.sequence) || row.sequence <= run.sequence) continue;
      run.sequence = row.sequence;
      if (row.stage === 'candidates_started') {run.phase = 'candidates'; run.candidateStarted ||= Date.now();}
      if (row.stage === 'fusion_started') {run.phase = 'fusion'; run.fusionStarted ||= Date.now();}
      if (row.stage === 'fusion_completed') run.phase = 'saving';
      if (['completed', 'failed', 'cancelled'].includes(row.stage)) run.phase = row.stage;
      if (row.purpose === 'candidate' && Object.hasOwn(run.providers, row.provider) && progressNames[row.stage]) {
        // Terminal saved/failed states cannot be replaced by a delayed live stage.
        const prior = run.providers[row.provider];
        if (!['candidate_saved', 'candidate_failed', 'candidate_timed_out', 'candidate_cancelled'].includes(prior)) {
          run.providers[row.provider] = row.stage;
          run.retryDetails ||= {};
          // Body-free progress comes from the local runner, never website prose.
          run.retryDetails[row.provider] = { stage: row.retry_stage || (['retrying','manual_retry_required'].includes(row.stage) ? run.retryDetails[row.provider]?.stage : undefined), remaining: row.remaining_seconds, reason: row.retry_reason };
        }
      }
      if (row.purpose === 'fusion' && row.provider && progressNames[row.stage]) run.fusionStage = `${providerName(row.provider)}：${progressNames[row.stage]}`;
    }
    if (terminalPhase) run.phase = terminalPhase;
    renderRunProgress();
  }
  async function pollRunProgress() {
    const run = state.run;
    if (!run || run.pollPending || !run.progressId) return;
    run.pollPending = true;
    try {
      const report = await api.request('GET', `/v1/progress/${run.progressId}?after=${run.sequence}`);
      applyRunProgress(run, report);
      run.progressError = false;
    } catch {run.progressError = true;} // Never fail/resubmit a chat because a status poll failed.
    finally {run.pollPending = false; if (state.run === run) renderRunProgress();}
  }
  function renderRunProgress() {
    const run = state.run, panel = $('model-progress');
    panel.hidden = !run;
    if (!run) return;
    const entries = Object.entries(run.providers), count = entries.filter(([, stage]) => stage === 'candidate_saved').length;
    const pending = entries.filter(([, stage]) => !['candidate_saved', 'candidate_failed', 'candidate_timed_out', 'candidate_cancelled'].includes(stage));
    let summary;
    if (run.phase === 'fusion' || run.phase === 'saving') summary = `${count}/${entries.length} 个候选已完成 · ${run.phase === 'saving' ? '保存整合结果' : '正在整合'}${run.fusionStage ? ' · ' + run.fusionStage : ''}`;
    else if (run.phase === 'completed') summary = '本轮已完成';
    else if (run.phase === 'failed' || run.phase === 'cancelled') summary = `${count}/${entries.length} 个候选已完成 · 本轮${run.phase === 'failed' ? '失败' : '已取消'}，未返回不完整整合结果`;
    else if (!run.candidateStarted) summary = `等待调度 · 候选阶段限时 ${run.timeout} 秒`;
    else {
      const remaining = Math.max(0, Math.ceil(run.timeout - (Date.now() - run.candidateStarted) / 1000));
      summary = `${count}/${entries.length} 已完成 · ${pending.length ? '等待 ' + pending.map(([id]) => providerName(id)).join('、') : '正在确认候选结果'} · 剩余约 ${remaining} 秒`;
    }
    if (run.progressError && state.busy) summary += ' · 状态同步暂不可用（不会重发请求）';
    const heading = $('model-progress-summary'); if (heading.textContent !== summary) heading.textContent = summary;
    const rows = $('model-progress-rows');
    for (const [id, stage] of entries) {
      let row = Array.from(rows.children).find(node => node.dataset.provider === id);
      if (!row) {row = text('div', undefined, 'model-progress-row'); row.dataset.provider = id; row.append(text('strong', providerName(id)), text('span')); rows.append(row);}
      row.dataset.stage = stage;
      let label = progressNames[stage] || stage;
      const retry = run.retryDetails?.[id];
      const retryLabels = {dom_handler:'第 1/3 级：页面重试', screenshot_click:'第 2/3 级：截图定位并点击', manual:'第 3/3 级：等待人工重试'};
      if (['retrying','manual_retry_required'].includes(stage) && retryLabels[retry?.stage]) {
        label = retryLabels[retry.stage];
        if (retry.stage === 'manual' && Number.isFinite(retry.remaining)) label += ` · 剩余 ${retry.remaining} 秒`;
      }
      if (stage === 'retry_unavailable') label += ' · ' + ({total_deadline_expired:'本轮总时限已耗尽',recovery_disabled:'恢复等待设置为 0',cancelled:'任务已取消',not_submitted:'未提交问题',already_attempted:'本轮已尝试'}[retry?.reason] || '请查看日志');
      if (row.lastElementChild.textContent !== label) row.lastElementChild.textContent = label;
    }
  }
  function updateControls() {
    const locked = isLocked();
    ['open-settings', 'open-api', 'new-chat', 'history-select'].forEach((id) => {$(id).disabled = locked || !state.config;});
    $('prompt').disabled = locked;
    $('send-button').disabled = locked || !state.config || !state.status?.bridge_connected || !$('prompt').value.trim();
    $('send-label').textContent = state.busy ? '处理中' : '发送';
    $('reload-provider').disabled = locked || !inspectedProvider();
    $('run-status').hidden = !state.busy;
    if (state.busy) {
      const active = enabledProviders().filter((provider) => activeStates.has(providerStates[provider.id]?.state));
      $('run-status-text').textContent = active.length ? `${active.map((p) => p.name).join('、')} 正在处理…` : '正在等待模型回答与语义整合…';
      $('elapsed-time').textContent = `${Math.floor((Date.now() - state.started) / 1000)} 秒`;
    }
    const importing = isImporting();
    $('composer-note').textContent = importing ? '正在导入浏览器登录信息，请稍候' : state.busy ? (state.config?.allow_partial ? '等待所有候选任务结束，再整合成功回答（已允许部分结果）' : '等待所有候选模型成功完成回答，再进行整合') : !state.status?.bridge_connected ? '正在等待桌面浏览器连接' : state.status?.busy ? '另一个 API 请求正在使用模型网页' : `${enabledProviders().length} 个模型参与 · 请先完成网页登录`;
    $('fusion-description').textContent = state.config?.fusion.mode === 'api' ? 'API 语义整合 · 保留原文' : '网页语义整合 · 保留原文';
    const badge = $('connection-status');
    badge.className = `connection-badge ${state.status?.bridge_connected ? 'connected' : 'disconnected'}`;
    badge.lastElementChild.textContent = state.status?.bridge_connected ? (importing ? '正在导入登录' : state.status?.busy ? '模型正在工作' : '本地服务已连接') : '浏览器尚未连接';
    if (state.status?.queue_size) badge.title = `队列中有 ${state.status.queue_size} 个请求`; else badge.removeAttribute('title');
    $('provider-tabs').querySelectorAll('button').forEach((button) => {button.disabled = locked || state.settings;});
    $('settings-form').querySelectorAll('input,textarea,select').forEach((input) => {input.disabled = locked;});
    $('save-settings').disabled = locked; $('discard-settings').disabled = locked;
    $('close-settings').disabled = state.saving || importing;
    document.querySelectorAll('.settings-tab').forEach((button) => {button.disabled = state.saving || importing;});
    ['layout-mode', 'pane-provider-0', 'pane-provider-1'].forEach((id) => {$(id).disabled = locked || state.settings || !state.config;});
    $('show-model-windows').disabled = !canReopenWindows();
    $('diagnose-send').disabled = locked || state.settings || !inspectedProvider() || !api?.diagnoseSend;
    renderRecoveryActions();
    renderRunProgress();
    ['workspace-splitter', 'panes-splitter'].forEach((id) => {$(id).setAttribute('aria-disabled', String(locked || state.settings)); $(id).tabIndex = locked || state.settings ? -1 : 0;});
    updateBrowserLoginControls();
  }
  async function pollStatus() {
    if (state.pollPending || !api) return;
    state.pollPending = true;
    try {
      state.status = await api.request('GET', '/internal/status'); state.statusErrors = 0;
      Object.entries(state.status.providers || {}).forEach(([id, status]) => {providerStates[id] = status;});
      renderProviderTabs(); renderLayout(); updateProviderStatus();
    } catch (error) {
      state.statusErrors += 1;
      if (state.statusErrors >= 2) {state.status = {bridge_connected: false, busy: false}; $('connection-status').title = errorMessage(error);}
    } finally {state.pollPending = false; updateControls();}
  }
  async function loadHistory() {
    const result = await api.request('GET', '/internal/history');
    const select = $('history-select'); select.replaceChildren(new Option('历史对话', ''));
    (result.conversations || []).forEach((conversation) => select.add(new Option(conversation.title || '未命名对话', conversation.id)));
    select.value = state.conversationId || '';
  }
  async function openConversation(id) {
    if (!id || isLocked()) return;
    state.run = null; $('model-progress-rows').replaceChildren();
    state.loadingHistory = true; updateControls(); setError('chat-error', '');
    try {
      const conversation = await api.request('GET', `/internal/history/${encodeURIComponent(id)}`);
      state.messages = conversation.messages || []; state.conversationId = conversation.id; state.turns = new Map();
      const assistantIndexes = state.messages.map((m, i) => m.role === 'assistant' ? i : -1).filter((i) => i >= 0);
      const runs = conversation.runs || [];
      if (assistantIndexes.length) runs.slice(-assistantIndexes.length).forEach((run, i, arr) => state.turns.set(assistantIndexes[assistantIndexes.length - arr.length + i], run));
      $('conversation-title').textContent = conversation.title || '历史对话'; $('prompt').value = ''; renderMessages(); scrollBottom();
    } catch (error) {setError('chat-error', `打开对话失败：${errorMessage(error)}`); $('history-select').value = state.conversationId || '';}
    finally {state.loadingHistory = false; updateControls();}
  }
  function newConversation() {
    if (isLocked()) return;
    state.run = null; $('model-progress-rows').replaceChildren();
    state.conversationId = null; state.messages = []; state.turns = new Map(); $('history-select').value = '';
    $('conversation-title').textContent = '新的对话'; $('prompt').value = ''; setError('chat-error', ''); renderMessages(); updateControls(); $('prompt').focus();
  }
  async function send(event) {
    event.preventDefault();
    const draft = $('prompt').value;
    if (!draft.trim() || isLocked()) return;
    if (!state.status?.bridge_connected) {setError('chat-error', '桌面浏览器尚未连接，请等待服务就绪后重试。'); return;}
    const priorMessages = state.messages.slice();
    state.messages = [...priorMessages, {role: 'user', content: draft.trim()}];
    state.run = {progressId: crypto.randomUUID(), sequence: 0, pollPending: false, phase: 'queued',
      timeout: state.config.chat?.timeout_seconds ?? 60, candidateStarted: 0,
      providers: Object.fromEntries(enabledProviders().map(provider => [provider.id, 'queued']))};
    $('model-progress-rows').replaceChildren();
    state.busy = true; state.started = Date.now(); setError('chat-error', ''); renderMessages(); updateControls(); scrollBottom();
    try {
      const body = {model: 'web-fusion', messages: state.messages, stream: false};
      if (state.conversationId) body.conversation_id = state.conversationId;
      const result = await api.request('POST', '/v1/chat/completions', body, {progressId: state.run.progressId});
      await pollRunProgress();
      const content = result.choices?.[0]?.message?.content;
      if (typeof content !== 'string' || !content.trim()) throw new Error('服务未返回有效的文本结果。');
      state.run.phase = 'completed'; state.run.finalized = true;
      for (const source of result.fusion?.sources || []) if (Object.hasOwn(state.run.providers, source.provider)) state.run.providers[source.provider] = 'candidate_saved';
      state.messages.push({role: 'assistant', content});
      if (result.fusion) {state.conversationId = result.fusion.conversation_id || state.conversationId; state.turns.set(state.messages.length - 1, result.fusion);}
      $('prompt').value = ''; $('conversation-title').textContent = state.messages.find((m) => m.role === 'user')?.content.slice(0, 35) || '融合对话';
      try {await loadHistory();} catch (error) {toast(`回答已完成，但历史列表刷新失败：${errorMessage(error)}`);}
    } catch (error) {await pollRunProgress(); state.run.phase = 'failed'; state.run.finalized = true; state.messages = priorMessages; $('prompt').value = draft; setError('chat-error', `${errorMessage(error)}\n问题已保留，可检查右侧网页后重新发送。`);}
    finally {state.busy = false; renderMessages(); await pollStatus(); updateControls(); scrollBottom(); $('prompt').focus();}
  }

  function updateBrowserLoginControls() {
    const locked = isLocked();
    const available = !!state.config && !!state.status?.bridge_connected;
    const selectedProfile = state.browserProfiles.some((profile) => profile.id === $('browser-profile-select').value);
    const selectedProvider = enabledProviders().some((provider) => provider.id === $('browser-login-provider').value && loginProviders.has(provider.id));
    $('detect-browser-profiles').disabled = locked || state.browserDetecting;
    $('browser-profile-select').disabled = locked || state.browserDetecting || !state.browserProfiles.length;
    $('browser-login-provider').disabled = locked || !enabledProviders().some((provider) => loginProviders.has(provider.id));
    $('import-browser-login').disabled = locked || state.browserDetecting || !available || !selectedProfile || !selectedProvider;
    $('import-login-file').disabled = locked || !available || !selectedProvider;
    $('browser-login-progress').hidden = !isImporting();
    $('import-browser-login').textContent = state.browserImporting ? '正在导入…' : '从浏览器导入登录';
  }
  function renderBrowserProviderChoices() {
    const select = $('browser-login-provider'); const selected = select.value || state.activeProvider;
    select.replaceChildren();
    enabledProviders().filter((provider) => loginProviders.has(provider.id)).forEach((provider) => select.add(new Option(provider.name, provider.id)));
    if (Array.from(select.options).some((option) => option.value === selected)) select.value = selected;
    if (!select.options.length) select.add(new Option('请先保存并启用一个常见模型', ''));
  }
  function updateBrowserProfileDetails() {
    const profile = state.browserProfiles.find((item) => item.id === $('browser-profile-select').value);
    $('browser-profile-details').textContent = profile ? `${profile.browser} · ${profile.name}\n${profile.path}` : '';
    updateBrowserLoginControls();
  }
  function renderLoginWarnings(id, warnings) {
    const container = $(id); container.replaceChildren();
    const messages = Array.isArray(warnings) ? warnings.filter((warning) => warning && typeof warning.message === 'string').slice(0, 50) : [];
    if (messages.length) {
      const list = text('ul'); messages.forEach((warning) => list.append(text('li', warning.message.slice(0, 1500)))); container.append(list);
    }
    container.hidden = !messages.length;
  }
  async function detectBrowserProfiles() {
    if (state.browserDetecting || isLocked()) return;
    state.browserDetecting = true; $('browser-profiles-status').textContent = '正在检测浏览器配置目录…';
    setError('browser-login-error', ''); updateBrowserLoginControls();
    const selected = $('browser-profile-select').value;
    try {
      if (typeof api?.listBrowserProfiles !== 'function') throw new Error('当前桌面组件不支持登录导入，请完整更新应用并重新启动。');
      const result = await api.listBrowserProfiles();
      if (result?.ok === false) throw new Error(result.error?.message || '无法检测浏览器配置目录。');
      if (result?.ok !== true || !Array.isArray(result.profiles) || result.profiles.some((profile) => !profile ||
        !['id', 'browser', 'name', 'path'].every((key) => typeof profile[key] === 'string') || !profile.id || !['firefox', 'chromium'].includes(profile.family))) {
        throw new Error('浏览器配置检测返回了无效结果，请重新检测或更新应用。');
      }
      state.browserProfiles = result.profiles;
      const select = $('browser-profile-select'); select.replaceChildren();
      state.browserProfiles.forEach((profile) => select.add(new Option(`${profile.browser} — ${profile.name}`, profile.id)));
      if (state.browserProfiles.some((profile) => profile.id === selected)) select.value = selected;
      if (!state.browserProfiles.length) select.add(new Option('未发现浏览器配置', ''));
      $('browser-profiles-status').textContent = `已检测到 ${state.browserProfiles.length} 个浏览器配置；此步骤只读取目录信息。`;
      $('browser-profiles-empty').hidden = state.browserProfiles.length > 0;
      $('browser-crypto-guidance').hidden = result.capabilities?.chromium_decryption !== false;
      renderLoginWarnings('browser-profiles-warnings', result.warnings);
      updateBrowserProfileDetails();
    } catch (error) {
      state.browserProfiles = []; $('browser-profile-select').replaceChildren(new Option('检测未完成', ''));
      $('browser-profile-details').textContent = ''; $('browser-profiles-empty').hidden = false;
      $('browser-profiles-status').textContent = '无法完成检测，可重新检测或使用扩展导出的登录文件。';
      setError('browser-login-error', `检测失败：${errorMessage(error)}`);
    } finally {state.browserDetecting = false; updateBrowserLoginControls();}
  }
  async function importBrowserLogin(fromFile) {
    if (isLocked() || (!fromFile && state.browserDetecting)) return;
    setError('browser-login-error', ''); $('browser-login-result').hidden = true;
    const providerId = $('browser-login-provider').value;
    const profileId = $('browser-profile-select').value;
    if (!enabledProviders().some((provider) => provider.id === providerId && loginProviders.has(providerId))) {
      setError('browser-login-error', '请先选择已保存并启用的目标模型。'); return;
    }
    if (!fromFile && !state.browserProfiles.some((profile) => profile.id === profileId)) {
      setError('browser-login-error', '请先检测并选择来源浏览器配置。'); return;
    }
    if (!state.status?.bridge_connected) {setError('browser-login-error', '桌面浏览器尚未连接，请等待服务就绪后重试。'); return;}
    state.browserImporting = true; updateControls();
    try {
      await publishLayout({hidden: true});
      const method = fromFile ? 'importLoginFile' : 'importBrowserLogin';
      if (typeof api[method] !== 'function') throw new Error('当前桌面组件不支持登录导入，请完整更新应用并重新启动。');
      const report = await api[method](fromFile ? {provider_id: providerId} : {profile_id: profileId, provider_id: providerId});
      if (fromFile && report?.canceled === true) {toast('已取消选择登录文件'); return;}
      if (report?.ok === false) throw new Error(report.error?.message || '导入未完成，请重试或使用扩展登录文件。');
      if (!report || report.provider_id !== providerId || !['imported', 'skipped', 'storage_imported'].every((key) => Number.isSafeInteger(report[key]) && report[key] >= 0)) {
        throw new Error('导入结果格式异常，无法确认导入数量；请检查模型网页后重试。');
      }
      $('browser-login-summary').textContent = `已导入 ${report.imported} 项 Cookie；请在网页确认登录`;
      $('browser-login-counts').textContent = `目标：${providerName(providerId)} · localStorage：${report.storage_imported} 项 · 跳过：${report.skipped} 项`;
      renderLoginWarnings('browser-login-warnings', report.warnings);
      $('browser-login-result').hidden = false;
    } catch (error) {
      setError('browser-login-error', `导入未完成：${errorMessage(error)}\n数据库或密钥环不可访问时，可使用配套扩展导出登录文件；也可在模型网页直接登录。`);
    } finally {
      await pollStatus(); state.browserImporting = false; updateControls(); scheduleBounds();
    }
  }

  function inputField(label, value, className, placeholder = '') {
    const wrapper = text('label', undefined, 'field'); wrapper.append(text('span', label));
    const input = document.createElement('input'); input.type = 'text'; input.className = className; input.value = value || ''; input.placeholder = placeholder; input.autocomplete = 'off'; input.spellcheck = false; wrapper.append(input); return wrapper;
  }
  function numberField(label, value, className, min, max, help) {
    const wrapper = text('label', undefined, 'field'); wrapper.append(text('span', label));
    const input = document.createElement('input'); input.type = 'number'; input.className = className;
    input.value = String(value ?? 0); input.min = String(min); input.max = String(max); input.step = '0.1';
    wrapper.append(input); if (help) wrapper.append(text('small', help)); return wrapper;
  }
  function renderSettings() {
    const config = state.config; if (!config) return;
    $('provider-settings').replaceChildren();
    config.providers.forEach((provider) => {
      const card = text('div', undefined, 'provider-card'); card.dataset.provider = provider.id;
      const heading = text('div', undefined, 'provider-card-header'); heading.append(text('span', provider.name.slice(0, 1).toUpperCase(), 'provider-initial'), text('strong', provider.name), text('code', provider.id));
      const toggle = text('label', undefined, 'toggle-field'); toggle.append(text('span', '参与回答'));
      const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.className = 'provider-enabled'; enabled.checked = provider.enabled; enabled.setAttribute('aria-label', `启用 ${provider.name}`); toggle.append(enabled); heading.append(toggle); card.append(heading);
      const fields = text('div', undefined, 'field-row'); fields.append(inputField('网页地址', provider.url, 'provider-url', 'https://…'), inputField('独立代理', provider.proxy, 'provider-proxy', '留空使用默认代理')); card.append(fields);
      const speed = text('div', undefined, 'field-row'); speed.append(numberField('最小访问间隔（秒）', provider.access_interval_seconds ?? 0, 'provider-access-interval', 0, 3600, '0 表示不限速；访问过快时等待到间隔满足后再操作网页。')); card.append(speed);
      const details = text('details', undefined, 'selector-details'); details.append(text('summary', '高级：网页 CSS 选择器（JSON）'));
      const label = text('label', undefined, 'field'); label.append(text('span', '保留默认规则；网页结构变化时可在这里调整'));
      const selectors = document.createElement('textarea'); selectors.className = 'provider-selectors'; selectors.value = JSON.stringify(provider.selectors || {}, null, 2); selectors.spellcheck = false; selectors.setAttribute('aria-label', `${provider.name} CSS 选择器 JSON`);
      label.append(selectors, text('small', '字段：input、send、assistant、stop、new_chat，值为 CSS 选择器字符串数组。启用模型必须配置 input 与 assistant；send 未匹配时补充模型控件识别，Qwen 必须识别发送按钮，其他模型可在表单无歧义时使用 Enter；stop 未匹配时依赖稳定时间判断完成。')); details.append(label); card.append(details); $('provider-settings').append(card);
      enabled.addEventListener('change', updateFusionProviderChoices);
    });
    $('fusion-mode').value = config.fusion.mode; updateFusionProviderChoices(config.fusion.provider);
    $('fusion-base-url').value = config.fusion.base_url || ''; $('fusion-api-key').value = config.fusion.api_key || '';
    $('fusion-model').value = config.fusion.model || ''; $('fusion-timeout').value = config.fusion.timeout_seconds;
    $('chat-timeout').value = config.chat?.timeout_seconds ?? 60;
    $('generation-timeout').value = config.generation.timeout_seconds; $('generation-stable').value = config.generation.stable_seconds;
    $('generation-submission-timeout').value = config.generation.submission_timeout_seconds ?? 120;
    $('input-chunk-chars').value = config.generation.input_chunk_chars ?? 4096;
    $('input-chunk-delay').value = config.generation.input_chunk_delay_ms ?? 35;
    $('submit-settle').value = config.generation.submit_settle_seconds ?? 2;
    $('qwen-retry-stages').checked = config.generation.qwen_retry_stages !== false;
    $('qwen-retry-screenshot').checked = config.generation.qwen_retry_screenshot !== false;
    $('qwen-retry-learning').checked = config.generation.qwen_retry_learning !== false;
    $('qwen-retry-trigger-wait').value = config.generation.qwen_retry_trigger_wait_seconds ?? 3;
    $('qwen-manual-retry-wait').value = config.generation.qwen_manual_retry_wait_seconds ?? 20;
    $('generation-recovery-timeout').value = config.generation.recovery_timeout_seconds ?? 180;
    $('generation-min-wait').value = config.generation.min_wait_seconds; $('allow-partial').checked = config.allow_partial;
    updateFusionFields(); state.dirty = false; $('save-settings').classList.remove('dirty-dot'); setError('settings-error', '');
  }
  function updateFusionProviderChoices(preferred) {
    const selected = typeof preferred === 'string' ? preferred : $('fusion-provider').value || state.config?.fusion.provider;
    $('fusion-provider').replaceChildren();
    document.querySelectorAll('.provider-card').forEach((card) => {
      if (card.querySelector('.provider-enabled').checked) $('fusion-provider').add(new Option(providerName(card.dataset.provider), card.dataset.provider));
    });
    if (Array.from($('fusion-provider').options).some((option) => option.value === selected)) $('fusion-provider').value = selected;
  }
  function updateFusionFields() {$('web-fusion-fields').hidden = $('fusion-mode').value !== 'web'; $('api-fusion-fields').hidden = $('fusion-mode').value !== 'api';}
  function setSettingsSection(section) {
    if (state.saving || isImporting()) return;
    state.settingsSection = section;
    document.querySelectorAll('[data-settings-section]').forEach((el) => {el.hidden = el.dataset.settingsSection !== section;});
    document.querySelectorAll('.settings-tab').forEach((el) => {el.classList.toggle('active', el.dataset.section === section); el.setAttribute('aria-current', el.dataset.section === section ? 'page' : 'false');});
    document.querySelector('.settings-save-actions').hidden = ['api', 'browser-login'].includes(section);
    $('settings-save-note').textContent = section === 'api' ? 'API 凭据仅在本机设置页显示。' : section === 'browser-login' ? '手动单向导入，只操作所选模型；不会自动持续同步。' : '修改后保存生效。网页仍使用各自独立的登录会话。';
    document.querySelector('.settings-scroll').scrollTop = 0;
    if (section === 'api') loadRuntimeInfo();
    if (section === 'browser-login') {renderBrowserProviderChoices(); detectBrowserProfiles();}
    updateControls();
  }
  async function openSettings(section = 'models') {
    if (isLocked() || !state.config) return;
    state.settings = true;
    try {await publishLayout({hidden: true});} catch (error) {state.settings = false; toast(`无法打开设置：${errorMessage(error)}`); scheduleBounds(); return;}
    $('workspace').hidden = true; $('settings-view').hidden = false;
    if (!state.dirty) renderSettings();
    setSettingsSection(section);
  }
  function closeSettings() {
    if (state.saving || isImporting()) return;
    // Preserve an unfinished settings draft when the user returns to the conversation.
    state.settings = false; $('settings-view').hidden = true; $('workspace').hidden = false; scheduleBounds(); updateControls();
  }
  function numberValue(id, label, min, max) {
    const raw = $(id).value; const value = Number(raw);
    if (!raw.trim() || !Number.isFinite(value) || value < min) throw new Error(`${label}必须是不小于 ${min} 的有效数字。`);
    if (max !== undefined && value > max) throw new Error(`${label}不能超过 ${max} 秒。`);
    return value;
  }
  function collectConfig() {
    const config = clone(state.config);
    config.providers = config.providers.map((provider) => {
      const card = Array.from(document.querySelectorAll('.provider-card')).find((el) => el.dataset.provider === provider.id);
      let selectors;
      try {selectors = JSON.parse(card.querySelector('.provider-selectors').value || '{}');} catch {throw new Error(`${provider.name} 的选择器不是有效 JSON。`);}
      if (!selectors || Array.isArray(selectors) || typeof selectors !== 'object') throw new Error(`${provider.name} 的选择器必须是 JSON 对象。`);
      for (const [key, values] of Object.entries(selectors)) {
        if (!['input', 'send', 'assistant', 'stop', 'new_chat'].includes(key) || !Array.isArray(values) || values.some((value) => typeof value !== 'string')) throw new Error(`${provider.name} 的选择器字段必须为 input、send、assistant、stop、new_chat，值为字符串数组。`);
        if (values.length > 30 || values.some((value) => !value.trim() || value.length > 1000)) throw new Error(`${provider.name} 的每组选择器最多 30 个，每个为 1–1000 字符的非空字符串。`);
      }
      if (card.querySelector('.provider-enabled').checked && (!selectors.input?.length || !selectors.assistant?.length)) throw new Error(`${provider.name} 需要至少一个 input 和 assistant 选择器。`);
      const url = card.querySelector('.provider-url').value.trim();
      try {const parsed = new URL(url); if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname))) throw new Error();} catch {throw new Error(`${provider.name} 的网页地址必须是 HTTPS URL（本地测试地址可使用 HTTP）。`);}
      const intervalRaw = card.querySelector('.provider-access-interval').value;
      const access_interval_seconds = Number(intervalRaw);
      if (!intervalRaw.trim() || !Number.isFinite(access_interval_seconds) || access_interval_seconds < 0 || access_interval_seconds > 3600) throw new Error(`${provider.name} 的最小访问间隔必须是 0–3600 秒。`);
      return {...provider, enabled: card.querySelector('.provider-enabled').checked, url, proxy: card.querySelector('.provider-proxy').value.trim(), access_interval_seconds, selectors};
    });
    const count = config.providers.filter((p) => p.enabled).length;
    if (count < 1 || count > 5) throw new Error('请启用 1–5 个模型网页。');
    config.fusion = {...config.fusion, mode: $('fusion-mode').value, provider: $('fusion-provider').value || config.fusion.provider,
      base_url: $('fusion-base-url').value.trim(), api_key: $('fusion-api-key').value, model: $('fusion-model').value.trim(),
      timeout_seconds: numberValue('fusion-timeout', '整合超时', 15, 1200)};
    if (config.fusion.mode === 'api' && (!config.fusion.base_url || !config.fusion.model)) throw new Error('使用 API 整合时，请填写 API Base URL 和模型名称。');
    config.chat = {...config.chat, timeout_seconds: numberValue('chat-timeout', '多模型候选回答总等待', 15, 1200)};
    config.generation = {...config.generation,
      qwen_retry_stages: $('qwen-retry-stages').checked,
      qwen_retry_screenshot: $('qwen-retry-screenshot').checked,
      qwen_retry_learning: $('qwen-retry-learning').checked,
      qwen_retry_trigger_wait_seconds: numberValue('qwen-retry-trigger-wait', 'Qwen 重试确认等待', 0.5, 15),
      qwen_manual_retry_wait_seconds: numberValue('qwen-manual-retry-wait', 'Qwen 人工重试等待', 1, 120),
      input_chunk_chars: numberValue('input-chunk-chars', '长输入分段字符数', 256, 16384),
      input_chunk_delay_ms: numberValue('input-chunk-delay', '分段输入间隔', 0, 1000),
      submit_settle_seconds: numberValue('submit-settle', '发送后页面保持时间', 0, 10), recovery_timeout_seconds: numberValue('generation-recovery-timeout', '网页恢复等待时间', 0, 600), timeout_seconds: numberValue('generation-timeout', '生成超时', 15, 1200), submission_timeout_seconds: numberValue('generation-submission-timeout', '接收确认等待时间', 15, 600), stable_seconds: numberValue('generation-stable', '内容稳定时间', 2, 60), min_wait_seconds: numberValue('generation-min-wait', '最短等待时间', 2, 120)};
    const minimumDuration = config.generation.stable_seconds + config.generation.min_wait_seconds;
    if (minimumDuration >= config.generation.timeout_seconds) throw new Error('生成超时必须大于内容稳定时间与最短等待时间之和。');
    if (config.fusion.mode === 'web' && minimumDuration >= config.fusion.timeout_seconds) throw new Error('网页整合超时必须大于内容稳定时间与最短等待时间之和。');
    config.allow_partial = $('allow-partial').checked;
    return config;
  }
  async function saveSettings() {
    if (isLocked()) return;
    setError('settings-error', '');
    let config; try {config = collectConfig();} catch (error) {setError('settings-error', errorMessage(error)); return;}
    state.saving = true; $('settings-form').inert = true; $('save-settings').disabled = true; $('save-settings').textContent = '正在保存…'; $('close-settings').disabled = true; $('discard-settings').disabled = true;
    try {
      state.config = await api.request('PUT', '/internal/config', config); state.dirty = false;
      renderApiModelChoices();
      if (!enabledProviders().some((provider) => provider.id === state.activeProvider)) state.activeProvider = enabledProviders()[0].id;
      reconcileLayout(); renderProviderTabs(); renderLayout(); saveLayoutPreference(); updateProviderStatus(); toast('设置已保存');
      if (!api.setLayout) await api.showProvider(state.activeProvider);
      state.saving = false; closeSettings(); await pollStatus();
    } catch (error) {setError('settings-error', `保存失败：${errorMessage(error)}`);}
    finally {state.saving = false; $('settings-form').inert = false; $('save-settings').disabled = false; $('save-settings').textContent = '保存设置'; $('save-settings').classList.toggle('dirty-dot', state.dirty); $('close-settings').disabled = false; $('discard-settings').disabled = false; updateControls();}
  }
  function shellQuote(value) {return `'${String(value).replace(/'/g, `'"'"'`)}'`;}
  function renderApiModelChoices() {
    const select = $('local-api-model');
    const previous = select.value;
    select.replaceChildren(new Option('多模型整合 · web-fusion', 'web-fusion'));
    enabledProviders().forEach((provider) => select.add(new Option(`${provider.name} · web-${provider.id}`, `web-${provider.id}`)));
    select.value = Array.from(select.options).some((option) => option.value === previous) ? previous : 'web-fusion';
    $('curl-example').textContent = curlExample(false);
  }
  function curlExample(withToken = false) {
    const url = (state.runtime?.baseUrl || 'http://127.0.0.1:8765').replace(/\/$/, '').replace(/\/v1$/, '');
    const selected = $('local-api-model').value;
    const model = enabledProviders().some((provider) => `web-${provider.id}` === selected) ? selected : 'web-fusion';
    const body = JSON.stringify({model, messages: [{role: 'user', content: '请分析 Android 内存泄漏的排查思路'}], stream: false});
    return `curl ${shellQuote(url + '/v1/chat/completions')} \\\n  -H ${shellQuote('Authorization: Bearer ' + (withToken ? state.runtime?.token || 'YOUR_API_KEY' : 'YOUR_API_KEY'))} \\\n  -H 'Content-Type: application/json' \\\n  -d ${shellQuote(body)}`;
  }
  async function loadRuntimeInfo() {
    renderApiModelChoices();
    try {
      state.runtime = await api.runtimeInfo();
      $('local-api-url').value = state.runtime.baseUrl.replace(/\/$/, '').replace(/\/v1$/, '') + '/v1';
      $('local-api-token').value = state.runtime.token; $('local-api-token').type = 'password'; $('reveal-api-token').textContent = '显示';
      $('local-api-directory').textContent = `数据与 Markdown 文件目录：${state.runtime.dataDir}`;
      $('curl-example').textContent = curlExample(false);
    } catch (error) {setError('settings-error', `无法读取 API 信息：${errorMessage(error)}`);}
  }

  $('prompt-form').addEventListener('submit', send);
  $('prompt').addEventListener('input', updateControls);
  $('prompt').addEventListener('keydown', (event) => {if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {event.preventDefault(); $('prompt-form').requestSubmit();}});
  document.querySelectorAll('.starter').forEach((button) => button.addEventListener('click', () => {if (isLocked()) return; $('prompt').value = button.dataset.prompt; $('prompt').focus(); updateControls();}));
  $('new-chat').addEventListener('click', newConversation);
  $('history-select').addEventListener('change', (event) => openConversation(event.target.value));
  $('brand-home').addEventListener('click', (event) => {event.preventDefault(); if (state.settings) closeSettings();});
  $('open-settings').addEventListener('click', () => openSettings('models'));
  $('open-api').addEventListener('click', () => openSettings('api'));
  $('close-settings').addEventListener('click', closeSettings);
  $('discard-settings').addEventListener('click', () => {if (isLocked()) return; renderSettings(); closeSettings();});
  $('save-settings').addEventListener('click', saveSettings);
  $('settings-form').addEventListener('submit', (event) => {event.preventDefault(); saveSettings();});
  $('settings-form').addEventListener('input', (event) => {if (event.target.closest('#browser-login-section') || event.target.id === 'local-api-model' || isLocked()) return; state.dirty = true; $('save-settings').classList.add('dirty-dot');});
  $('detect-browser-profiles').addEventListener('click', detectBrowserProfiles);
  $('browser-profile-select').addEventListener('change', updateBrowserProfileDetails);
  $('browser-login-provider').addEventListener('change', updateBrowserLoginControls);
  $('import-browser-login').addEventListener('click', () => importBrowserLogin(false));
  $('import-login-file').addEventListener('click', () => importBrowserLogin(true));
  $('fusion-mode').addEventListener('change', updateFusionFields);
  document.querySelectorAll('.settings-tab').forEach((button) => button.addEventListener('click', () => setSettingsSection(button.dataset.section)));
  $('dismiss-login-tip').addEventListener('click', () => {$('login-tip').hidden = true; scheduleBounds();});
  $('reload-provider').addEventListener('click', async () => {const providerId = inspectedProvider(); if (isLocked() || !providerId) return; try {await api.reloadProvider(providerId); toast('正在重新加载网页');} catch (error) {toast(errorMessage(error));}});
  $('copy-api-url').addEventListener('click', () => copyText($('local-api-url').value));
  $('copy-api-token').addEventListener('click', () => {if (state.runtime?.token) copyText(state.runtime.token);});
  $('local-api-model').addEventListener('change', () => {$('curl-example').textContent = curlExample(false);});
  $('copy-curl').addEventListener('click', () => {if (state.runtime?.token) copyText(curlExample(true));});
  $('reveal-api-token').addEventListener('click', () => {const visible = $('local-api-token').type === 'password'; $('local-api-token').type = visible ? 'text' : 'password'; $('reveal-api-token').textContent = visible ? '隐藏' : '显示';});
  document.addEventListener('click', (event) => {
    const anchor = event.target.closest?.('.markdown a');
    if (!anchor) return;
    event.preventDefault();
    const href = anchor.getAttribute('href');
    if (href && /^https?:\/\//i.test(href)) api.copyText(href).then(() => toast('链接已复制，可在系统浏览器打开')).catch((error) => toast(errorMessage(error)));
  });
  $('layout-mode').addEventListener('change', () => changeLayout($('layout-mode').value));
  [0, 1].forEach((index) => {
    $(`pane-provider-${index}`).addEventListener('change', () => changeLayout(state.layout.mode, index, $(`pane-provider-${index}`).value));
    $(`pane-provider-${index}`).addEventListener('focus', () => {if (!isLocked()) {state.focusedPane = index; renderLayout(); updateProviderStatus();}});
  });
  $('show-model-windows').addEventListener('click', () => {if (canReopenWindows()) publishLayout({reopen_windows: true}).catch((error) => toast(errorMessage(error)));});
  $('diagnose-send').addEventListener('click', diagnoseSend);
  $('close-send-diagnostic').addEventListener('click', closeDiagnostic);
  $('copy-send-diagnostic').addEventListener('click', () => copyText($('send-diagnostic-json').textContent));
  $('send-diagnostic-dialog').addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {event.preventDefault(); closeDiagnostic();}
    if (event.key === 'Tab') {
      const first = $('close-send-diagnostic'), last = $('copy-send-diagnostic');
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
    }
  });
  setupSplitter('workspace-splitter', 'workspace'); setupSplitter('panes-splitter', 'panes');
  document.addEventListener('pointermove', (event) => {
    if (!state.dragging || event.pointerId !== state.dragging.pointerId) return;
    const kind = state.dragging.kind;
    const rect = $(kind === 'workspace' ? 'workspace' : 'webview-group').getBoundingClientRect();
    const padding = kind === 'workspace' ? 20 : 0;
    resizeShare(kind, (event.clientX - rect.left - padding - 6) / Math.max(1, rect.width - padding * 2 - 12) * 100);
  });
  document.addEventListener('pointerup', finishResize); document.addEventListener('pointercancel', finishResize);
  window.addEventListener('blur', () => finishResize());
  window.addEventListener('resize', () => {resizeShare('workspace', state.layout.chatShare); resizeShare('panes', state.layout.paneShare); scheduleBounds();});
  document.addEventListener('visibilitychange', scheduleBounds);
  for (const id of ['webview-placeholder', 'webview-placeholder-1']) new ResizeObserver(scheduleBounds).observe($(id));

  async function initialize() {
    if (!api) {setError('chat-error', '请通过项目的 run.sh 或 Electron 启动应用；普通浏览器无法连接桌面模型工作区。'); $('connection-status').lastElementChild.textContent = '需要桌面应用'; return;}
    api.onStatus((event) => {
      if (event.type === 'browser-maintenance') {state.browserMaintenance = event.active === true; scheduleBounds();}
      if (event.type === 'provider') {providerStates[event.provider_id] = {state: event.state, message: event.message}; renderProviderTabs(); renderLayout(); updateProviderStatus();}
      if (event.type === 'input-visibility') {state.inputLeaseActive = event.active === true; $('input-visibility-note').hidden = !event.active; $('input-visibility-note').textContent = event.active ? `正在向 ${providerName(event.provider_id)} 发送，确认后恢复布局` : '';}
      if (event.type === 'recovery-provider-opened') {applyRecoveryLayout(event.layout); if (!state.recoveryOpening) scheduleBounds();}
      if (event.type === 'layout-window-closed') toast(`${providerName(event.provider_id)} 窗口已隐藏；可点击“重新显示窗口”恢复。`);
      if (event.type === 'bridge') {if (!state.status) state.status = {}; state.status.bridge_connected = event.state === 'connected' || event.state === 'ready';}
      updateControls();
    });
    try {
      state.config = await api.request('GET', '/internal/config');
      reconcileLayout(); state.focusedPane = Math.max(0, state.layout.providers.indexOf(state.activeProvider)); renderProviderTabs(); renderLayout(); updateProviderStatus();
      if (state.activeProvider) {if (!api.setLayout) await api.showProvider(state.activeProvider); await publishLayout(); saveLayoutPreference();}
      const results = await Promise.allSettled([loadHistory(), pollStatus()]);
      if (results[0].status === 'rejected') toast(`无法加载历史对话：${errorMessage(results[0].reason)}`);
      scheduleBounds(); updateControls();
    } catch (error) {setError('chat-error', `初始化失败：${errorMessage(error)}\n请检查启动终端中的服务日志，然后重新打开应用。`);}
    statusTimer = setInterval(pollStatus, 2000);
    progressTimer = setInterval(() => {if (state.busy) void pollRunProgress();}, 500);
    setInterval(() => {if (state.busy) updateControls();}, 1000);
  }
  window.addEventListener('beforeunload', () => {if (statusTimer) clearInterval(statusTimer); if (progressTimer) clearInterval(progressTimer);});
  initialize();
})();
