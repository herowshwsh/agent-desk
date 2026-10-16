const sessionKey = 'agentdesk.sessions.v3';
const moduleKey = 'agentdesk.active-module.v1';
const appearanceKey = 'agentdesk.appearance.v1';
const modelKey = 'agentdesk.selected-model.v1';
const moduleModelKey = 'agentdesk.module-model.v1';
const workspaceKey = 'agentdesk.workspace.v1';
const workspaceHistoryKey = 'agentdesk.workspace-history.v1';
const SESSION_LIMIT = 8;
const CHAT_HISTORY_LIMIT = 12;

const fallbackModules = [
  {
    id: 'server',
    label: '服务器助手',
    hint: '查询服务器信息与运行状态',
    icon: 'server',
    mode: 'agent',
    show_trace: true,
    show_sessions: true,
    model_locked: false,
    welcome_title: '今天想让 Agent\n帮你完成什么？',
    welcome_subtitle: '连接你的服务器，查看系统状态和运行信息。所有写操作都会先向你确认。',
    placeholder: '查询服务器信息，或描述你要执行的运维任务…',
    prompts: ['查询我的服务器信息', '查询服务器正在运行的服务', '查询 PM2 当前运行的服务', '查看服务器 CPU 和内存使用情况', '查看服务器磁盘空间', '当前服务器运行了多久？'],
  },
  {
    id: 'chat',
    label: '自由聊天',
    hint: '和模型随便聊，不触碰服务器',
    icon: 'chat',
    mode: 'chat',
    show_trace: false,
    show_sessions: true,
    model_locked: false,
    welcome_title: '想到什么\n就说什么。',
    welcome_subtitle: '这里的话会原样转给模型，不会触发任何服务器操作，也不会写入审计记录。',
    placeholder: '随便说点什么…',
    prompts: ['帮我把这段话改得更专业', '解释一下什么是向量数据库', '用一句话解释 CAP 定理', '给我列一个三个月学习计划'],
  },
];

const moduleIcons = {
  server: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v3A2.5 2.5 0 0 1 17.5 11h-11A2.5 2.5 0 0 1 4 8.5v-3Zm2 1.75a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5ZM4 15.5A2.5 2.5 0 0 1 6.5 13h11a2.5 2.5 0 0 1 2.5 2.5v3a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 18.5v-3Zm2 1.75a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z"/>',
  chat: '<path d="M4.75 4.5h14.5A1.75 1.75 0 0 1 21 6.25v8.5a1.75 1.75 0 0 1-1.75 1.75h-6.1l-3.6 3.2a.9.9 0 0 1-1.47-.71V16.5H4.75A1.75 1.75 0 0 1 3 14.75v-8.5A1.75 1.75 0 0 1 4.75 4.5Zm2.5 4a1 1 0 1 0 0 2h10a1 1 0 1 0 0-2h-10Zm0 3.25a1 1 0 1 0 0 2h6.5a1 1 0 1 0 0-2H7.25Z"/>',
  spark: '<path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6-5.6-1.9 5.6-1.9L12 2.5Zm6.5 12l.85 2.15L21.5 17.5l-2.15.85L18.5 20.5l-.85-2.15L15.5 17.5l2.15-.85L18.5 14.5Z"/>',
};

const state = {
  busy: false,
  activeRequest: null,
  activeModuleOfRequest: null,
  cancelledRequests: new Set(),
  activeSessionId: null,
  detachedRequests: new Map(),
  stepCount: 0,
  thinkingMessage: null,
  streamText: '',
  stage: '',
  stageDetail: '',
  stageStartedAt: 0,
  stageTicker: null,
  previewTimer: null,
  modules: structuredClone(fallbackModules),
  activeModuleId: localStorage.getItem(moduleKey) || '',
  defaultModuleId: 'server',
  sessionsByModule: loadSessions(),
  workspaces: loadWorkspaces(),
  workspaceHistory: loadWorkspaceHistory(),
  modelOptions: [],
  defaultModelId: '',
  moduleModels: loadModuleModels(),
  selectedModelId: localStorage.getItem(modelKey) || '',
};

const $ = (selector) => document.querySelector(selector);
const appShell = $('.app-shell');
const moduleTabs = $('#module-tabs');
const welcome = $('#welcome-view');
const conversation = $('#conversation-view');
const conversationScroll = $('#conversation-scroll');
const settingsView = $('#settings-view');
const traceList = $('#trace-list');
const inspector = $('#inspector');
const composer = $('#composer');
const compactComposer = $('#conversation-composer');
const backgroundLayer = $('#background-layer');
const themeSelect = $('#theme-select');
const backgroundFile = $('#background-file');
const backgroundFileName = $('#background-file-name');
const backgroundOpacity = $('#background-opacity');
const backgroundOpacityValue = $('#background-opacity-value');
const settingsStatus = $('#settings-status');
const modelMenu = $('#model-menu');
const promptChips = $('#prompt-chips');
const welcomeTitle = $('#welcome-title');
const sessionSection = $('#session-section');
const clearConversation = $('#clear-conversation');
const workspaceMenu = $('#workspace-menu');
const workspaceControls = [
  { picker: $('#workspace-picker'), button: $('#workspace-button'), label: $('#workspace-label') },
  { picker: $('#workspace-picker-2'), button: $('#workspace-button-2'), label: $('#workspace-label-2') },
].filter((entry) => entry.picker && entry.button);
let backgroundDataUrl = '';

function loadSessions() {
  try {
    const saved = JSON.parse(localStorage.getItem(sessionKey) || 'null');
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {};
    const clean = {};
    Object.entries(saved).forEach(([moduleId, list]) => {
      if (!Array.isArray(list)) return;
      clean[moduleId] = list
        .filter((session) => session && typeof session.id === 'string')
        .map((session) => ({
          id: session.id,
          title: String(session.title || '未命名任务'),
          time: String(session.time || ''),
          messages: Array.isArray(session.messages)
            ? session.messages.filter((message) => message && typeof message.text === 'string').map((message) => ({ role: message.role === 'assistant' ? 'assistant' : 'user', text: message.text }))
            : [],
        }));
    });
    return clean;
  } catch {
    return {};
  }
}

function saveSessions() {
  try {
    localStorage.setItem(sessionKey, JSON.stringify(state.sessionsByModule));
  } catch {
    settingsStatus.textContent = '本地存储不可用，本次的会话记录不会被保存。';
  }
}

function loadModuleModels() {
  try {
    const saved = JSON.parse(localStorage.getItem(moduleModelKey) || '{}');
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
  } catch {
    return {};
  }
}

function saveModuleModels() {
  try {
    localStorage.setItem(moduleModelKey, JSON.stringify(state.moduleModels));
  } catch {
    /* 本地存储不可用时保持内存态 */
  }
}

function loadWorkspaces() {
  try {
    const saved = JSON.parse(localStorage.getItem(workspaceKey) || '{}');
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
  } catch {
    return {};
  }
}

function saveWorkspaces() {
  try {
    localStorage.setItem(workspaceKey, JSON.stringify(state.workspaces));
  } catch {
    /* 保持内存态 */
  }
}

function loadWorkspaceHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(workspaceHistoryKey) || '[]');
    return Array.isArray(saved) ? saved.filter((item) => typeof item === 'string' && item.trim()).slice(0, 6) : [];
  } catch {
    return [];
  }
}

function saveWorkspaceHistory() {
  try {
    localStorage.setItem(workspaceHistoryKey, JSON.stringify(state.workspaceHistory));
  } catch {
    /* 保持内存态 */
  }
}

function workspaceName(path) {
  return String(path || '').split('/').filter(Boolean).pop() || '工作空间';
}

function moduleWorkspace(moduleId) {
  return state.workspaces[moduleId] || '';
}

function loadAppearance() {
  try {
    const saved = JSON.parse(localStorage.getItem(appearanceKey) || '{}');
    // 兼容旧版本：旧图片曾经保存在 localStorage，启动时先在内存中使用，随后迁移到文件。
    return { ...saved, legacyImage: saved.image || '', hasImage: Boolean(saved.hasImage || saved.image) };
  } catch {
    return {};
  }
}

function saveAppearance(appearance) {
  localStorage.setItem(appearanceKey, JSON.stringify(appearance));
}

function normalizeTheme(theme) {
  // 旧版主题（default/ice/mist 均为浅色）迁移到新命名；dark 为默认。
  if (theme === 'light' || theme === 'ice' || theme === 'mist') return 'light';
  return 'dark';
}

function applyAppearance(appearance) {
  document.body.classList.remove('theme-light');
  const theme = normalizeTheme(appearance.theme);
  if (theme === 'light') document.body.classList.add('theme-light');
  if (appearance.hasImage && backgroundDataUrl) {
    document.body.classList.add('has-custom-background');
    backgroundLayer.style.backgroundImage = `url("${backgroundDataUrl}")`;
    backgroundLayer.style.opacity = String(Number(appearance.opacity ?? 55) / 100);
    backgroundFileName.textContent = appearance.fileName || '已使用本地背景图片';
  } else {
    document.body.classList.remove('has-custom-background');
    backgroundLayer.style.backgroundImage = 'none';
    backgroundLayer.style.opacity = '0';
    backgroundFileName.textContent = '尚未选择背景图片';
  }
  themeSelect.value = normalizeTheme(appearance.theme);
  backgroundOpacity.value = String(appearance.opacity ?? 55);
  backgroundOpacityValue.textContent = `${backgroundOpacity.value}%`;
}

function updateAppearance(patch) {
  const next = { ...loadAppearance(), ...patch };
  delete next.image;
  delete next.legacyImage;
  saveAppearance(next);
  applyAppearance(next);
  settingsStatus.textContent = '已保存到本机。';
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]));
}

function formatAssistantText(value) {
  const lines = String(value).replace('[写入提案]', '').trim().split('\n');
  const parts = [];
  let index = 0;
  while (index < lines.length) {
    const header = lines[index];
    const divider = lines[index + 1] || '';
    if (header.includes('|') && /^[|:\-\s]+$/.test(divider)) {
      const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => escapeHtml(cell.trim()));
      const headings = cells(header);
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(cells(lines[index]));
        index += 1;
      }
      parts.push(`<div class="result-table"><table><thead><tr>${headings.map((cell) => `<th>${cell}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    parts.push(escapeHtml(header).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'));
    index += 1;
  }
  return parts.join('<br />');
}

function activeModule() {
  return state.modules.find((module) => module.id === state.activeModuleId) || state.modules[0];
}

function moduleSessions(moduleId) {
  if (!state.sessionsByModule[moduleId]) state.sessionsByModule[moduleId] = [];
  return state.sessionsByModule[moduleId];
}

function moduleModelId(module) {
  if (!module || module.model_locked) return state.defaultModelId;
  const saved = state.moduleModels[module.id];
  return state.modelOptions.some((model) => model.id === saved) ? saved : state.defaultModelId;
}

function renderModuleTabs() {
  moduleTabs.innerHTML = '';
  state.modules.forEach((module) => {
    const tab = document.createElement('button');
    const selected = module.id === state.activeModuleId;
    tab.type = 'button';
    tab.className = `module-tab${selected ? ' selected' : ''}`;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', String(selected));
    tab.dataset.moduleId = module.id;
    tab.title = module.hint || module.label;
    const paths = moduleIcons[module.icon] || moduleIcons.chat;
    tab.innerHTML = `<svg class="tab-icon" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg><span class="tab-label">${escapeHtml(module.label)}</span>`;
    tab.addEventListener('click', () => switchModule(module.id));
    moduleTabs.appendChild(tab);
  });
}

function applyModuleConfig(config) {
  if (Array.isArray(config?.models)) state.modelOptions = config.models.filter((model) => model?.id && model?.label);
  if (!state.modelOptions.length && config?.model_id && config?.model) {
    state.modelOptions = [{ id: config.model_id, label: config.model, model: config.model_id }];
  }
  state.defaultModelId = config?.model_id || state.modelOptions[0]?.id || '';
  if (!state.selectedModelId || !state.modelOptions.some((model) => model.id === state.selectedModelId)) {
    state.selectedModelId = state.defaultModelId;
  }
  localStorage.setItem(modelKey, state.selectedModelId);

  if (Array.isArray(config?.modules) && config.modules.length) {
    state.modules = config.modules.filter((module) => module?.id && module?.label);
  }
  state.defaultModuleId = config?.default_module_id || state.modules[0]?.id || '';
  if (!state.modules.some((module) => module.id === state.activeModuleId)) {
    const saved = localStorage.getItem(moduleKey);
    state.activeModuleId = state.modules.some((module) => module.id === saved) ? saved : state.defaultModuleId;
  }
  renderModuleTabs();
  applyModule();
}

function applyModule() {
  const module = activeModule();
  if (!module) return;
  const placeholder = module.placeholder || '输入你的消息…';
  document.title = `${module.label} · Agent`;
  document.querySelector('[data-module-label]').textContent = module.label;
  document.querySelectorAll('[data-module-hint]').forEach((element) => { element.textContent = module.hint || '智能工作台'; });
  document.querySelector('[data-module-subtitle]').textContent = module.welcome_subtitle || '';
  composer.placeholder = placeholder;
  compactComposer.placeholder = module.mode === 'chat' ? placeholder : `继续描述你的任务…`;
  const title = String(module.welcome_title || '');
  welcomeTitle.innerHTML = title
    ? title.split('\n').map((line, index) => (index === 0 ? escapeHtml(line) : `<em>${escapeHtml(line)}</em>`)).join('<br />')
    : '';

  const eyebrow = module.mode === 'chat' ? 'FREE CHAT' : 'AGENTDESK WORKSPACE';
  const eyebrowElement = document.querySelector('[data-module-eyebrow]');
  if (eyebrowElement) eyebrowElement.textContent = eyebrow;

  promptChips.innerHTML = '';
  (module.prompts || []).forEach((prompt) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.prompt = prompt;
    button.textContent = prompt;
    button.addEventListener('click', () => {
      composer.value = prompt;
      composer.focus();
    });
    promptChips.appendChild(button);
  });

  sessionSection.classList.toggle('hidden', !module.show_sessions);
  const showInspector = Boolean(module.show_trace);
  inspector.classList.toggle('hidden', !showInspector);
  appShell.classList.toggle('no-inspector', !showInspector);

  applyWorkspaceControls();
  applyModelLabels();
  resetTrace();
  renderSessionList();
  // 服务器选择器只在「服务器助手」模块显示：用根容器标记 + CSS 控制，避免时序问题
  appShell.classList.toggle('server-module', module.id === 'server');
  showWelcome();
  renderServerPicker();
}

function applyWorkspaceControls() {
  const module = activeModule();
  const show = Boolean(module?.allow_files);
  const current = moduleWorkspace(module?.id);
  workspaceControls.forEach(({ picker, button, label }) => {
    picker.classList.toggle('hidden', !show);
    button.classList.toggle('active', Boolean(current));
    label.textContent = current ? workspaceName(current) : '工作空间';
    button.title = current ? `当前工作空间：${current}` : '选择一个文件夹作为工作空间，模型可以读取里面的文件';
  });
}

function openWorkspaceMenu(anchor) {
  const module = activeModule();
  if (!module?.allow_files) return;
  const current = moduleWorkspace(module.id);
  workspaceMenu.innerHTML = '';
  if (!state.workspaceHistory.length) {
    const hint = document.createElement('div');
    hint.className = 'workspace-option';
    hint.style.cursor = 'default';
    hint.innerHTML = '<span style="color: var(--text-3)">还没有选择过工作空间</span>';
    workspaceMenu.appendChild(hint);
  }
  state.workspaceHistory.forEach((path) => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = `workspace-option${path === current ? ' selected' : ''}`;
    const name = workspaceName(path);
    option.innerHTML = `<span>${escapeHtml(name)}</span><small title="${escapeHtml(path)}">${escapeHtml(path)}</small>`;
    option.addEventListener('click', () => {
      state.workspaces[module.id] = path;
      saveWorkspaces();
      applyWorkspaceControls();
      closeWorkspaceMenu();
    });
    workspaceMenu.appendChild(option);
  });
  const browse = document.createElement('button');
  browse.type = 'button';
  browse.className = 'workspace-option';
  browse.innerHTML = '<span>浏览文件夹…</span>';
  browse.addEventListener('click', async () => {
    closeWorkspaceMenu();
    const result = await window.agentDesk?.pickFiles?.({ directory: true });
    if (!result?.ok || !result.paths?.length) return;
    const path = result.paths[0];
    state.workspaces[module.id] = path;
    state.workspaceHistory = [path, ...state.workspaceHistory.filter((item) => item !== path)].slice(0, 6);
    saveWorkspaces();
    saveWorkspaceHistory();
    applyWorkspaceControls();
  });
  workspaceMenu.appendChild(browse);
  if (current) {
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'workspace-clear';
    clear.textContent = '清除当前工作空间';
    clear.addEventListener('click', () => {
      delete state.workspaces[module.id];
      saveWorkspaces();
      applyWorkspaceControls();
      closeWorkspaceMenu();
    });
    workspaceMenu.appendChild(clear);
  }
  workspaceMenu.classList.remove('hidden');
  const anchorRect = anchor.getBoundingClientRect();
  const menuRect = workspaceMenu.getBoundingClientRect();
  const left = Math.min(Math.max(10, anchorRect.left), window.innerWidth - menuRect.width - 10);
  const above = anchorRect.top - menuRect.height - 8;
  workspaceMenu.style.left = `${left}px`;
  workspaceMenu.style.top = `${above >= 10 ? above : anchorRect.bottom + 8}px`;
}

function closeWorkspaceMenu() {
  workspaceMenu.classList.add('hidden');
  workspaceControls.forEach(({ button }) => button.setAttribute('aria-expanded', 'false'));
}

function switchModule(moduleId) {
  if (moduleId === state.activeModuleId || !state.modules.some((module) => module.id === moduleId)) return;
  const previousModuleId = state.activeModuleId;
  if (state.busy) cancelCurrentRequest({ silent: true });
  state.activeModuleId = moduleId;
  localStorage.setItem(moduleKey, moduleId);
  state.activeSessionId = null;
  conversationScroll.innerHTML = '';
  closeModelMenu();
  closeWorkspaceMenu();
  renderModuleTabs();
  renderServerPicker();
  applyModule();
}

function showWelcome() {
  welcome.classList.remove('hidden');
  conversation.classList.add('hidden');
  settingsView.classList.add('hidden');
  $('#page-title').textContent = '新任务';
  $('#workbench-nav').classList.add('selected');
  $('#settings-nav').classList.remove('selected');
  clearConversation.classList.add('hidden');
}

function showConversation() {
  welcome.classList.add('hidden');
  conversation.classList.remove('hidden');
  settingsView.classList.add('hidden');
  $('#workbench-nav').classList.add('selected');
  $('#settings-nav').classList.remove('selected');
  renderServerPicker();
}

function applyModelLabels() {
  const module = activeModule();
  const modelId = moduleModelId(module);
  const selected = state.modelOptions.find((model) => model.id === modelId);
  const modelLabel = String(selected?.label || '').trim();
  if (!modelLabel) return;
  document.querySelectorAll('[data-model-name]').forEach((element) => { element.textContent = modelLabel; });
  const status = document.querySelector('[data-model-status]');
  if (status) status.textContent = `${modelLabel} 已连接`;
  const mode = document.querySelector('[data-module-mode]');
  if (mode) mode.textContent = '模型服务已连接';
  document.querySelectorAll('[data-model-picker]').forEach((button) => {
    button.classList.toggle('locked', Boolean(module?.model_locked));
    button.disabled = Boolean(module?.model_locked);
  });
}

function closeModelMenu() {
  modelMenu.classList.add('hidden');
  document.querySelectorAll('[data-model-picker]').forEach((button) => button.setAttribute('aria-expanded', 'false'));
}

function openModelMenu(anchor) {
  const module = activeModule();
  if (!state.modelOptions.length || module?.model_locked) return;
  const currentModelId = moduleModelId(module);
  modelMenu.innerHTML = '';
  state.modelOptions.forEach((model) => {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = `model-option${model.id === currentModelId ? ' selected' : ''}`;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(model.id === currentModelId));
    option.innerHTML = `<span>${escapeHtml(model.label)}</span><small>${escapeHtml(model.model || model.id)}</small>`;
    option.addEventListener('click', () => {
      state.moduleModels[module.id] = model.id;
      saveModuleModels();
      applyModelLabels();
      closeModelMenu();
    });
    modelMenu.appendChild(option);
  });
  modelMenu.classList.remove('hidden');
  const anchorRect = anchor.getBoundingClientRect();
  const menuRect = modelMenu.getBoundingClientRect();
  const left = Math.min(Math.max(10, anchorRect.right - menuRect.width), window.innerWidth - menuRect.width - 10);
  const above = anchorRect.top - menuRect.height - 8;
  modelMenu.style.left = `${left}px`;
  modelMenu.style.top = `${above >= 10 ? above : anchorRect.bottom + 8}px`;
  document.querySelectorAll('[data-model-picker]').forEach((button) => button.setAttribute('aria-expanded', String(button === anchor)));
}

const FAILURE_PATTERN = /^(请求失败|工作流失败|无法连接|执行失败|等待模型响应超过)/;

// 两步写入：模型回复以 [写入提案] 开头时，在气泡下挂确认按钮；
// 用户点确认才发送确认消息触发模型生成并写入。
function attachProposalActions(messageEl, text) {
  if (!String(text).includes('[写入提案]')) return;
  const content = messageEl.querySelector('.message-content');
  if (!content || content.querySelector('.proposal-actions')) return;
  const bar = document.createElement('div');
  bar.className = 'proposal-actions';
  bar.innerHTML = '<button class="proposal-approve">确认写入</button><button class="proposal-reject">不允许</button>';
  content.appendChild(bar);
  bar.querySelector('.proposal-approve').addEventListener('click', () => {
    if (state.busy) return;
    bar.innerHTML = '<span class="proposal-note">已确认，模型开始生成并写入…</span>';
    submitQuestion('[确认写入] 同意该提案，请生成内容并写入文件');
  });
  bar.querySelector('.proposal-reject').addEventListener('click', () => {
    if (state.busy) return;
    bar.innerHTML = '<span class="proposal-note">已拒绝，本次不写入</span>';
    submitQuestion('[拒绝写入] 我不同意这个提案，请根据你的理解继续回答');
  });
}

function appendMessage(role, text, tags) {
  const message = document.createElement('article');
  const isFailure = role === 'assistant' && FAILURE_PATTERN.test(String(text).trim());
  message.className = `message ${role}${isFailure ? ' error' : ''}`;
  const content = role === 'assistant' ? formatAssistantText(text) : escapeHtml(text);
  const tagBar = Array.isArray(tags) && tags.length
    ? `<div class="message-attachments">${tags.map((tag) => `<span class="attachment-tag">${escapeHtml(tag)}</span>`).join('')}</div>`
    : '';
  const retryBar = isFailure ? '<div class="retry-bar"><button type="button" class="retry-button">重试</button></div>' : '';
  message.innerHTML = `<div class="message-content">${tagBar}<div class="message-bubble">${content}</div>${retryBar}</div>`;
  if (isFailure) {
    message.querySelector('.retry-button').addEventListener('click', () => retryFrom(message, text));
  }
  if (role === 'assistant') attachProposalActions(message, text);
  conversationScroll.appendChild(message);
  conversationScroll.scrollTop = conversationScroll.scrollHeight;
}

// 失败一键重发：移除失败的回答与对应提问，重新走一遍提交。
function retryFrom(failureNode, failureText) {
  if (state.busy) return;
  const module = activeModule();
  const session = moduleSessions(module.id).find((item) => item.id === state.activeSessionId);
  if (!session) return;
  let index = session.messages.length - 1;
  while (index >= 0 && session.messages[index].role !== 'user') index -= 1;
  const userMessage = session.messages[index];
  if (!userMessage) return;
  session.messages.splice(index);
  saveSessions();
  failureNode.remove();
  submitQuestion(userMessage.text);
}

function showThinkingMessage() {
  const message = document.createElement('article');
  message.className = 'message assistant thinking';
  message.innerHTML = '<div class="message-content"><div class="message-bubble"><span class="thinking-label">正在思考</span><span class="thinking-dots" aria-label="思考中"><i></i><i></i><i></i></span></div></div>';
  conversationScroll.appendChild(message);
  conversationScroll.scrollTop = conversationScroll.scrollHeight;
  state.thinkingMessage = message;
}

function renderSessionList() {
  const list = $('#session-list');
  list.innerHTML = '';
  const sessions = moduleSessions(state.activeModuleId);
  sessions.slice(0, SESSION_LIMIT).forEach((session) => {
    const row = document.createElement('div');
    row.className = 'session-row';
    const item = document.createElement('button');
    item.className = 'session-item';
    item.dataset.sessionId = session.id;
    item.innerHTML = `<span>${escapeHtml(session.title)}</span><time>${escapeHtml(session.time || '')}</time>`;
    item.addEventListener('click', () => openSession(session.id));
    const remove = document.createElement('button');
    remove.className = 'session-delete';
    remove.type = 'button';
    remove.dataset.sessionId = session.id;
    remove.setAttribute('aria-label', `删除任务：${session.title}`);
    remove.textContent = '×';
    remove.addEventListener('click', (event) => {
      event.stopPropagation();
      if (state.busy || !window.confirm(`确定删除“${session.title}”吗？`)) return;
      const deletingActive = state.activeSessionId === session.id;
      state.sessionsByModule[state.activeModuleId] = sessions.filter((itemToKeep) => itemToKeep.id !== session.id);
      saveSessions();
      renderSessionList();
      if (deletingActive) goHome();
    });
    row.append(item, remove);
    list.appendChild(row);
  });
  const count = document.querySelector('.section-count');
  if (count) count.textContent = String(sessions.length);
}

function resetTrace() {
  traceList.innerHTML = '<div class="trace-empty"><div class="trace-orbit">✦</div><strong>等待你的任务</strong><span>Agent 开始工作后，工具调用和审批会显示在这里。</span></div>';
  state.stepCount = 0;
}

function goHome() {
  if (state.busy) return;
  state.activeSessionId = null;
  conversationScroll.innerHTML = '';
  resetTrace();
  showWelcome();
  document.querySelectorAll('.session-item').forEach((item) => item.classList.remove('active'));
}

function showSettings() {
  if (state.busy) return;
  welcome.classList.add('hidden');
  conversation.classList.add('hidden');
  settingsView.classList.remove('hidden');
  $('#page-title').textContent = '设置';
  $('#workbench-nav').classList.remove('selected');
  $('#settings-nav').classList.add('selected');
  clearConversation.classList.add('hidden');
  if (window.agentDesk.getModels) window.agentDesk.getModels();
  if (window.agentDesk.getServers) window.agentDesk.getServers();
}

// ---- 设置页：模型管理 ----
let modelsDraft = [];
let modelsDefault = '';
let modelsEditingIndex = -1;  // 当前展开编辑表单的行；-1 表示纯列表

function modelFormHtml(model, index) {
  return `
    <div class="model-admin-grid">
      <label>显示名称<input data-field="label" value="${escapeHtml(model.label || '')}" placeholder="DeepSeek" /></label>
      <label>模型名称<input data-field="model" value="${escapeHtml(model.model || '')}" placeholder="deepseek-chat" /></label>
      <label>接口地址<input data-field="base_url" value="${escapeHtml(model.base_url || '')}" placeholder="https://api.deepseek.com" /></label>
      <label>密钥值（留空不修改）<input data-field="key_value" type="password" placeholder="${escapeHtml(model.key_masked || '未配置')}" /></label>
      <label>协议
        <select data-field="wire_api">
          <option value="chat"${model.wire_api !== 'responses' ? ' selected' : ''}>chat</option>
          <option value="responses"${model.wire_api === 'responses' ? ' selected' : ''}>responses</option>
        </select>
      </label>
      <label>推理强度
        <select data-field="reasoning_effort">
          <option value=""${!model.reasoning_effort ? ' selected' : ''}>默认</option>
          <option value="low"${model.reasoning_effort === 'low' ? ' selected' : ''}>low</option>
          <option value="medium"${model.reasoning_effort === 'medium' ? ' selected' : ''}>medium</option>
          <option value="high"${model.reasoning_effort === 'high' ? ' selected' : ''}>high</option>
        </select>
      </label>
      <label class="model-admin-check"><input data-field="enabled" type="checkbox"${model.enabled !== false ? ' checked' : ''} /> 启用</label>
      <label class="model-admin-check"><input data-field="is_default" type="checkbox"${modelsDefault === model.id ? ' checked' : ''} /> 默认模型</label>
    </div>
    <div class="model-form-actions"><button class="model-collapse" type="button">收起</button></div>`;
}

function renderModelsAdmin() {
  const list = $('#models-admin-list');
  if (!list) return;
  list.innerHTML = modelsDraft.map((model, index) => {
    const editing = index === modelsEditingIndex;
    const defaultBadge = modelsDefault === model.id ? '<span class="model-badge model-badge-default">默认</span>' : '';
    const enabledBadge = model.enabled === false ? '<span class="model-badge">已停用</span>' : '';
    if (editing) {
      return `
        <div class="model-admin-item editing" data-index="${index}">
          <div class="model-row-head"><strong>${escapeHtml(model.label || '未命名模型')}</strong>${defaultBadge}${enabledBadge}</div>
          ${modelFormHtml(model, index)}
          <button class="model-remove" type="button" data-remove="${index}">删除该模型</button>
        </div>`;
    }
    return `
      <div class="model-admin-item" data-index="${index}">
        <div class="model-row-head">
          <strong>${escapeHtml(model.label || '未命名模型')}</strong>
          <code class="model-row-model">${escapeHtml(model.model || '')}</code>
          <span class="model-badge">${escapeHtml(model.wire_api === 'responses' ? 'responses' : 'chat')}</span>
          ${defaultBadge}${enabledBadge}
          <span class="model-row-key" title="密钥状态">${escapeHtml(model.key_masked || '未配置密钥')}</span>
        </div>
        <div class="model-row-actions">
          <button class="model-edit" type="button">编辑</button>
          <button class="model-remove" type="button" data-remove="${index}">删除</button>
        </div>
      </div>`;
  }).join('') || '<div class="settings-file-name">还没有模型，点击"添加模型"。</div>';

  list.querySelectorAll('.model-admin-item').forEach((item) => {
    const index = Number(item.dataset.index);
    item.querySelectorAll('input, select').forEach((input) => {
      const field = input.dataset.field;
      if (!field) return;
      input.addEventListener('change', () => updateModelDraft(index, field, input));
      input.addEventListener('input', () => updateModelDraft(index, field, input));
    });
    const remove = item.querySelector('.model-remove');
    if (remove) remove.addEventListener('click', () => {
      modelsDraft.splice(index, 1);
      if (modelsEditingIndex === index) modelsEditingIndex = -1;
      else if (modelsEditingIndex > index) modelsEditingIndex -= 1;
      renderModelsAdmin();
    });
    const edit = item.querySelector('.model-edit');
    if (edit) edit.addEventListener('click', () => {
      modelsEditingIndex = index;
      renderModelsAdmin();
    });
    const collapse = item.querySelector('.model-collapse');
    if (collapse) collapse.addEventListener('click', () => {
      modelsEditingIndex = -1;
      renderModelsAdmin();
    });
  });
}

function updateModelDraft(index, field, input) {
  const model = modelsDraft[index];
  if (!model) return;
  if (field === 'enabled') {
    model.enabled = input.checked;
  } else if (field === 'is_default') {
    if (input.checked) {
      modelsDefault = model.id;
      modelsDraft.forEach((item) => { if (item.id !== model.id) item.is_default = false; });
    } else {
      modelsDefault = '';
    }
    renderModelsAdmin();
    return;
  } else if (field === 'key_value') {
    model._key_value = input.value;
    return;
  }
  model[field] = input.value;
}

function handleModelsConfig(event) {
  modelsDraft = Array.isArray(event.models) ? event.models.map((model) => ({ ...model })) : [];
  modelsDefault = String(event.default || '');
  renderModelsAdmin();
  const note = $('#models-admin-note');
  if (note) note.textContent = `模型配置已读取（密钥文件：${event.env_path || '本机 .env'}）。修改后点"保存全部"。`;
}

function saveModelsDraft() {
  const note = $('#models-admin-note');
  if (note) note.textContent = '正在保存…';
  window.agentDesk.saveModels({
    models: modelsDraft.map((model) => {
      const entry = { ...model };
      delete entry.key_masked;  // 展示字段不回传
      const key = String(model._key_value || '').trim();
      if (key) {
        entry._key_value = key;
      } else {
        delete entry._key_value;
      }
      return entry;
    }),
    default: modelsDefault,
  });
}

function handleModelsSaved(event) {
  const note = $('#models-admin-note');
  if (note) note.textContent = `${event.message || ''}保存成功，已生效。`.trim();
}

function handleModelsError(event) {
  const note = $('#models-admin-note');
  if (note) note.textContent = `保存失败：${event.message || '未知错误'}`;
}

// ---- 服务器选择器（server 模块 composer）----
const serverSelectionsKey = 'agentdesk.serverSelections.v1';
let availableServers = [];

function serverSelections() {
  try {
    return JSON.parse(localStorage.getItem(serverSelectionsKey) || '{}');
  } catch {
    return {};
  }
}

function selectedServerId(moduleId) {
  return serverSelections()[moduleId] || '';
}

function rememberServerSelection(moduleId, serverId) {
  const all = serverSelections();
  all[moduleId] = serverId;
  localStorage.setItem(serverSelectionsKey, JSON.stringify(all));
}

function renderServerPicker() {
  const module = activeModule();
  // 仅「服务器助手」模块显示服务器选择器（按 id 精确匹配，其他 agent 模块不显示）
  const isServerModule = Boolean(module && module.id === 'server');
  document.querySelectorAll('[data-server-picker]').forEach((picker) => {
    if (!isServerModule || availableServers.length === 0) return;
    const remembered = selectedServerId(module.id);
    const current = availableServers.some((server) => server.id === remembered) ? remembered : (availableServers[0]?.id || '');
    if (current !== remembered && current) rememberServerSelection(module.id, current);
    picker.innerHTML = availableServers.map((server) =>
      `<option value="${escapeHtml(server.id)}"${server.id === current ? ' selected' : ''}>${escapeHtml(server.label)}</option>`
    ).join('');
  });
}

// ---- 设置页：服务器管理（与模型管理同构：列表 + 点编辑展开表单）----
let serversDraft = [];
let serversDefault = '';
let serversEditingIndex = -1;

function serverFormHtml(server) {
  return `
    <div class="model-admin-grid">
      <label>显示名称<input data-field="label" value="${escapeHtml(server.label || '')}" placeholder="生产服务器" /></label>
      <label>服务器地址<input data-field="host" value="${escapeHtml(server.host || '')}" placeholder="192.168.1.100" /></label>
      <label>端口<input data-field="port" value="${escapeHtml(String(server.port ?? 22))}" placeholder="22" /></label>
      <label>登录用户<input data-field="username" value="${escapeHtml(server.username || '')}" placeholder="root" /></label>
      <label>密码（留空不修改）<input data-field="_password_draft" type="password" placeholder="${server.has_password ? '已设置' : '未设置'}" /></label>
      <label>连接超时（秒）<input data-field="connect_timeout" value="${escapeHtml(String(server.connect_timeout ?? 10))}" placeholder="10" /></label>
      <label class="model-admin-check"><input data-field="is_default" type="checkbox"${serversDefault === server.id ? ' checked' : ''} /> 默认服务器</label>
    </div>
    <div class="model-form-actions">
      <button class="server-test" type="button">测试连接</button>
      <button class="model-collapse" type="button">收起</button>
    </div>`;
}

function renderServersAdmin() {
  const list = $('#servers-admin-list');
  if (!list) return;
  list.innerHTML = serversDraft.map((server, index) => {
    const editing = index === serversEditingIndex;
    const defaultBadge = serversDefault === server.id ? '<span class="model-badge model-badge-default">默认</span>' : '';
    if (editing) {
      return `
        <div class="model-admin-item editing" data-index="${index}">
          <div class="model-row-head"><strong>${escapeHtml(server.label || '未命名服务器')}</strong>${defaultBadge}</div>
          ${serverFormHtml(server)}
          <button class="model-remove" type="button" data-remove="${index}">删除该服务器</button>
        </div>`;
    }
    return `
      <div class="model-admin-item" data-index="${index}">
        <div class="model-row-head">
          <strong>${escapeHtml(server.label || '未命名服务器')}</strong>
          <code class="model-row-model">${escapeHtml(server.host || '未填写地址')}</code>
          ${defaultBadge}
          <span class="model-row-key">${escapeHtml(String(server.port ?? 22))} · ${escapeHtml(server.username || 'root')}</span>
        </div>
        <div class="model-row-actions">
          <button class="server-edit" type="button">编辑</button>
          <button class="model-remove" type="button" data-remove="${index}">删除</button>
        </div>
      </div>`;
  }).join('') || '<div class="settings-file-name">还没有服务器，点击"添加服务器"。</div>';

  list.querySelectorAll('.model-admin-item').forEach((item) => {
    const index = Number(item.dataset.index);
    item.querySelectorAll('input, select').forEach((input) => {
      const field = input.dataset.field;
      if (!field) return;
      input.addEventListener('change', () => updateServerDraft(index, field, input));
      input.addEventListener('input', () => updateServerDraft(index, field, input));
    });
    const remove = item.querySelector('.model-remove');
    if (remove) remove.addEventListener('click', () => {
      serversDraft.splice(index, 1);
      if (serversEditingIndex === index) serversEditingIndex = -1;
      else if (serversEditingIndex > index) serversEditingIndex -= 1;
      renderServersAdmin();
    });
    const edit = item.querySelector('.server-edit');
    if (edit) edit.addEventListener('click', () => {
      serversEditingIndex = index;
      renderServersAdmin();
    });
    const collapse = item.querySelector('.model-collapse');
    if (collapse) collapse.addEventListener('click', () => {
      serversEditingIndex = -1;
      renderServersAdmin();
    });
    const test = item.querySelector('.server-test');
    if (test) test.addEventListener('click', () => {
      test.textContent = '测试中…';
      window.agentDesk.testServer({ id: serversDraft[index].id, servers: serversDraft });
    });
  });
}

function updateServerDraft(index, field, input) {
  const server = serversDraft[index];
  if (!server) return;
  if (field === 'is_default') {
    if (input.checked) {
      serversDefault = server.id;
    } else {
      serversDefault = '';
    }
    renderServersAdmin();
    return;
  } else if (field === '_password_draft') {
    server._password_draft = input.value;
    return;
  } else if (field === 'port' || field === 'connect_timeout') {
    server[field] = input.value;
    return;
  }
  server[field] = input.value;
}

function handleServersConfig(event) {
  serversDraft = Array.isArray(event.servers) ? event.servers.map((server) => ({ ...server })) : [];
  serversDefault = String(event.default || '');
  renderServersAdmin();
  const note = $('#servers-admin-note');
  if (note) note.textContent = `服务器配置已读取（${event.servers_path || '本机 servers.json'}）。修改后点"保存全部"。`;
}

function saveServersDraft() {
  const note = $('#servers-admin-note');
  if (note) note.textContent = '正在保存…';
  window.agentDesk.saveServers({
    servers: serversDraft.map((server) => {
      const entry = { ...server };
      delete entry.has_password;
      return entry;
    }),
    default: serversDefault,
  });
}

function handleServersSaved(event) {
  const note = $('#servers-admin-note');
  if (note) note.textContent = `${event.message || ''}保存成功，已生效。`.trim();
}

function handleServersError(event) {
  const note = $('#servers-admin-note');
  if (note) note.textContent = `保存失败：${event.message || '未知错误'}`;
}

function handleServerTestResult(event) {
  const note = $('#servers-admin-note');
  if (note) note.textContent = `测试连接：${event.ok ? '✓ ' : '✗ '}${event.message || (event.ok ? '连接成功' : '连接失败')}`;
  if (event.ok) window.setTimeout(() => renderServersAdmin(), 2500);
}

function createSession(title) {
  const module = activeModule();
  const sessions = moduleSessions(module.id);
  const session = { id: `${module.id}-session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, title, time: '刚刚', messages: [] };
  sessions.unshift(session);
  state.sessionsByModule[module.id] = sessions.slice(0, SESSION_LIMIT);
  state.activeSessionId = session.id;
  saveSessions();
  renderSessionList();
  return state.sessionsByModule[module.id].find((item) => item.id === session.id) || session;
}

function openSession(sessionId) {
  if (state.busy) return;
  const session = moduleSessions(state.activeModuleId).find((item) => item.id === sessionId);
  if (!session) return;
  state.activeSessionId = session.id;
  conversationScroll.innerHTML = '';
  session.messages.forEach((message) => appendMessage(message.role, message.text, message.workspace ? [`工作空间：${message.workspace}`] : []));
  resetTrace();
  showConversation();
  $('#page-title').textContent = session.title;
  clearConversation.classList.toggle('hidden', session.messages.length === 0);
  document.querySelectorAll('.session-item').forEach((item) => item.classList.toggle('active', item.dataset.sessionId === session.id));
}

function addTrace(title, detail, status = 'active') {
  if (traceList.querySelector('.trace-empty')) traceList.innerHTML = '';
  const item = document.createElement('div');
  item.className = `trace-item ${status}`;
  item.dataset.traceId = `trace-${++state.stepCount}`;
  item.innerHTML = `<div class="trace-node">${status === 'done' ? '✓' : '·'}</div><div class="trace-copy"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
  traceList.appendChild(item);
  traceList.scrollTop = traceList.scrollHeight;
  return item.dataset.traceId;
}

function completeTrace(traceId, detail) {
  const item = traceList.querySelector(`[data-trace-id="${traceId}"]`);
  if (!item) return;
  item.classList.remove('active');
  item.classList.add('done');
  item.querySelector('.trace-node').textContent = '✓';
  item.querySelector('.trace-copy span').textContent = detail;
}

function finishActiveTraces(detail) {
  document.querySelectorAll('.trace-item.active').forEach((item) => {
    item.classList.remove('active');
    item.classList.add('done');
    item.querySelector('.trace-node').textContent = '✓';
    item.querySelector('.trace-copy span').textContent = detail;
  });
}

function setBusyState(busy) {
  document.querySelectorAll('.send-button').forEach((button) => {
    button.classList.toggle('thinking', busy);
    button.setAttribute('aria-label', busy ? '取消当前思考' : '发送');
    button.innerHTML = busy
      ? '<span class="send-spinner" aria-hidden="true"></span><span class="send-label">正在思考</span>'
      : '<span class="send-icon">↑</span><span class="send-label">发送</span>';
    button.disabled = false;
  });
  document.querySelectorAll('[data-model-picker]').forEach((button) => { button.disabled = busy || Boolean(activeModule()?.model_locked); });
}

function cancelCurrentRequest({ silent = false } = {}) {
  if (!state.busy || !state.activeRequest) return;
  const requestId = state.activeRequest;
  state.cancelledRequests.add(requestId);
  clearRequestTimer();
  if (state.previewTimer) {
    window.clearTimeout(state.previewTimer);
    state.previewTimer = null;
  }
  if (window.agentDesk?.cancelChat) window.agentDesk.cancelChat({ id: requestId });
  if (silent) {
    // 切换模块时只释放 UI，不写"已取消"占位——等真实结果回来再落回原会话。
    state.detachedRequests.set(requestId, { moduleId: state.activeModuleId, sessionId: state.activeSessionId });
    finishActiveTraces('已取消，未继续执行');
    clearThinkingMessage();
    clearStream();
    state.busy = false;
    state.activeModuleOfRequest = null;
    setBusyState(false);
    return;
  }
  finishActiveTraces('已取消，未继续执行');
  finishTurn(activeModule()?.mode === 'chat' ? '已取消当前思考，未发送任何内容。' : '已取消当前思考，未执行服务器操作。', '已取消当前思考');
}

function clearThinkingMessage() {
  if (!state.thinkingMessage) return;
  state.thinkingMessage.remove();
  state.thinkingMessage = null;
}

const REQUEST_TIMEOUT_MS = 120000;

const STAGE_LABELS = {
  queued: '已提交请求',
  connecting: '连接模型服务',
  thinking: '模型思考中',
  generating: '正在生成回复',
  reading_file: '正在读取文件',
  retrying: '服务出错，正在重试',
};

function clearRequestTimer() {
  if (!state.requestTimer) return;
  window.clearTimeout(state.requestTimer);
  state.requestTimer = null;
}

function resetRequestTimer() {
  const requestId = state.activeRequest;
  if (!state.busy || !requestId) return;
  clearRequestTimer();
  state.requestTimer = window.setTimeout(() => {
    if (!state.busy || state.activeRequest !== requestId) return;
    state.cancelledRequests.add(requestId);
    if (window.agentDesk?.cancelChat) window.agentDesk.cancelChat({ id: requestId });
    finishActiveTraces('等待超时，已停止');
    finishTurn('等待模型响应超过 120 秒，已停止等待。可能是模型服务或网络异常，请稍后重试。', '等待超时');
  }, REQUEST_TIMEOUT_MS);
}

function stopStageTicker() {
  if (state.stageTicker) {
    window.clearInterval(state.stageTicker);
    state.stageTicker = null;
  }
}

function renderStageLine() {
  if (!state.thinkingMessage) return;
  const label = state.thinkingMessage.querySelector('.stage-label');
  if (!label) return;
  const detail = state.stageDetail ? ` ${escapeHtml(state.stageDetail)}` : '';
  const seconds = Math.max(1, Math.round((Date.now() - state.stageStartedAt) / 1000));
  const showSeconds = state.stage !== 'generating';
  label.innerHTML = `<i class="stage-dot"></i><span>${escapeHtml(STAGE_LABELS[state.stage] || '正在思考')}${detail}</span>${showSeconds ? `<em class="stage-sec">${seconds}s</em>` : ''}`;
}

function applyStage(stage, detail) {
  if (!state.thinkingMessage) return;
  state.stage = stage;
  state.stageDetail = detail || '';
  state.stageStartedAt = Date.now();
  let label = state.thinkingMessage.querySelector('.stage-label');
  if (!label) {
    const bubble = state.thinkingMessage.querySelector('.message-bubble');
    if (!bubble) return;
    label = document.createElement('span');
    label.className = 'stage-label';
    bubble.prepend(label);
  }
  stopStageTicker();
  state.stageTicker = window.setInterval(renderStageLine, 1000);
  renderStageLine();
  if (stage === 'generating' && state.streamText) {
    label.classList.add('inline');
  }
}

// 流式：把"正在思考"占位转成渐进文本气泡，delta 一段段追加。
// chat 模块（无轨迹栏）的写确认：渲染成对话流里的卡片。
// 两步流程：第一步只确认"要不要写入"（此时不展示内容），用户点"查看变更"
// 后才展开 diff，最后"确认写入"才落盘 —— 顺序与用户的决策习惯一致。
function renderInlineApproval(event, plan) {
  const message = document.createElement('article');
  message.className = 'message assistant approval-message';
  const detailLines = Object.entries(plan)
    .map(([key, value]) => {
      const text = String(value);
      if (key === '变更' && text.includes('\n')) {
        const colored = text.split('\n').map((line) => {
          const cls = line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : (line.startsWith('-') && !line.startsWith('---') ? 'diff-del' : '');
          return `<span class="diff-line ${cls}">${escapeHtml(line)}</span>`;
        }).join('');
        return `<div class="approval-line"><span>${escapeHtml(key)}</span><code class="diff-block">${colored}</code></div>`;
      }
      return `<div class="approval-line"><span>${escapeHtml(key)}</span><code>${escapeHtml(text)}</code></div>`;
    })
    .join('');
  message.innerHTML = `
    <div class="message-content">
      <div class="message-bubble approval-bubble">
        <strong>AI 想写入文件，需要你确认</strong>
        <div class="approval-line"><span>文件</span><code>${escapeHtml(String(plan.path || ''))}</code></div>
        <div class="approval-step-first"><div class="approval-actions"><button class="review">查看变更内容</button><button class="reject">不允许写入</button></div></div>
        <div class="approval-step-detail" hidden>
          ${detailLines}
          <div class="approval-actions"><button class="approve">确认写入</button><button class="reject">取消</button></div>
        </div>
      </div>
    </div>`;
  const bubble = message.querySelector('.approval-bubble');
  const reject = (text) => {
    bubble.innerHTML = `<strong>${escapeHtml(text)}</strong>`;
  };
  const settle = (text) => {
    bubble.innerHTML = `<strong>${escapeHtml(text)}</strong>`;
    window.setTimeout(() => message.remove(), 2500);
  };
  bubble.querySelector('.approval-step-first .review').addEventListener('click', () => {
    bubble.querySelector('.approval-step-first').hidden = true;
    bubble.querySelector('.approval-step-detail').hidden = false;
  });
  bubble.querySelector('.approval-step-detail .approve').addEventListener('click', () => {
    window.agentDesk.sendApproval({ id: event.id, approved: true });
    settle('已确认，正在写入…');
  });
  bubble.querySelectorAll('.reject').forEach((btn) => btn.addEventListener('click', () => {
    window.agentDesk.sendApproval({ id: event.id, approved: false });
    reject('已取消，未写入文件');
  }));
  conversationScroll.appendChild(message);
  conversationScroll.scrollTop = conversationScroll.scrollHeight;
}

function appendStreamDelta(text) {
  if (!state.thinkingMessage) return;
  const bubble = state.thinkingMessage.querySelector('.message-bubble');
  if (!bubble) return;
  if (!state.streamText) {
    state.thinkingMessage.classList.remove('thinking');
    state.streamText = '';
    const stream = document.createElement('span');
    stream.className = 'stream-text';
    bubble.appendChild(stream);
  }
  state.streamText += text;
  const stream = bubble.querySelector('.stream-text');
  if (stream) stream.textContent = state.streamText;
  conversationScroll.scrollTop = conversationScroll.scrollHeight;
}

function clearStream() {
  state.streamText = '';
  state.stage = '';
  state.stageDetail = '';
  stopStageTicker();
}

function makeRequestId() {
  // Date.now() 同毫秒会撞 id：取消旧请求后立刻发新请求时，取消标记会误拦新回复。
  return `request-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function submitQuestion(question) {
  const value = question.trim();
  if (!value || state.busy) return;
  const module = activeModule();
  const isChat = module.mode === 'chat';
  const workspace = module.allow_files ? moduleWorkspace(module.id) : '';
  state.busy = true;
  state.activeRequest = makeRequestId();
  state.activeModuleOfRequest = module.id;
  const existing = state.activeSessionId ? moduleSessions(module.id).find((item) => item.id === state.activeSessionId) : null;
  const session = existing || createSession(value);
  if (!session) return;
  if (!session.messages.length) session.title = value;
  session.time = '刚刚';
  const history = session.messages.slice(-CHAT_HISTORY_LIMIT).map((message) => ({ role: message.role, text: message.text }));
  const workspaceNameForMessage = workspace ? workspaceName(workspace) : '';
  session.messages.push({ role: 'user', text: value, workspace: workspaceNameForMessage });
  saveSessions();
  renderSessionList();
  composer.value = '';
  compactComposer.value = '';
  showConversation();
  $('#page-title').textContent = session.title;
  clearConversation.classList.remove('hidden');
  appendMessage('user', value, workspaceNameForMessage ? [`工作空间：${workspaceNameForMessage}`] : []);
  showThinkingMessage();
  const thinkingId = addTrace('理解任务', isChat ? '正在整理你的问题' : '正在分析你的请求与上下文');
  if (!isChat) addTrace('安全检查', '准备检查工具权限与参数');
  setBusyState(true);

  if (window.agentDesk) {
    const requestId = state.activeRequest;
    window.agentDesk.sendChat({
      id: requestId,
      question: value,
      model_id: moduleModelId(module),
      module_id: module.id,
      server_id: module.mode === 'agent' ? selectedServerId(module.id) : '',
      history,
      workspace,
    });
    // 兜底：桥接进程挂住或第三方无响应时，不能永远停在"正在思考"。
    resetRequestTimer();
  } else {
    state.previewTimer = window.setTimeout(() => {
      completeTrace(thinkingId, '预览模式：桌面桥接尚未启动');
      addTrace('完成', '这是界面预览回复', 'done');
      finishTurn('这是前端预览模式。通过桌面程序启动后，我会调用真实的工作流。');
    }, 850);
  }
}

function finishTurn(answer, detail = '任务已返回结果') {
  const requestModuleId = state.activeModuleOfRequest || state.activeModuleId;
  const session = moduleSessions(requestModuleId).find((item) => item.id === state.activeSessionId);
  clearStream();
  if (state.thinkingMessage) {
    state.thinkingMessage.remove();
    state.thinkingMessage = null;
  }
  // 切走模块时不在新模块的气泡区渲染，只把结果落回原会话。
  if (requestModuleId === state.activeModuleId) {
    appendMessage('assistant', answer);
  }
  if (session) {
    session.messages.push({ role: 'assistant', text: answer });
    saveSessions();
    if (requestModuleId === state.activeModuleId) renderSessionList();
  }
  state.busy = false;
  state.activeModuleOfRequest = null;
  state.previewTimer = null;
  clearRequestTimer();
  setBusyState(false);
  if (requestModuleId === state.activeModuleId) addTrace('完成', detail, 'done');
}

function requestBelongsToActiveModule() {
  return !state.activeModuleOfRequest || state.activeModuleOfRequest === state.activeModuleId;
}

function handleBridgeEvent(event) {
  if (event.type === 'app_config') {
    if (Array.isArray(event.servers)) {
      availableServers = event.servers;
    }
    applyModuleConfig(event);
    renderServerPicker();
    return;
  }
  if (event.type === 'status') {
    if (event.id && event.id !== state.activeRequest) return;
    if (!activeModule()?.show_trace || !requestBelongsToActiveModule()) return;
    addTrace(event.message || 'Agent 正在工作', '工作流已启动');
    return;
  }
  if (event.type === 'bridge_error') {
    if (activeModule()?.show_trace && requestBelongsToActiveModule()) addTrace('服务异常', event.message || '桥接进程发生错误');
    finishTurn(`无法连接到 Agent 服务：${event.message || '未知错误'}`);
    return;
  }
  if (event.type === 'approval_request') {
    if (event.request_id && state.cancelledRequests.has(event.request_id)) {
      window.agentDesk.sendApproval({ id: event.id, approved: false });
      return;
    }
    if (!requestBelongsToActiveModule()) {
      window.agentDesk.sendApproval({ id: event.id, approved: false });
      return;
    }
    const plan = event.plan || {};
    if (activeModule()?.show_trace) {
      const traceId = addTrace('等待确认', `即将执行 ${event.action || '写操作'}`);
      const item = traceList.querySelector(`[data-trace-id="${traceId}"]`);
      const card = document.createElement('div');
      card.className = 'approval-card';
      card.innerHTML = `<strong>需要你的确认</strong><p>${escapeHtml(event.action || '即将执行一项写操作')}<br />${escapeHtml(JSON.stringify(plan, null, 2))}</p><div class="approval-actions"><button class="approve">确认执行</button><button class="reject">取消</button></div>`;
      item.querySelector('.trace-copy').appendChild(card);
      card.querySelector('.approve').addEventListener('click', () => { card.remove(); completeTrace(traceId, '已确认，继续执行'); window.agentDesk.sendApproval({ id: event.id, approved: true }); });
      card.querySelector('.reject').addEventListener('click', () => {
        card.remove();
        completeTrace(traceId, '已取消，未执行服务器操作');
        finishActiveTraces('已取消，未继续执行');
        if (state.activeRequest) state.cancelledRequests.add(state.activeRequest);
        window.agentDesk.sendApproval({ id: event.id, approved: false });
        finishTurn('已取消本次服务器操作，未执行任何修改。', '已取消，未执行服务器操作');
      });
      return;
    }
    renderInlineApproval(event, plan);
    return;
  }
  if (event.type === 'chat_delta') {
    if (event.id && event.id !== state.activeRequest) return;
    if (!requestBelongsToActiveModule()) return;
    if (event.text) appendStreamDelta(event.text);
    resetRequestTimer();
    return;
  }
  if (event.type === 'models_config') {
    handleModelsConfig(event);
    return;
  }
  if (event.type === 'servers_config') {
    handleServersConfig(event);
    return;
  }
  if (event.type === 'servers_saved') {
    handleServersSaved(event);
    return;
  }
  if (event.type === 'servers_config_error' || event.type === 'servers_save_error') {
    handleServersError(event);
    return;
  }
  if (event.type === 'server_test_result') {
    handleServerTestResult(event);
    return;
  }
  if (event.type === 'models_saved') {
    handleModelsSaved(event);
    return;
  }
  if (event.type === 'models_config_error' || event.type === 'models_save_error') {
    handleModelsError(event);
    return;
  }
  if (event.type === 'chat_stage') {
    if (event.id && event.id !== state.activeRequest) return;
    if (!requestBelongsToActiveModule()) return;
    applyStage(event.stage, event.detail);
    resetRequestTimer();
    return;
  }
  if (event.type === 'chat_done') {
    const detached = event.id ? state.detachedRequests.get(event.id) : null;
    if (event.id && state.cancelledRequests.has(event.id) && !detached) {
      state.cancelledRequests.delete(event.id);
      return;
    }
    if (detached) {
      // 切走后才回来的结果：只落回原会话，不碰当前视图。
      state.detachedRequests.delete(event.id);
      state.cancelledRequests.delete(event.id);
      const session = moduleSessions(detached.moduleId).find((item) => item.id === detached.sessionId);
      if (session) {
        session.messages.push({ role: 'assistant', text: event.answer || '' });
        saveSessions();
      }
      return;
    }
    if (event.id && event.id !== state.activeRequest) return;
    // 流式气泡已展示了同样的内容，直接把它升级成最终渲染，避免先删再加的闪烁。
    if (state.thinkingMessage && state.streamText) {
      const bubble = state.thinkingMessage.querySelector('.message-bubble');
      const answer = event.answer || '任务已完成。';
      const isFailure = FAILURE_PATTERN.test(String(answer).trim());
      if (bubble) {
        state.thinkingMessage.classList.toggle('error', isFailure);
        bubble.innerHTML = formatAssistantText(answer);
      }
      state.thinkingMessage.classList.remove('thinking');
      const content = state.thinkingMessage.querySelector('.message-content');
      if (isFailure && content) {
        const bar = document.createElement('div');
        bar.className = 'retry-bar';
        bar.innerHTML = '<button type="button" class="retry-button">重试</button>';
        content.appendChild(bar);
        content.querySelector('.retry-button').addEventListener('click', () => retryFrom(state.thinkingMessage, answer));
      }
      attachProposalActions(state.thinkingMessage, answer);
      state.thinkingMessage.classList.remove('thinking');
      state.thinkingMessage = null;
      clearStream();
      const session = moduleSessions(state.activeModuleOfRequest || state.activeModuleId).find((item) => item.id === state.activeSessionId);
      if (session) {
        session.messages.push({ role: 'assistant', text: answer });
        saveSessions();
        renderSessionList();
      }
      state.busy = false;
      state.activeModuleOfRequest = null;
      clearRequestTimer();
      setBusyState(false);
      if (activeModule()?.show_trace) finishActiveTraces('已完成');
      addTrace('完成', '任务已返回结果', 'done');
      return;
    }
    clearStream();
    if (requestBelongsToActiveModule()) finishActiveTraces('已完成');
    finishTurn(event.answer || '任务已完成。');
  }
}

function bindComposer(form, input) {
  form.addEventListener('submit', (event) => { event.preventDefault(); submitQuestion(input.value); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submitQuestion(input.value); }
  });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; });
}

bindComposer($('#composer-form'), composer);
bindComposer($('#conversation-form'), compactComposer);
document.querySelectorAll('.send-button').forEach((button) => button.addEventListener('click', (event) => {
  if (!state.busy) return;
  event.preventDefault();
  cancelCurrentRequest();
}));
renderModuleTabs();
renderSessionList();
// Logo：主进程把图标绝对路径经页面参数传入（打包后 icon 不在 asar 内）。
// 拿不到时回退到相对路径（开发模式）。
(function applyBrandLogo() {
  const iconParam = new URLSearchParams((window.location && window.location.search) || '').get('icon');
  const sources = [];
  if (iconParam) sources.push(`file://${iconParam}`);
  sources.push('../assets/icon.png');
  document.querySelectorAll('img.brand-logo, img.avatar, img.profile-mark').forEach((img) => {
    let attempt = 0;
    const tryNext = () => {
      attempt += 1;
      if (attempt < sources.length) img.src = sources[attempt];
    };
    img.addEventListener('error', tryNext);
    img.src = sources[0];
  });
})();

const initialAppearance = loadAppearance();
backgroundDataUrl = initialAppearance.legacyImage || '';
applyAppearance(initialAppearance);
applyModule();
if (initialAppearance.legacyImage && window.agentDesk?.saveBackground) {
  window.agentDesk.saveBackground({ dataUrl: initialAppearance.legacyImage, fileName: initialAppearance.fileName }).then((result) => {
    if (!result?.ok) return;
    backgroundDataUrl = initialAppearance.legacyImage;
    saveAppearance({ theme: normalizeTheme(initialAppearance.theme), opacity: initialAppearance.opacity ?? 55, fileName: initialAppearance.fileName || '', hasImage: true });
  });
} else if (initialAppearance.hasImage && window.agentDesk?.loadBackground) {
  window.agentDesk.loadBackground().then((result) => {
    if (!result?.dataUrl) return;
    backgroundDataUrl = result.dataUrl;
    applyAppearance(loadAppearance());
  });
}
document.querySelectorAll('[data-model-picker]').forEach((button) => button.addEventListener('click', (event) => {
  event.stopPropagation();
  if (modelMenu.classList.contains('hidden')) openModelMenu(button);
  else closeModelMenu();
}));
document.addEventListener('click', closeModelMenu);
workspaceControls.forEach(({ button }) => button.addEventListener('click', (event) => {
  event.stopPropagation();
  if (workspaceMenu.classList.contains('hidden')) openWorkspaceMenu(button);
  else closeWorkspaceMenu();
}));
document.addEventListener('click', closeWorkspaceMenu);
$('#new-task').addEventListener('click', goHome);
$('#workbench-nav').addEventListener('click', goHome);
$('#settings-nav').addEventListener('click', showSettings);
clearConversation.addEventListener('click', () => {
  if (state.busy) return;
  const module = activeModule();
  if (!module.show_sessions) return;
  if (state.activeSessionId && !window.confirm('清空当前对话的上下文？')) return;
  const session = moduleSessions(module.id).find((item) => item.id === state.activeSessionId);
  if (!session) return;
  session.messages = [];
  saveSessions();
  renderSessionList();
  goHome();
});
document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n') {
    event.preventDefault();
    goHome();
  }
  if ((event.metaKey || event.ctrlKey) && event.key === ',') {
    event.preventDefault();
    showSettings();
  }
});
themeSelect.addEventListener('change', () => updateAppearance({ theme: themeSelect.value }));
backgroundOpacity.addEventListener('input', () => updateAppearance({ opacity: Number(backgroundOpacity.value) }));
backgroundFile.addEventListener('change', () => {
  const file = backgroundFile.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    settingsStatus.textContent = '正在保存背景图片…';
    const result = await window.agentDesk?.saveBackground({ dataUrl: reader.result, fileName: file.name });
    if (!result?.ok) {
      settingsStatus.textContent = result?.message || '背景图片保存失败。';
      return;
    }
    backgroundDataUrl = reader.result;
    updateAppearance({ hasImage: true, fileName: file.name });
  };
  reader.readAsDataURL(file);
});
$('#clear-background').addEventListener('click', async () => {
  await window.agentDesk?.clearBackground();
  backgroundDataUrl = '';
  updateAppearance({ hasImage: false, fileName: '' });
});
$('#models-add').addEventListener('click', () => {
  modelsDraft.push({
    id: `model-${Date.now().toString(36)}`,
    label: '',
    model: '',
    base_url: 'https://api.deepseek.com',
    wire_api: 'chat',
    reasoning_effort: '',
    enabled: true,
  });
  modelsEditingIndex = modelsDraft.length - 1;
  renderModelsAdmin();
});
$('#models-save').addEventListener('click', saveModelsDraft);
document.querySelectorAll('[data-server-picker]').forEach((picker) => {
  picker.addEventListener('change', () => {
    rememberServerSelection(state.activeModuleId, picker.value);
  });
});
$('#servers-add').addEventListener('click', () => {
  serversDraft.push({
    id: `server-${Date.now().toString(36)}`,
    label: '',
    host: '',
    port: 22,
    username: 'root',
    password: '',
    connect_timeout: 10,
  });
  serversEditingIndex = serversDraft.length - 1;
  renderServersAdmin();
});
$('#servers-save').addEventListener('click', saveServersDraft);
if (window.agentDesk) {
  window.agentDesk.onEvent(handleBridgeEvent);
  window.agentDesk.requestConfig();
}