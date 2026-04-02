const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 1 1-6 0V6a3 3 0 0 1 3-3z"></path><path d="M19 11a7 7 0 0 1-14 0"></path><path d="M12 18v3"></path><path d="M8 21h8"></path></svg>';
const SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13"></path><path d="m22 2-7 20-4-9-9-4 20-7Z"></path></svg>';
const INITIAL_RUNTIME = window.__INITIAL_RUNTIME__ || {};
const KNOWLEDGE_SEARCH_OPTIONS = [
  '番剧',
  '国创',
  '纪录片',
  '电影',
  '电视剧',
  '综艺',
  '动画',
  '游戏',
  '鬼畜',
  '音乐',
  '舞蹈',
  '科技数码',
  '汽车',
  '时尚美妆',
  '体育运动',
  '动物',
  '生活',
  '知识科普',
  '娱乐热点',
  '职场成长',
  '情感婚恋',
  '两性心理',
  '通用爆款',
];
const KNOWLEDGE_PARTITION_LABELS = {
  knowledge: '知识',
  tech: '科技',
  life: '生活',
  game: '游戏',
  ent: '娱乐',
};

const state = {
  videoResolved: null,
  videoResolvedUrl: '',
  videoResolveTimer: null,
  videoResolveSeq: 0,
  moduleSplit: 0.5,
  moduleSwapProgress: 0,
  introCollapse: 0,
  sceneTicking: false,
  progressJobs: {},
  activeModule: 'analyze',
  knowledgeActiveSubtab: 'upload',
  knowledgeStatus: null,
  knowledgeUpdateTask: null,
  knowledgeForm: {
    updateLimit: 10,
    viewLimit: 6,
    searchQuery: '',
  },
  knowledgeResults: {
    upload: '',
    sync: '',
    view: '',
    search: '',
  },
  chatPending: false,
  chatTyping: false,
  chatHistory: [],
  pendingRuntimeRetryAction: null,
  runtime: {
    mode: INITIAL_RUNTIME.mode || 'rules',
    llmEnabled: Boolean(INITIAL_RUNTIME.llm_enabled),
    chatAvailable: Boolean(INITIAL_RUNTIME.chat_available),
    switchChecked: Boolean(INITIAL_RUNTIME.switch_checked),
    hasSavedConfig: Boolean(INITIAL_RUNTIME.has_saved_llm_config),
    savedConfigSource: INITIAL_RUNTIME.saved_config_source || '',
    savedProvider: INITIAL_RUNTIME.saved_provider || '',
    savedModel: INITIAL_RUNTIME.saved_model || '',
    savedBaseUrl: INITIAL_RUNTIME.saved_base_url || '',
    savedApiKeyMasked: INITIAL_RUNTIME.saved_api_key_masked || '',
    requiresConfig: Boolean(INITIAL_RUNTIME.requires_config),
    modeLabel: INITIAL_RUNTIME.mode_label || '无 Key 逻辑模式',
    modeTitle: INITIAL_RUNTIME.mode_title || '当前运行中：无 Key 逻辑模式',
    modeDescription: INITIAL_RUNTIME.mode_description || '',
    tokenPolicy: INITIAL_RUNTIME.token_policy || '',
    switchHint: INITIAL_RUNTIME.switch_hint || '',
    forceConfigPrompt: false,
    runtimeErrorMessage: '',
  },
  recognition: null,
  isListening: false,
};

function knowledgeMajorTopicLabel(value = '') {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const head = raw.split('｜')[0].trim();
  return head.includes('・') ? head.split('・')[0].trim() : head;
}

function knowledgePartitionLabel(value = '') {
  const raw = String(value || '').trim();
  if (!raw) return '';
  return KNOWLEDGE_PARTITION_LABELS[raw.toLowerCase()] || raw;
}

function parseKnowledgeDocumentId(value = '') {
  const raw = String(value || '').trim();
  if (!raw) {
    return { boardType: '', partition: '', bvid: '' };
  }
  const partitionMatch = raw.match(/^分区热门榜:([^:]+)/);
  const boardMatch = raw.match(/^(全站热门榜|每周必看|入站必刷)/);
  const bvidMatch = raw.match(/BV[0-9A-Za-z]{10}/);
  return {
    boardType: partitionMatch ? `分区热门榜:${partitionMatch[1]}` : (boardMatch ? boardMatch[1] : ''),
    partition: partitionMatch ? partitionMatch[1] : '',
    bvid: bvidMatch ? bvidMatch[0] : '',
  };
}

function parseKnowledgeStructuredPayload(text = '') {
  const raw = String(text || '').trim();
  if (!raw || !raw.startsWith('{')) return null;
  try {
    const payload = JSON.parse(raw);
    return payload && typeof payload === 'object' ? payload : null;
  } catch (error) {
    return null;
  }
}

function extractKnowledgeTextField(text = '', field = '') {
  const raw = String(text || '');
  const name = String(field || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  if (!raw || !name) return '';
  const fullMatch = raw.match(new RegExp(`"${name}"\\s*:\\s*"((?:\\\\.|[^"])*)"`, 'u'));
  if (fullMatch) {
    return fullMatch[1]
      .replace(/\\"/g, '"')
      .replace(/\\\\/g, '\\')
      .trim();
  }
  const partialMatch = raw.match(new RegExp(`"${name}"\\s*:\\s*"([^\\n]*)`, 'u'));
  return partialMatch ? partialMatch[1].replace(/[",\s]+$/g, '').trim() : '';
}

function knowledgeBoardLabel(value = '') {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (raw.startsWith('分区热门榜:')) return '分区热门榜';
  return raw.split(':')[0].trim();
}

function knowledgeSourceLabel(metadata = {}, payload = null, parsedId = {}) {
  const boardLabel = knowledgeBoardLabel(metadata.board_type || payload?.榜单来源 || parsedId.boardType || '');
  const source = String(metadata.source || '').trim();
  if (source === 'uploaded_file') return '上传文件';
  if (source === 'bilibili_hot_sync') return boardLabel || '热门样本同步';
  return boardLabel || source;
}

function knowledgeCategoryLabel(metadata = {}, payload = null, parsedId = {}, fallbackQuery = '') {
  const boardType = String(metadata.board_type || payload?.榜单来源 || parsedId.boardType || '').trim();
  if (boardType.startsWith('分区热门榜:')) {
    return knowledgePartitionLabel(boardType.slice('分区热门榜:'.length).split(':')[0]);
  }
  if (parsedId.partition) {
    return knowledgePartitionLabel(parsedId.partition);
  }
  return knowledgeMajorTopicLabel(fallbackQuery);
}

function knowledgeDocumentTitle(item = {}, metadata = {}, payload = null) {
  const rawDocId = String(item.id || metadata.document_id || '').trim();
  const extractedTitle = String(payload?.视频标题 || extractKnowledgeTextField(item.text || '', '视频标题') || metadata.title || '').trim();
  const extractedPartition = String(payload?.分区 || extractKnowledgeTextField(item.text || '', '分区') || metadata.partition || '').trim();
  const fallbackTitle = String(metadata.filename || rawDocId || '未命名文档').trim();
  const titleLooksLikeDocId = !extractedTitle && (
    fallbackTitle === rawDocId
    || /^(分区热门榜:|全站热门榜:|每周必看:|入站必刷:|file:)/.test(fallbackTitle)
    || /BV[0-9A-Za-z]{10}/.test(fallbackTitle)
  );
  const cleanTitle = extractedTitle || (titleLooksLikeDocId ? '' : fallbackTitle);
  const boardType = String(metadata.board_type || payload?.榜单来源 || parseKnowledgeDocumentId(rawDocId).boardType || '').trim();
  const boardLabel = knowledgeBoardLabel(boardType);
  if (boardLabel) {
    return [boardLabel, extractedPartition, cleanTitle].filter(Boolean).join('：') || boardLabel;
  }
  return cleanTitle || extractedPartition || fallbackTitle;
}

function knowledgeDocumentTags(metadata = {}, context = {}) {
  const list = [];
  if (context.category) list.push(`分类：${context.category}`);
  if (context.source) list.push(`来源：${context.source}`);
  if (context.bvid) list.push(`BVID：${context.bvid}`);
  if (context.filename && context.filename !== context.title) list.push(`文件：${context.filename}`);
  if (context.sourceChannel === 'web_upload') {
    list.push('导入：网页上传');
  } else if (context.sourceChannel) {
    list.push(`导入：${context.sourceChannel}`);
  }

  const hiddenKeys = new Set(['source', 'board_type', 'partition', 'filename', 'source_channel', 'bvid', 'title', 'content_hash', 'document_id']);
  Object.entries(metadata).forEach(([key, value]) => {
    if (hiddenKeys.has(key) || value === null || value === undefined || String(value).trim() === '') return;
    if (key === 'chunk_index') {
      list.push(`分片：${value}`);
      return;
    }
    list.push(`${key}: ${value}`);
  });

  return list;
}

function knowledgeChunkIndex(metadata = {}) {
  const value = Number(metadata?.chunk_index);
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
}

function groupKnowledgeDocuments(items = []) {
  const groups = new Map();
  (Array.isArray(items) ? items : []).forEach((item, index) => {
    const metadata = item?.metadata && typeof item.metadata === 'object' ? item.metadata : {};
    const key = String(item?.id || metadata.document_id || `knowledge_doc_${index}`);
    const chunkIndex = knowledgeChunkIndex(metadata);
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        ...item,
        metadata: { ...metadata },
        _chunkIndex: chunkIndex,
      });
      return;
    }

    const nextScore = Number(item?.score);
    const prevScore = Number(existing.score);
    if (item?.score !== undefined && (!Number.isFinite(prevScore) || (Number.isFinite(nextScore) && nextScore < prevScore))) {
      existing.score = nextScore;
    }

    if (chunkIndex < existing._chunkIndex) {
      existing.text = item?.text || existing.text;
      existing.metadata = { ...metadata };
      existing._chunkIndex = chunkIndex;
      if (item?.id) existing.id = item.id;
    }
  });

  return Array.from(groups.values()).map(({ _chunkIndex, ...item }) => item);
}

// 读取单个匹配选择器的 DOM 节点。
const $ = selector => document.querySelector(selector);
// 读取所有匹配选择器的 DOM 节点并转成数组。
const $$ = selector => Array.from(document.querySelectorAll(selector));

// 转义 HTML 特殊字符，避免把用户内容直接插进页面时产生注入问题。
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 把纯文本转换成适合富文本区域展示的 HTML。
function rich(value) {
  return escapeHtml(value || '').replace(/\n/g, '<br>');
}

// 把数字格式化成中文环境下更易读的展示文本。
function num(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '0';
}

// 判断一个指标是否为“明确数值”，用于区分真实的 0 和缺失字段。
function hasMetricValue(value) {
  return value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value));
}

// 把比例值格式化成百分比字符串。
function pct(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

// 统一处理封面 URL，补齐协议并修正 http 地址。
function coverUrl(url) {
  const v = String(url || '').trim();
  if (!v) return '';
  if (v.startsWith('//')) return `https:${v}`;
  if (v.startsWith('http://')) return `https://${v.slice('http://'.length)}`;
  return v;
}

const COVER_RETRY_LIMIT = 2;
const COVER_RETRY_BASE_DELAY_MS = 900;

// 生成封面区域的 HTML，包括加载态和失败兜底态。
function renderCoverMedia(url, title, variant = 'card') {
  const safeUrl = coverUrl(url);
  const safeTitle = title || '视频封面';
  const stateClass = safeUrl ? 'is-loading' : 'is-fallback';
  return `
    <div class="cover-frame cover-frame--${escapeHtml(variant)} ${stateClass}" data-cover-frame>
      <div class="cover-frame__loader" data-cover-loader ${safeUrl ? '' : 'hidden'} aria-hidden="true">
        <span class="cover-frame__loader-orbit">
          <span class="cover-frame__loader-core"></span>
        </span>
        <span class="cover-frame__loader-text">封面加载中</span>
      </div>
      ${safeUrl ? `<img class="cover-frame__img" data-cover-image data-original-src="${escapeHtml(safeUrl)}" src="${escapeHtml(safeUrl)}" alt="${escapeHtml(safeTitle)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" />` : ''}
      <div class="cover-frame__fallback" data-cover-fallback ${safeUrl ? 'hidden' : ''} title="${escapeHtml(safeTitle)}" aria-label="${escapeHtml(safeTitle)}">
        <span class="cover-frame__fallback-title">b站视频</span>
      </div>
    </div>
  `;
}

// 为封面重试请求拼一个带时间戳的地址，尽量绕开缓存失败。
function coverRetrySrc(src, attempt) {
  if (!src) return '';
  try {
    const next = new URL(src, window.location.href);
    next.searchParams.set('_cover_retry', `${attempt}-${Date.now()}`);
    return next.toString();
  } catch (error) {
    const separator = src.includes('?') ? '&' : '?';
    return `${src}${separator}_cover_retry=${attempt}-${Date.now()}`;
  }
}

// 切换封面组件的加载、成功和失败状态。
function setCoverFrameState(frame, nextState) {
  if (!frame) return;
  const loader = frame.querySelector('[data-cover-loader]');
  const fallback = frame.querySelector('[data-cover-fallback]');
  const img = frame.querySelector('[data-cover-image]');

  frame.dataset.coverState = nextState;
  frame.classList.toggle('is-loading', nextState === 'loading');
  frame.classList.toggle('is-loaded', nextState === 'loaded');
  frame.classList.toggle('is-fallback', nextState === 'fallback');

  if (loader) loader.hidden = nextState !== 'loading';
  if (fallback) fallback.hidden = nextState !== 'fallback';
  if (img) {
    if (nextState === 'fallback') {
      img.hidden = true;
      img.style.display = 'none';
    } else {
      img.hidden = false;
      img.style.display = '';
    }
  }
}

// 在封面彻底加载失败时切到兜底展示。
function finalizeCoverFallback(frame, img) {
  if (img) {
    img.hidden = true;
    img.style.display = 'none';
    img.removeAttribute('src');
  }
  setCoverFrameState(frame, 'fallback');
}

// 按退避节奏安排封面图片重试加载。
function scheduleCoverRetry(img, frame) {
  const retries = Number(img?.dataset.retryCount || 0);
  if (!img || !frame) return;
  if (retries >= COVER_RETRY_LIMIT) {
    finalizeCoverFallback(frame, img);
    return;
  }
  img.dataset.retryCount = String(retries + 1);
  const retryDelay = COVER_RETRY_BASE_DELAY_MS + retries * 700;
  window.setTimeout(() => {
    if (!img.isConnected) return;
    const originalSrc = img.dataset.originalSrc || '';
    if (!originalSrc) {
      finalizeCoverFallback(frame, img);
      return;
    }
    setCoverFrameState(frame, 'loading');
    img.hidden = false;
    img.style.display = '';
    img.src = coverRetrySrc(originalSrc, retries + 1);
  }, retryDelay);
}

// 给单张封面图片绑定加载成功和失败处理逻辑。
function bindCoverImage(img) {
  if (!img || img.dataset.coverBound === '1') return;
  img.dataset.coverBound = '1';

  const frame = img.closest('[data-cover-frame]');
  if (!frame) return;
  if (!img.dataset.originalSrc) {
    img.dataset.originalSrc = img.getAttribute('data-original-src') || img.currentSrc || img.src || '';
  }

  const handleLoad = () => {
    if (!img.naturalWidth) {
      scheduleCoverRetry(img, frame);
      return;
    }
    setCoverFrameState(frame, 'loaded');
  };

  const handleError = () => {
    scheduleCoverRetry(img, frame);
  };

  img.addEventListener('load', handleLoad);
  img.addEventListener('error', handleError);

  if (img.complete) {
    handleLoad();
  } else {
    setCoverFrameState(frame, 'loading');
  }
}

// 在指定范围内批量绑定封面媒体组件。
function bindCoverMedia(scope = document) {
  const root = scope && typeof scope.querySelectorAll === 'function' ? scope : document;
  if (root.matches && root.matches('[data-cover-image]')) {
    bindCoverImage(root);
  }
  if (root.matches && root.matches('[data-cover-frame]')) {
    const rootImage = root.querySelector('[data-cover-image]');
    if (!rootImage) {
      setCoverFrameState(root, 'fallback');
    } else {
      bindCoverImage(rootImage);
    }
  }
  root.querySelectorAll('[data-cover-frame]').forEach(frame => {
    const img = frame.querySelector('[data-cover-image]');
    if (!img) {
      setCoverFrameState(frame, 'fallback');
      return;
    }
    bindCoverImage(img);
  });
}

// 初始化全局封面媒体监听，处理后续动态插入的图片节点。
function initCoverMedia() {
  bindCoverMedia(document);
  if (state.coverObserver) {
    state.coverObserver.disconnect();
  }
  state.coverObserver = new MutationObserver(mutations => {
    mutations.forEach(mutation => {
      mutation.addedNodes.forEach(node => {
        if (!(node instanceof Element)) return;
        if (node.matches('[data-cover-frame], [data-cover-image]')) {
          bindCoverMedia(node);
          return;
        }
        if (node.querySelector('[data-cover-frame], [data-cover-image]')) {
          bindCoverMedia(node);
        }
      });
    });
  });
  state.coverObserver.observe(document.body, { childList: true, subtree: true });
}

// 更新页面顶部的全局状态提示。
function setStatus(text, type = 'idle') {
  const pill = $('#globalStatusPill');
  if (!pill) return;
  pill.classList.remove('is-loading', 'is-success', 'is-error');
  if (type === 'loading') pill.classList.add('is-loading');
  if (type === 'success') pill.classList.add('is-success');
  if (type === 'error') pill.classList.add('is-error');
  $('#statusText').textContent = text;
  $('#currentModeText').textContent = text;
}

// 在页面右下角弹出短暂提示消息。
function showToast(title, message, type = 'success') {
  const stack = $('#toastStack');
  if (!stack) return;
  const node = document.createElement('div');
  node.className = `toast toast--${type}`;
  node.innerHTML = `<div class="toast__title">${escapeHtml(title)}</div><div>${escapeHtml(message)}</div>`;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 2800);
}

// 切换按钮的 loading 状态和禁用态。
function setButtonLoading(id, loading) {
  const button = document.getElementById(id);
  if (!button) return;
  button.disabled = loading;
  button.classList.toggle('is-loading', loading);
}

// 根据内容自动调整文本框高度。
function autosize(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
}

// 返回一个指定时长后完成的 Promise。
function sleep(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

// 把百分比值限制在 0 到 100 之间。
function clampPercent(value) {
  return Math.max(0, Math.min(100, Math.round(value || 0)));
}

// 把进度值限制在 0 到 100 之间并转成数字。
function clampProgressValue(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

// 把进度数值格式化成前端展示用的百分比文本。
function formatProgressLabel(value) {
  const safe = clampProgressValue(value);
  if (safe >= 100) return '100%';
  return `${safe.toFixed(1)}%`;
}

// 停止某个进度条任务，并可选写入最终进度。
function stopProgressJob(key, finalPercent = null) {
  const job = state.progressJobs[key];
  if (!job) return;
  if (job.timer) {
    window.clearInterval(job.timer);
  }
  if (finalPercent !== null && typeof job.onUpdate === 'function') {
    job.percent = clampProgressValue(finalPercent);
    job.onUpdate(job.percent);
  }
  delete state.progressJobs[key];
}

// 启动一个模拟进度条任务，给异步请求提供平滑的视觉反馈。
function startProgressJob(key, onUpdate, options = {}) {
  stopProgressJob(key);
  const start = clampProgressValue(options.start ?? 6);
  const max = clampProgressValue(options.max ?? 94);
  const cap = Math.max(max, clampProgressValue(options.cap ?? 99.85));
  const slowStart = Math.min(cap, clampProgressValue(options.slowStart ?? 75));
  const tailStart = Math.min(cap, Math.max(slowStart, clampProgressValue(options.tailStart ?? 95)));
  const interval = options.interval ?? 180;
  const durationMs = options.durationMs ?? 12000;
  const ticks = Math.max(1, Math.round(durationMs / interval));
  const job = {
    percent: start,
    max,
    cap,
    slowStart,
    tailStart,
    increment: Math.max(options.minStep ?? 0.35, (max - start) / ticks),
    midMinStep: Math.max(0.04, Number(options.midMinStep ?? 0.06)),
    midFactor: Math.max(0.003, Number(options.midFactor ?? 0.008)),
    tailMinStep: Math.max(0.03, Number(options.tailMinStep ?? 0.05)),
    tailFactor: Math.max(0.004, Number(options.tailFactor ?? 0.012)),
    onUpdate,
    timer: null,
  };
  state.progressJobs[key] = job;

  if (typeof onUpdate === 'function') {
    onUpdate(job.percent);
  }

  job.timer = window.setInterval(() => {
    if (job.percent >= job.cap - 0.002) return;
    let nextPercent = job.percent;
    if (job.percent < job.slowStart) {
      nextPercent = Math.min(job.slowStart, job.percent + job.increment);
    } else if (job.percent < job.tailStart) {
      const remaining = job.tailStart - job.percent;
      const midStep = Math.max(job.midMinStep, remaining * job.midFactor);
      nextPercent = Math.min(job.tailStart, job.percent + midStep);
    } else {
      const remaining = job.cap - job.percent;
      if (remaining <= 0) return;
      const tailStep = Math.max(job.tailMinStep, remaining * job.tailFactor);
      nextPercent = Math.min(job.cap, job.percent + tailStep);
    }
    if (nextPercent <= job.percent) return;
    job.percent = nextPercent;
    if (typeof onUpdate === 'function') {
      onUpdate(job.percent);
    }
  }, interval);

  return job;
}

// 读取某个进度任务当前的进度百分比。
function getProgressPercent(key, fallback = 0) {
  return clampProgressValue(state.progressJobs[key]?.percent ?? fallback);
}

// 发送 POST JSON 请求，并统一处理后端错误结构。
async function requestJson(url, payload) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      const error = new Error(data.error || '请求失败');
      error.payload = data.data || {};
      throw error;
    }
    return data.data;
  } catch (error) {
    if (error instanceof Error && /Failed to fetch/i.test(error.message)) {
      throw new Error('接口请求失败，请检查 Flask 服务是否在运行，或后端是否正在重启。');
    }
    throw error;
  }
}

// 发送 GET 请求并按项目约定解析 JSON 响应。
async function requestGetJson(url) {
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || '请求失败');
    return data.data;
  } catch (error) {
    if (error instanceof Error && /Failed to fetch/i.test(error.message)) {
      throw new Error('接口请求失败，请检查 Flask 服务是否在运行。');
    }
    throw error;
  }
}

// 发送 multipart/form-data 请求，供知识库文件上传使用。
async function requestFormData(url, formData) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || '请求失败');
    }
    return data.data;
  } catch (error) {
    if (error instanceof Error && /Failed to fetch/i.test(error.message)) {
      throw new Error('接口请求失败，请检查 Flask 服务是否在运行，或后端是否正在重启。');
    }
    throw error;
  }
}

// 调用浏览器剪贴板接口复制文本，并给出提示。
async function copyText(text, label = '内容') {
  try {
    await navigator.clipboard.writeText(text);
    showToast('复制成功', `${label}已复制到剪贴板`);
  } catch (error) {
    showToast('复制失败', '当前浏览器不支持自动复制，请手动复制', 'error');
  }
}

// 在指定区域内批量绑定“一键复制”按钮行为。
function bindCopyButtons(scope = document) {
  scope.querySelectorAll('[data-copy]').forEach(button => {
    if (button.dataset.copyBound === '1') return;
    button.dataset.copyBound = '1';
    button.addEventListener('click', () => copyText(button.dataset.copy || '', button.dataset.copyLabel || '内容'));
  });
}

// 把标签数组渲染成统一样式的标签列表。
function tags(items = []) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  return list.length
    ? `<div class="tag-list">${list.map(item => `<span class="tag">${escapeHtml(item)}</span>`).join('')}</div>`
    : '<p class="section-note">暂无标签</p>';
}

// 生成一个简洁的信息提示卡片。
function infoCard(title, text, tone = '') {
  return `<div class="info-card ${tone ? `info-card--${tone}` : ''}"><h4>${escapeHtml(title)}</h4><p>${escapeHtml(text)}</p></div>`;
}

// 渲染当前知识库状态卡片。
function knowledgeStatusView(payload = {}, summaryHtml = '') {
  const available = Boolean(payload.available);
  const countText = available ? num(payload.document_count || 0) : '不可用';
  const backendText = payload.backend || 'disabled';
  const pathText = payload.vector_db_path || payload.persist_directory || './vector_db';
  const memoryText = payload.memory_backend || 'disabled';
  const uploadTypes = Array.isArray(payload.supported_upload_types) ? payload.supported_upload_types : [];
  const usingJsonFallback = backendText === 'json_fallback';
  const detailHtml = payload.backend_detail
    ? `<div class="info-card ${usingJsonFallback ? '' : 'info-card--danger'}"><h4>${usingJsonFallback ? '回退说明' : '后端说明'}</h4><p>${escapeHtml(payload.backend_detail)}</p></div>`
    : '';
  const errorHtml = payload.init_error
    ? `<div class="info-card info-card--danger"><h4>初始化错误</h4><p>${escapeHtml(payload.init_error)}</p></div>`
    : '';
  const embeddingHtml = payload.embedding_model
    ? `<div class="info-card"><h4>Embedding 模型</h4><p>${escapeHtml(payload.embedding_model)}${payload.embedding_fallback ? '（当前处于 fallback）' : ''}</p></div>`
    : '';

  return `
    <section class="copy-block">
      <div class="block-title">
        <div><h4>知识库状态</h4><p>${available ? (usingJsonFallback ? '当前未启用 Chroma，系统已回退到本地 JSON 存储，上传、同步、检索仍可继续使用。' : '当前向量库已可用，后续检索会优先走这里。') : '当前未检测到可用知识库后端，相关检索和入库会报错。'}</p></div>
        <span class="type-badge ${available ? '' : 'type-badge--danger'}">${escapeHtml(available ? '可用' : '不可用')}</span>
      </div>
      <div class="summary-strip summary-strip--metrics knowledge-status-metrics">
        ${metricCard('知识库后端', backendText, '当前知识库使用的后端类型')}
        ${metricCard('文档数量', countText, '当前知识库后端中的文档总数')}
        ${metricCard('记忆后端', memoryText, '当前长期记忆使用的后端状态')}
      </div>
      <div class="info-card"><h4>向量库路径</h4><p>${escapeHtml(pathText)}</p></div>
      ${embeddingHtml}
      ${detailHtml}
      ${uploadTypes.length ? `<div><div class="meta-line">支持上传格式</div>${tags(uploadTypes)}</div>` : ''}
      ${summaryHtml}
      ${errorHtml}
    </section>
  `;
}

// 渲染知识库顶部状态条。
function knowledgeStatusBarView(payload = {}) {
  const available = Boolean(payload.available);
  const dot = available ? '✅' : '⚠️';
  const count = num(payload.document_count || 0);
  const lastUpdated = payload.last_updated_at || '未知';
  const backendText = payload.backend || 'disabled';
  return `${dot} 知识库${available ? '可用' : '不可用'} | 后端：${escapeHtml(backendText)} | 当前文档总数：${count} | 最后更新时间：${escapeHtml(lastUpdated)}`;
}

// 把知识库文档列表渲染成可读卡片。
function knowledgeDocumentsView(items = [], options = {}) {
  const title = options.title || '知识库内容';
  const note = options.note || '';
  const query = options.query || '';
  const docs = groupKnowledgeDocuments(items);
  if (!docs.length) {
    return infoCard(title, note || '当前没有可展示的知识库内容。');
  }
  return `
    <section class="copy-block">
      <div class="block-title">
        <div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(note || `共展示 ${docs.length} 条结果`)}</p></div>
        <span class="type-badge">共 ${docs.length} 条</span>
      </div>
      <div class="knowledge-doc-list">
        ${docs.map((item, index) => {
          const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
          const payload = parseKnowledgeStructuredPayload(item.text || '');
          const parsedId = parseKnowledgeDocumentId(item.id || metadata.document_id || '');
          const docTitle = knowledgeDocumentTitle(item, metadata, payload);
          const docCategory = knowledgeCategoryLabel(metadata, payload, parsedId, query);
          const docSource = knowledgeSourceLabel(metadata, payload, parsedId);
          const docBvid = String(metadata.bvid || payload?.BVID || parsedId.bvid || '').trim();
          const tagsList = knowledgeDocumentTags(metadata, {
            title: docTitle,
            category: docCategory,
            source: docSource,
            boardType: String(metadata.board_type || payload?.榜单来源 || parsedId.boardType || '').trim(),
            bvid: docBvid,
            filename: String(metadata.filename || '').trim(),
            sourceChannel: String(metadata.source_channel || '').trim(),
          }).slice(0, 6);
          const metaLine = [`DOC ${index + 1}`];
          if (docCategory) {
            metaLine.push(`分类：${docCategory}`);
          } else if (docSource) {
            metaLine.push(`来源：${docSource}`);
          }
          return `
            <article class="knowledge-doc-item">
              <div class="block-title">
                <div>
                  <div class="meta-line">${escapeHtml(metaLine.join(' · '))}</div>
                  <h4>${escapeHtml(docTitle)}</h4>
                </div>
                <div class="knowledge-doc-item__actions">
                  ${item.score !== undefined ? `<span class="result-badge">相关性 ${escapeHtml(String(Number(item.score).toFixed(4)))}</span>` : ''}
                  <button class="copy-btn" type="button" data-copy="${escapeHtml(item.text || '')}" data-copy-label="知识内容">复制内容</button>
                </div>
              </div>
              ${tagsList.length ? tags(tagsList) : '<p class="section-note">暂无元数据</p>'}
              <pre>${escapeHtml(item.text || '')}</pre>
            </article>
          `;
        }).join('')}
      </div>
    </section>
  `;
}

// 渲染知识库顶部状态条。
function renderKnowledgeStatusBar(payload = {}) {
  const box = $('#knowledgeStatusBar');
  if (!box) return;
  box.innerHTML = knowledgeStatusBarView(payload);
}

// 统一渲染知识库结果展示区。
function renderKnowledgeResult(html, tab = state.knowledgeActiveSubtab) {
  state.knowledgeResults[tab] = html;
  if (state.knowledgeActiveSubtab !== tab) return;
  const box = $('#knowledgeStageResult');
  if (!box) return;
  box.innerHTML = html;
  bindCopyButtons(box);
}

// 读取当前知识库查看条数。
function knowledgeViewLimit() {
  return Math.max(1, Math.min(20, Number(state.knowledgeForm.viewLimit || 6) || 6));
}

// 把知识库输入框里的当前值同步到前端状态，避免切换子 Tab 时丢失。
function syncKnowledgeFormStateFromDom() {
  const updateLimitInput = $('#knowledgeUpdateLimit');
  const viewLimitInput = $('#knowledgeViewLimit');
  const searchInput = $('#knowledgeSearchInput');
  if (updateLimitInput) {
    state.knowledgeForm.updateLimit = Math.max(1, Math.min(20, Number(updateLimitInput.value || 10) || 10));
  }
  if (viewLimitInput) {
    state.knowledgeForm.viewLimit = Math.max(1, Math.min(20, Number(viewLimitInput.value || 6) || 6));
  }
  if (searchInput) {
    state.knowledgeForm.searchQuery = searchInput.value || '';
  }
}

// 生成知识库检索下拉选项，保证当前值也能被正确回显。
function knowledgeSearchOptionsHtml() {
  const current = String(state.knowledgeForm.searchQuery || '').trim();
  const options = current && !KNOWLEDGE_SEARCH_OPTIONS.includes(current)
    ? [current, ...KNOWLEDGE_SEARCH_OPTIONS]
    : KNOWLEDGE_SEARCH_OPTIONS.slice();
  return [
    '<option value="">请选择检索分类</option>',
    ...options.map(option => `<option value="${escapeHtml(option)}"${option === current ? ' selected' : ''}>${escapeHtml(option)}</option>`),
  ].join('');
}

// 根据当前子 Tab 渲染唯一的知识库操作面板，确保按钮一对一不混用。
function knowledgeActionPane(tab = state.knowledgeActiveSubtab) {
  if (tab === 'sync') {
    return `
      <div class="knowledge-pane is-active">
        <div class="knowledge-action-row">
          <label class="field knowledge-field">
            <span class="field__label">榜单抓取条数</span>
            <input id="knowledgeUpdateLimit" type="number" min="1" max="20" value="${escapeHtml(String(state.knowledgeForm.updateLimit || 10))}" />
          </label>
          <button class="action-btn action-btn--primary action-btn--inline" id="knowledgeUpdateBtn" type="button">
            <span class="action-btn__title">自动更新热门知识库</span>
            <span class="action-btn__desc">同步并去重更新</span>
          </button>
        </div>
        <div class="field__hint">每次更新只追加/更新最新热门样本，自动去重，不清空历史数据。</div>
      </div>
    `;
  }
  if (tab === 'view') {
    return `
      <div class="knowledge-pane is-active">
        <div class="knowledge-action-row">
          <label class="field knowledge-field">
            <span class="field__label">知识库查看条数</span>
            <input id="knowledgeViewLimit" type="number" min="1" max="20" value="${escapeHtml(String(state.knowledgeForm.viewLimit || 6))}" />
          </label>
          <button class="action-btn action-btn--primary action-btn--inline" id="knowledgeSampleBtn" type="button">
            <span class="action-btn__title">查看知识库内容</span>
            <span class="action-btn__desc">读取最新文档</span>
          </button>
        </div>
        <div class="field__hint">展示向量库中最新的 N 条原始文档。</div>
      </div>
    `;
  }
  if (tab === 'search') {
    return `
      <div class="knowledge-pane is-active">
        <div class="knowledge-action-row">
          <label class="field knowledge-field">
            <span class="field__label">知识库检索关键词</span>
            <select id="knowledgeSearchInput">
              ${knowledgeSearchOptionsHtml()}
            </select>
          </label>
          <button class="action-btn action-btn--primary action-btn--inline" id="knowledgeSearchBtn" type="button">
            <span class="action-btn__title">按关键词检索知识库</span>
            <span class="action-btn__desc">执行语义检索</span>
          </button>
        </div>
        <div class="field__hint">点击下拉框选择一级分类，再检索知识库。</div>
      </div>
    `;
  }
  return `
    <div class="knowledge-pane is-active">
      <div class="knowledge-action-row">
        <label class="field knowledge-field">
          <span class="field__label">选择文件</span>
          <input id="knowledgeFileInput" type="file" accept=".txt,.md,.docx,.pdf" />
        </label>
        <button class="action-btn action-btn--primary action-btn--inline" id="knowledgeUploadBtn" type="button">
          <span class="action-btn__title">上传到知识库</span>
          <span class="action-btn__desc">自动读取并入库</span>
        </button>
      </div>
      <div class="field__hint">支持 txt / md / docx / pdf，自动读取、清洗、切片并写入 Chroma 向量库。</div>
    </div>
  `;
}

// 渲染知识库操作区，当前子 Tab 只保留自己的专属按钮。
function renderKnowledgeActionPane(tab = state.knowledgeActiveSubtab) {
  const host = $('#knowledgeActionHost');
  if (!host) return;
  host.innerHTML = knowledgeActionPane(tab);
}

// 切换知识库子 Tab。
function setKnowledgeSubtab(tab) {
  const next = ['upload', 'sync', 'view', 'search'].includes(tab) ? tab : 'upload';
  syncKnowledgeFormStateFromDom();
  state.knowledgeActiveSubtab = next;
  $$('[data-knowledge-subtab]').forEach(button => {
    button.classList.toggle('is-active', button.dataset.knowledgeSubtab === next);
  });
  renderKnowledgeActionPane(next);
  if (next === 'sync') {
    syncKnowledgeUpdateButtonState();
  }
  const box = $('#knowledgeStageResult');
  if (!box) return;
  box.innerHTML = state.knowledgeResults[next] || knowledgePlaceholder(next);
  bindCopyButtons(box);
}

// 各知识库子 Tab 的默认占位内容。
function knowledgePlaceholder(tab) {
  if (tab === 'upload') return infoCard('等待上传', '选择本地资料文件后，点击“上传到知识库”，结果会在这里显示。');
  if (tab === 'sync') return infoCard('等待同步', '点击“自动更新热门知识库”后，这里会展示各榜单写入情况和完整 Chroma 状态总览。');
  if (tab === 'view') return infoCard('等待查看', '点击“查看知识库内容”后，这里会展示向量库中的最新文档。');
  if (tab === 'search') return infoCard('等待检索', '输入关键词并点击“按关键词检索知识库”后，这里会展示命中的文档和相关性。');
  return infoCard('等待操作', '请选择知识库子功能。');
}

// 生成“同步热门样本”子 Tab 的默认总览结果。
function knowledgeSyncDefaultResult(payload = {}) {
  return knowledgeStatusView(
    payload,
    infoCard('等待同步', '点击“自动更新热门知识库”后，这里会展示各榜单写入情况和完整 Chroma 状态总览。'),
  );
}

function knowledgeUpdateBoardSummary(result = {}) {
  const boards = Array.isArray(result.boards) ? result.boards : [];
  if (!boards.length) return '';
  return `<div class="summary-strip summary-strip--metrics">${boards.map(item => metricCard(item.board_type || '榜单', `写入 ${num(item.saved_count || 0)}`, `覆盖 ${num(item.updated_count || 0)} / 失败 ${Array.isArray(item.failed) ? item.failed.length : 0}`)).join('')}</div>`;
}

function knowledgeUpdateSummaryHtml(job = {}) {
  const result = job.result || {};
  return `
    ${infoCard('更新完成', `本次共写入 ${result.total_saved || 0} 条热门样本，其中覆盖更新 ${result.total_updated || 0} 条，失败 ${result.total_failed || 0} 条。`)}
    ${knowledgeUpdateBoardSummary(result)}
  `;
}

function knowledgeUpdateProgressView(job = {}) {
  const percent = clampProgressValue(job.percent ?? 0);
  const message = job.message || '系统正在抓取全站热门榜、分区热门榜、每周必看和入站必刷。';
  const boardText = job.board_type || '等待分配榜单';
  const itemText = job.current_title || '等待处理样本';
  return `
    ${loadingCard('正在更新热门知识库', message, ['准备任务', '抓取榜单', '同步样本', '完成更新'], percent)}
    <div class="summary-strip summary-strip--metrics">
      ${metricCard('实时进度', formatProgressLabel(percent), '按真实抓取与入库进度更新')}
      ${metricCard('榜单进度', `${num(job.processed_boards || 0)} / ${num(job.total_boards || 0)}`, '已完成榜单数 / 总榜单数')}
      ${metricCard('样本进度', `${num(job.processed_items || 0)} / ${num(job.total_items || 0)}`, '已同步样本数 / 预计总样本数')}
    </div>
    <div class="summary-strip">
      <div class="info-card"><h4>当前榜单</h4><p>${escapeHtml(boardText)}</p></div>
      <div class="info-card"><h4>当前样本</h4><p>${escapeHtml(itemText)}</p></div>
    </div>
  `;
}

function stopKnowledgeUpdatePolling() {
  if (state.knowledgeUpdateTask?.timer) {
    window.clearTimeout(state.knowledgeUpdateTask.timer);
  }
  state.knowledgeUpdateTask = null;
}

function syncKnowledgeUpdateButtonState() {
  const job = state.knowledgeUpdateTask?.job || null;
  if (!job || !['queued', 'running'].includes(job.status)) {
    setActionButtonLoading('knowledgeUpdateBtn', false);
    return;
  }
  const desc = `${formatProgressLabel(job.percent ?? 0)} · ${job.message || '正在抓取并去重'}`;
  setActionButtonLoading('knowledgeUpdateBtn', true, '更新中', desc);
}

function renderKnowledgeUpdateJob(job = {}) {
  state.knowledgeUpdateTask = {
    ...(state.knowledgeUpdateTask || {}),
    jobId: job.id || state.knowledgeUpdateTask?.jobId || '',
    job,
    timer: state.knowledgeUpdateTask?.timer || null,
  };
  renderKnowledgeResult(knowledgeUpdateProgressView(job), 'sync');
  syncKnowledgeUpdateButtonState();
}

function finishKnowledgeUpdateJob(job = {}, options = {}) {
  const knowledgeStatus = job.knowledge_status || state.knowledgeStatus || {};
  state.knowledgeStatus = knowledgeStatus;
  renderKnowledgeStatusBar(knowledgeStatus);
  renderKnowledgeResult(knowledgeStatusView(knowledgeStatus, knowledgeUpdateSummaryHtml(job)), 'sync');
  stopKnowledgeUpdatePolling();
  syncKnowledgeUpdateButtonState();
  setStatus('知识库已更新', 'success');
  if (!options.silent) {
    const result = job.result || {};
    showToast('更新成功', `本次写入 ${result.total_saved || 0} 条热门样本`);
  }
}

function failKnowledgeUpdateJob(job = {}, errorMessage = '', options = {}) {
  const detail = errorMessage || job.message || job.error || '知识库更新失败';
  const progressHtml = job && (job.percent || job.processed_items || job.processed_boards)
    ? knowledgeUpdateProgressView(job)
    : '';
  renderKnowledgeResult(`${progressHtml}${infoCard('知识库更新失败', detail, 'danger')}`, 'sync');
  stopKnowledgeUpdatePolling();
  syncKnowledgeUpdateButtonState();
  setStatus('知识库更新失败', 'error');
  if (!options.silent) {
    showToast('更新失败', detail, 'error');
  }
}

function scheduleKnowledgeUpdatePoll(delay = 900) {
  if (!state.knowledgeUpdateTask?.jobId) return;
  if (state.knowledgeUpdateTask.timer) {
    window.clearTimeout(state.knowledgeUpdateTask.timer);
  }
  state.knowledgeUpdateTask.timer = window.setTimeout(() => {
    pollKnowledgeUpdateJob(state.knowledgeUpdateTask?.jobId || '', { silent: state.knowledgeUpdateTask?.silent });
  }, delay);
}

async function pollKnowledgeUpdateJob(jobId, options = {}) {
  if (!jobId) return;
  try {
    const job = await requestGetJson(`/api/knowledge/update/${encodeURIComponent(jobId)}`);
    if (!state.knowledgeUpdateTask || state.knowledgeUpdateTask.jobId !== jobId) return;
    state.knowledgeUpdateTask.job = job;
    if (job.status === 'completed') {
      finishKnowledgeUpdateJob(job, options);
      return;
    }
    if (job.status === 'failed') {
      failKnowledgeUpdateJob(job, job.message || job.error || '', options);
      return;
    }
    renderKnowledgeUpdateJob(job);
    setStatus(job.message || '正在更新知识库', 'loading');
    scheduleKnowledgeUpdatePoll();
  } catch (error) {
    if (!state.knowledgeUpdateTask || state.knowledgeUpdateTask.jobId !== jobId) return;
    failKnowledgeUpdateJob(state.knowledgeUpdateTask.job || {}, `进度读取失败：${error.message}`, options);
  }
}

function resumeKnowledgeUpdateJob(job = {}, options = {}) {
  if (!job?.id) return;
  stopKnowledgeUpdatePolling();
  state.knowledgeUpdateTask = {
    jobId: job.id,
    job,
    timer: null,
    silent: Boolean(options.silent),
  };
  renderKnowledgeUpdateJob(job);
  setStatus(job.message || '正在更新知识库', 'loading');
  scheduleKnowledgeUpdatePoll(options.immediate ? 0 : 900);
}

// 页面加载时读取知识库状态。
async function loadKnowledgeBaseStatus() {
  if (!$('#knowledgeStatusBar')) return;
  try {
    const data = await requestGetJson('/api/knowledge/status');
    state.knowledgeStatus = data;
    renderKnowledgeStatusBar(data);
    if (data.active_update_job?.id && ['queued', 'running'].includes(data.active_update_job.status)) {
      resumeKnowledgeUpdateJob(data.active_update_job, { silent: true });
    } else {
      state.knowledgeResults.sync = knowledgeSyncDefaultResult(data);
      if (state.knowledgeActiveSubtab === 'sync') {
        renderKnowledgeResult(state.knowledgeResults.sync, 'sync');
      }
    }
  } catch (error) {
    renderKnowledgeStatusBar({ available: false, document_count: 0, last_updated_at: '读取失败' });
    state.knowledgeResults.sync = infoCard('知识库状态读取失败', error.message || '请检查后端服务', 'danger');
    if (state.knowledgeActiveSubtab === 'sync') {
      renderKnowledgeResult(state.knowledgeResults.sync, 'sync');
    }
  }
}

// 读取知识库中的样本文档并展示。
async function loadKnowledgeSamples() {
  syncKnowledgeFormStateFromDom();
  const limit = knowledgeViewLimit();
  setActionButtonLoading('knowledgeSampleBtn', true, '读取中', '正在拉取样本文档');
  renderKnowledgeResult(loadingCard('正在读取知识库内容', '系统正在从当前 Chroma 向量库读取样本文档。', ['读取状态', '拉取文档', '渲染结果']), 'view');
  try {
    const data = await requestGetJson(`/api/knowledge/sample?limit=${encodeURIComponent(limit)}`);
    renderKnowledgeResult(
      knowledgeDocumentsView(data.items || [], {
        title: '知识库样本文档',
        note: `当前展示知识库中的前 ${limit} 条样本文档。`,
      }),
      'view',
    );
    showToast('读取完成', `当前已展示 ${Array.isArray(data.items) ? data.items.length : 0} 条知识库文档`);
  } catch (error) {
    renderKnowledgeResult(infoCard('读取知识库内容失败', error.message, 'danger'), 'view');
    showToast('读取失败', error.message, 'error');
  } finally {
    setActionButtonLoading('knowledgeSampleBtn', false);
  }
}

// 按关键词检索知识库并展示命中结果。
async function searchKnowledgeContent() {
  syncKnowledgeFormStateFromDom();
  const query = ($('#knowledgeSearchInput')?.value || '').trim();
  if (!query) {
    showToast('缺少关键词', '请输入知识库检索关键词。', 'error');
    return;
  }
  const limit = knowledgeViewLimit();
  setActionButtonLoading('knowledgeSearchBtn', true, '检索中', '正在执行语义检索');
  renderKnowledgeResult(loadingCard('正在检索知识库', '系统正在基于当前关键词执行语义检索。', ['发送查询', '执行检索', '渲染命中']), 'search');
  try {
    const data = await requestGetJson(`/api/knowledge/search?q=${encodeURIComponent(query)}&limit=${encodeURIComponent(limit)}`);
    const displayQuery = knowledgeMajorTopicLabel(query) || query;
    renderKnowledgeResult(
      knowledgeDocumentsView(data.matches || [], {
        title: `检索结果：${displayQuery}`,
        note: `${displayQuery !== query ? '当前分类' : '当前关键词'}共返回 ${Array.isArray(data.matches) ? data.matches.length : 0} 条命中结果。`,
        query,
      }),
      'search',
    );
    showToast('检索完成', `关键词「${query}」已返回 ${Array.isArray(data.matches) ? data.matches.length : 0} 条结果`);
  } catch (error) {
    renderKnowledgeResult(infoCard('检索知识库失败', error.message, 'danger'), 'search');
    showToast('检索失败', error.message, 'error');
  } finally {
    setActionButtonLoading('knowledgeSearchBtn', false);
  }
}

// 更新知识库按钮文字和 loading 状态。
function setActionButtonLoading(id, loading, loadingTitle = '处理中', loadingDesc = '请稍候') {
  const button = document.getElementById(id);
  if (!button) return;
  const title = button.querySelector('.action-btn__title');
  const desc = button.querySelector('.action-btn__desc');
  if (title && !button.dataset.defaultTitle) button.dataset.defaultTitle = title.textContent || '';
  if (desc && !button.dataset.defaultDesc) button.dataset.defaultDesc = desc.textContent || '';
  setButtonLoading(id, loading);
  if (title) title.textContent = loading ? loadingTitle : (button.dataset.defaultTitle || title.textContent || '');
  if (desc) desc.textContent = loading ? loadingDesc : (button.dataset.defaultDesc || desc.textContent || '');
}

// 上传知识文件到 Chroma。
async function uploadKnowledgeFile() {
  const fileInput = $('#knowledgeFileInput');
  if (!fileInput?.files?.length) {
    showToast('缺少文件', '请先选择要导入知识库的文件。', 'error');
    return;
  }

  const file = fileInput.files[0];
  const formData = new FormData();
  formData.append('file', file);

  setActionButtonLoading('knowledgeUploadBtn', true, '上传中', '正在读取并切片');
  renderKnowledgeResult(loadingCard('正在导入知识文件', '系统会读取文件内容、清洗文本、切片并写入 Chroma 向量库。', ['读取文件', '清洗文本', '切片入库']), 'upload');
  setStatus('正在导入知识文件', 'loading');

  try {
    const data = await requestFormData('/api/knowledge/upload', formData);
    const result = data.upload_result || {};
    state.knowledgeStatus = data.knowledge_status || state.knowledgeStatus;
    renderKnowledgeStatusBar(state.knowledgeStatus || {});
    renderKnowledgeResult(
      `
        ${infoCard('上传完成', `文件 ${result.filename || file.name} 已写入知识库，文档 ID 为 ${result.document_id || '未知'}，切片数量 ${result.chunk_count ?? 0}。`)}
        ${knowledgeStatusView(data.knowledge_status || {}, '')}
      `,
      'upload',
    );
    fileInput.value = '';
    setStatus('知识文件已导入', 'success');
    showToast('导入成功', `${result.filename || file.name} 已写入知识库`);
  } catch (error) {
    renderKnowledgeResult(infoCard('知识库导入失败', error.message, 'danger'), 'upload');
    setStatus('知识库导入失败', 'error');
    showToast('导入失败', error.message, 'error');
  } finally {
    setActionButtonLoading('knowledgeUploadBtn', false);
  }
}

// 重新抓取热门榜并追加更新 Chroma。
async function updateKnowledgeBase() {
  syncKnowledgeFormStateFromDom();
  const limit = Math.max(1, Math.min(20, Number(state.knowledgeForm.updateLimit || 10) || 10));

  try {
    const data = await requestJson('/api/knowledge/update/start', { limit });
    const job = data.job || {};
    if (!job.id) {
      throw new Error('知识库更新任务启动失败，未返回任务 ID。');
    }
    resumeKnowledgeUpdateJob(job, { silent: Boolean(data.already_running), immediate: true });
    if (data.already_running) {
      showToast('任务继续执行中', '已有热门知识库更新任务在运行，已切换到当前实时进度。');
    }
  } catch (error) {
    failKnowledgeUpdateJob(state.knowledgeUpdateTask?.job || {}, error.message);
  }
}

// 生成视频预解析区域里的单个信息卡片。
function previewCard(label, value, hint = '根据视频链接自动解析') {
  const ok = value !== undefined && value !== null && String(value).trim() !== '';
  return `<div class="stat-card preview-card" title="${escapeHtml(hint)}"><h4>${escapeHtml(label)}</h4><span class="stat-card__value ${ok ? '' : 'is-placeholder'}">${ok ? escapeHtml(value) : '待解析'}</span></div>`;
}

// 生成指标展示卡片。
function metricCard(label, value, hint = '') {
  return `<div class="stat-card" title="${escapeHtml(hint)}"><h4>${escapeHtml(label)}</h4><span class="stat-card__value">${escapeHtml(value)}</span>${hint ? `<p>${escapeHtml(hint)}</p>` : ''}</div>`;
}

// 把选题结果渲染成前端展示卡片。
function renderIdeas(topicResult) {
  const ideas = topicResult?.ideas || [];
  if (!ideas.length) return infoCard('暂无选题建议', '当前没有可展示的选题结果。');
  return `<div class="topic-grid">${ideas.map((idea, index) => `
    <article class="topic-card">
      <div class="topic-card__head">
        <div><div class="meta-line">TOP ${index + 1}</div><h4>${escapeHtml(idea.topic || '未命名选题')}</h4></div>
        <span class="type-badge">${escapeHtml(idea.video_type || '干货')}</span>
      </div>
      <p>${escapeHtml(idea.reason || '')}</p>
      ${tags(idea.keywords || [])}
    </article>
  `).join('')}</div>`;
}

// 把参考视频列表渲染成可点击的卡片网格。
function referenceGrid(items = [], compact = false) {
  const list = Array.isArray(items) ? items.filter(item => item && item.url) : [];
  if (!list.length) return '';
  return `<div class="reference-grid ${compact ? 'reference-grid--chat' : ''}">${list.map(item => {
    const cover = coverUrl(item.cover);
    const title = item.title || '未命名视频';
    const viewText = hasMetricValue(item.view) ? num(item.view) : '暂缺';
    const likeText = hasMetricValue(item.like) ? num(item.like) : '暂缺';
    return `
      <a class="reference-card ${compact ? 'reference-card--chat' : ''}" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">
        <div class="reference-card__thumb">${renderCoverMedia(cover, title, compact ? 'reference-chat' : 'reference')}</div>
        <div class="reference-card__body">
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(item.author || '未知 UP')}</p>
          <div class="reference-card__meta"><span>播放 ${viewText}</span><span>点赞 ${likeText}</span></div>
        </div>
      </a>
    `;
  }).join('')}</div>`;
}

// 生成参考视频区块，统一处理空状态和说明文案。
function referenceSection(items = [], title = '可直接参考的高表现视频', desc = '点击卡片可直接打开当前做得好的视频页面。') {
  const grid = referenceGrid(items, false);
  return `
    <section class="copy-block" id="videoReferenceSection">
      <div class="block-title"><div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(desc)}</p></div></div>
      ${grid || '<div class="info-card"><h4>暂未找到强相关参考视频</h4><p>当前已优先按视频标题和主题检索同题材高表现视频；如果这一区块为空，通常是公开搜索结果过少或题材过窄。</p></div>'}
    </section>
  `;
}

// 把标题、脚本、简介和置顶评论渲染成可复制的文案结果。
function copyResult(copy) {
  if (!copy) return infoCard('暂无可直接复用文案', '当前结果里没有新的标题、脚本和简介。');
  const titles = Array.isArray(copy.titles) ? copy.titles.filter(Boolean) : [];
  const script = Array.isArray(copy.script) ? copy.script.filter(Boolean) : [];
  const description = copy.description || '';
  const tagList = Array.isArray(copy.tags) ? copy.tags.filter(Boolean) : [];
  const pinned = copy.pinned_comment || '';
  return `
    <div class="copy-layout">
      <section class="copy-block">
        <div class="block-title">
          <div><h4>高流量标题</h4><p>结合当前方向自动生成的标题备选</p></div>
          <button class="copy-btn" data-copy="${escapeHtml(titles.join('\n'))}" data-copy-label="标题集合">一键复制</button>
        </div>
        <div class="copy-title-grid">
          ${titles.length ? titles.map((title, index) => `
            <article class="copy-card">
              <div class="card-head">
                <div><div class="meta-line">标题 ${index + 1}</div><h4>${escapeHtml(title)}</h4></div>
                <button class="copy-btn" data-copy="${escapeHtml(title)}" data-copy-label="标题 ${index + 1}">复制</button>
              </div>
            </article>
          `).join('') : infoCard('暂无标题', '当前没有可展示的标题。')}
        </div>
      </section>
      <section class="copy-block">
        <div class="block-title">
          <div><h4>文案脚本</h4><p>可直接拆成视频段落使用</p></div>
          <button class="copy-btn" data-copy="${escapeHtml(script.map(item => `[${item.duration || ''}] ${item.section || ''}: ${item.content || ''}`).join('\n'))}" data-copy-label="完整脚本">一键复制</button>
        </div>
        <div class="script-list">
          ${script.length ? script.map((item, index) => `
            <article class="script-item">
              <div class="script-item__meta">
                <div class="script-item__title"><span class="script-item__index">${index + 1}</span><strong>${escapeHtml(item.section || '片段')}</strong></div>
                <span class="script-item__time">${escapeHtml(item.duration || '')}</span>
              </div>
              <p>${escapeHtml(item.content || '')}</p>
              <button class="copy-btn" data-copy="${escapeHtml(item.content || '')}" data-copy-label="脚本片段 ${index + 1}">复制片段</button>
            </article>
          `).join('') : infoCard('暂无脚本', '当前没有可展示的脚本。')}
        </div>
      </section>
      <section class="copy-block">
        <div class="block-title"><div><h4>简介与标签</h4><p>适合直接放到发布页里</p></div></div>
        <article class="copy-card">
          <div class="card-head">
            <div><div class="meta-line">视频简介</div><h4>简介文案</h4></div>
            <button class="copy-btn" data-copy="${escapeHtml(description)}" data-copy-label="视频简介">复制简介</button>
          </div>
          <p>${escapeHtml(description || '暂无简介')}</p>
          <div class="spacer-xs"></div>
          ${tags(tagList)}
        </article>
      </section>
      <section class="copy-block">
        <div class="block-title">
          <div><h4>置顶评论</h4><p>用于引导互动和收集下一期方向</p></div>
          <button class="copy-btn" data-copy="${escapeHtml(pinned)}" data-copy-label="置顶评论">复制评论</button>
        </div>
        <article class="dark-card"><p class="rich-text">${rich(pinned || '暂无置顶评论')}</p></article>
      </section>
    </div>
  `;
}

// 组装内容创作模块的完整结果视图。
function creatorResult(data) {
  const profile = data.normalized_profile || data.seed_topic || '未整理';
  const question = data.seed_topic || profile || '未整理';
  return `
    <div class="result-stack">
      ${data.llm_warning ? `<div class="inline-notice">${escapeHtml(data.llm_warning)}</div>` : ''}
      <div class="summary-strip">
        <div class="stat-card"><h4>整理后的方向</h4><span class="stat-card__value">${escapeHtml(profile)}</span></div>
        <div class="stat-card"><h4>当前问题</h4><span class="stat-card__value">${escapeHtml(question)}</span></div>
        <div class="stat-card"><h4>推荐主选题</h4><span class="stat-card__value">${escapeHtml(data.chosen_topic || '暂无')}</span></div>
        <div class="stat-card"><h4>文案风格</h4><span class="stat-card__value">${escapeHtml(data.style || '干货')}</span></div>
      </div>
      <section class="copy-block" id="creatorTopicsSection">
        <div class="block-title"><div><h4>热门选题建议</h4><p>基于你的方向和当前热门结构，自动整理出更值得做的切口。</p></div></div>
        ${renderIdeas(data.topic_result)}
      </section>
      <section class="copy-block" id="creatorCopySection">
        <div class="block-title"><div><h4>自动生成文案</h4><p>围绕主选题生成标题、脚本、简介和置顶评论。</p></div></div>
        ${copyResult(data.copy_result)}
      </section>
    </div>
  `;
}

// 渲染视频解析预览区，展示标题、封面和关键指标。
function videoPreview(data, options = {}) {
  const resolved = data || {};
  const stats = resolved.stats || {};
  const loading = Boolean(options.loading);
  const error = options.error || '';
  const title = loading ? '正在自动解析视频信息' : error ? '视频链接解析失败' : data ? '已自动解析当前视频信息' : '当前视频信息预览';
  const note = loading
    ? '系统正在根据你输入的 B 站视频链接提取标题、分区、UP 主和互动数据。'
    : error
      ? error
      : data
        ? '这些字段来自当前视频链接的自动解析结果，点击下方按钮会基于这些真实信息继续分析。'
        : '粘贴视频链接后，这里会自动显示标题、类型、播放、点赞、投币、收藏、评论和分享。';
  const cover = coverUrl(resolved.cover);
  return `
    <section class="copy-block" id="videoPreviewSection">
      <div class="block-title">
        <div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(note)}</p></div>
        <span class="type-badge ${error ? 'type-badge--danger' : ''}">${loading ? '自动解析中' : data ? '已解析' : '待解析'}</span>
      </div>
      ${loading ? '<div class="bili-progress"><div class="bili-progress__bar bili-progress__bar--indeterminate"></div></div>' : ''}
      ${cover ? `<div class="video-cover-strip"><div class="video-cover-strip__thumb">${renderCoverMedia(cover, resolved.title || '视频封面', 'strip')}</div><div class="video-cover-strip__body"><strong>${escapeHtml(resolved.title || '当前视频')}</strong><span>${escapeHtml(resolved.up_name || '自动解析结果')}</span></div></div>` : ''}
      <div class="summary-strip">
        ${previewCard('视频标题', resolved.title || '', '根据视频链接自动解析当前视频标题')}
        ${previewCard('视频类型', resolved.partition_label || resolved.partition || '', '根据视频链接自动解析分区和视频类型')}
        ${previewCard('UP 主', resolved.up_name || '', '根据视频链接自动解析对应 UP 主')}
        ${previewCard('BV 号', resolved.bv_id || '', '根据视频链接自动解析对应 BV 号')}
      </div>
      <div class="summary-strip summary-strip--metrics">
        ${previewCard('播放量', data ? num(stats.view) : '', '根据视频链接自动解析公开播放量')}
        ${previewCard('点赞量', data ? num(stats.like) : '', '根据视频链接自动解析公开点赞量')}
        ${previewCard('投币量', data ? num(stats.coin) : '', '根据视频链接自动解析公开投币量')}
        ${previewCard('收藏量', data ? num(stats.favorite) : '', '根据视频链接自动解析公开收藏量')}
        ${previewCard('评论量', data ? num(stats.reply) : '', '根据视频链接自动解析公开评论量')}
        ${previewCard('分享量', data ? num(stats.share) : '', '根据视频链接自动解析公开分享量')}
      </div>
    </section>
  `;
}

// 渲染视频核心指标摘要卡片。
function videoMetrics(resolved) {
  const stats = resolved?.stats || {};
  return `
    <div class="summary-strip summary-strip--metrics">
      ${metricCard('播放', num(stats.view), '当前公开播放数据')}
      ${metricCard('点赞', num(stats.like), `点赞率 ${pct(stats.like_rate)}`)}
      ${metricCard('投币', num(stats.coin), '反映内容认可度')}
      ${metricCard('收藏', num(stats.favorite), '反映内容留存和收藏意愿')}
      ${metricCard('评论', num(stats.reply), '反映讨论反馈')}
      ${metricCard('分享', num(stats.share), '反映传播意愿')}
    </div>
  `;
}

// 把分析建议列表渲染成统一的要点样式。
function bulletList(items = []) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) return infoCard('暂无内容', '当前没有可展示的分析项。');
  return `<div class="analysis-list">${list.map(item => `<article class="analysis-item"><span class="analysis-item__dot"></span><p>${escapeHtml(item)}</p></article>`).join('')}</div>`;
}

// 生成助手推荐追问按钮区域。
function assistantActionButtons(items = []) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) return '';
  return `
    <div class="assistant-actions-card">
      <div class="assistant-actions-title">相关问题</div>
      <div class="assistant-action-grid">
      ${list.map(item => `
        <button
          class="copy-btn assistant-question-btn"
          type="button"
          data-assistant-action="${escapeHtml(item)}"
        ><span class="assistant-question-btn__dot"></span><span>${escapeHtml(item)}</span></button>
      `).join('')}
      </div>
    </div>
  `;
}

// 渲染视频分析结果里的后续选题区块。
function topicSection(items = [], title = '后续可做方向', desc = '') {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) return '';
  return `
    <section class="copy-block" id="videoTopicSection">
      <div class="block-title"><div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(desc)}</p></div></div>
      <div class="topic-grid">${list.map((item, index) => `<article class="topic-card topic-card--simple"><div class="meta-line">方向 ${index + 1}</div><h4>${escapeHtml(item)}</h4></article>`).join('')}</div>
    </section>
  `;
}

// 渲染标题、封面和内容结构的优化建议区块。
function optimizeSection(titleSuggestions = [], coverSuggestion = '', contentSuggestions = []) {
  const titles = Array.isArray(titleSuggestions) ? titleSuggestions.filter(Boolean) : [];
  const content = Array.isArray(contentSuggestions) ? contentSuggestions.filter(Boolean) : [];
  if (!titles.length && !coverSuggestion && !content.length) return '';
  return `
    <section class="copy-block" id="videoOptimizeSection">
      <div class="block-title"><div><h4>具体优化建议</h4><p>从标题、封面和内容结构三个层面给出可执行调整。</p></div></div>
      ${titles.length ? `<article class="copy-card"><div class="card-head"><div><div class="meta-line">标题优化</div><h4>建议替换标题</h4></div><button class="copy-btn" data-copy="${escapeHtml(titles.join('\n'))}" data-copy-label="优化标题">复制标题</button></div>${bulletList(titles)}</article>` : ''}
      ${coverSuggestion ? `<article class="copy-card"><div class="card-head"><div><div class="meta-line">封面优化</div><h4>封面建议</h4></div></div><p>${escapeHtml(coverSuggestion)}</p></article>` : ''}
      ${content.length ? `<article class="copy-card"><div class="card-head"><div><div class="meta-line">内容结构优化</div><h4>内容建议</h4></div></div>${bulletList(content)}</article>` : ''}
    </section>
  `;
}

// 组装视频分析模块的完整结果视图。
function videoResult(data) {
  const resolved = data.resolved || state.videoResolved || {};
  const perf = data.performance || {};
  const analysis = data.analysis || {};
  const optimize = data.optimize_result || {};
  const topics = perf.is_hot ? analysis.followup_topics : analysis.next_topics;
  const titleSuggestions = analysis.title_suggestions || optimize.optimized_titles || [];
  const coverSuggestion = analysis.cover_suggestion || optimize.cover_suggestion || '';
  const contentSuggestions = analysis.content_suggestions || optimize.content_suggestions || [];
  const points = analysis.analysis_points || perf.reasons || [];
  return `
    <div class="result-stack">
      ${data.llm_warning ? `<div class="inline-notice">${escapeHtml(data.llm_warning)}</div>` : ''}
      <section class="performance-hero ${perf.is_hot ? 'performance-hero--hot' : 'performance-hero--low'}" id="videoPerformanceSection">
        <div><div class="meta-line">当前判断</div><h3>${escapeHtml(perf.label || '待判断')}</h3><p>${escapeHtml(perf.summary || '')}</p></div>
        <div class="performance-hero__side"><span class="performance-badge">${perf.is_hot ? '更像爆款' : '播放偏低'}</span><span class="performance-score">得分 ${escapeHtml(String(perf.score ?? 0))}</span></div>
      </section>
      <section class="copy-block" id="videoCoreInfoSection"><div class="block-title"><div><h4>当前视频核心信息</h4><p>以下数据来自当前视频链接自动解析结果。</p></div></div>${videoMetrics(resolved)}</section>
      <section class="copy-block" id="videoReasonSection">
        <div class="block-title"><div><h4>${perf.is_hot ? '为什么这条视频容易火' : '为什么这条视频目前表现偏弱'}</h4><p>${perf.is_hot ? '从标题、互动率和赛道匹配度拆解原因。' : '先找出当前短板，再决定后续优化动作。'}</p></div></div>
        ${bulletList(points)}
      </section>
      ${topicSection(topics, perf.is_hot ? '建议继续延展的后续题材' : '建议下一批优先尝试的题材', perf.is_hot ? '可以沿着这条视频的表现结构继续放大。' : '优先从更容易起量的切口重新测试。')}
      ${!perf.is_hot ? optimizeSection(titleSuggestions, coverSuggestion, contentSuggestions) : ''}
      ${data.copy_result ? `<section class="copy-block" id="videoCopySection"><div class="block-title"><div><h4>优化后可直接使用的文案</h4><p>当视频当前表现偏弱时，可直接拿下面这版标题和脚本做新一轮测试。</p></div></div>${copyResult(data.copy_result)}</section>` : ''}
      ${referenceSection(data.reference_videos || [], perf.is_hot ? '同赛道高表现参考视频' : '建议直接对标的参考视频', '点击卡片可直接跳转到当前表现更好的视频页面。')}
    </div>
  `;
}

// 渲染聊天面板的空状态提示。
function assistantEmptyState() {
  return `
    <div class="empty-state">
      <h4>${state.runtime.chatAvailable ? '还没有对话内容' : '当前为无 Key 逻辑模式'}</h4>
      <p>${state.runtime.chatAvailable ? '你可以直接像聊天一样提问，助手会结合当前页面上下文作答。' : '请先在上方运行模式区域开启 LLM Agent 模式，右侧智能助手才会真正进入 Agent 链路。'}</p>
    </div>
  `;
}

// 把后端返回的运行模式信息同步到前端状态对象里。
function applyRuntimePayload(data = {}) {
  state.runtime = {
    ...state.runtime,
    mode: data.mode || 'rules',
    llmEnabled: Boolean(data.llm_enabled),
    chatAvailable: Boolean(data.chat_available),
    switchChecked: Boolean(data.switch_checked),
    hasSavedConfig: Boolean(data.has_saved_llm_config),
    savedConfigSource: data.saved_config_source || '',
    savedProvider: data.saved_provider || '',
    savedModel: data.saved_model || '',
    savedBaseUrl: data.saved_base_url || '',
    savedApiKeyMasked: data.saved_api_key_masked || '',
    requiresConfig: Boolean(data.requires_config),
    modeLabel: data.mode_label || '无 Key 逻辑模式',
    modeTitle: data.mode_title || '当前运行中：无 Key 逻辑模式',
    modeDescription: data.mode_description || '',
    tokenPolicy: data.token_policy || '',
    switchHint: data.switch_hint || '',
    forceConfigPrompt: Boolean(data.force_config_prompt),
    runtimeErrorMessage: data.runtime_error_message || '',
  };
}

// 控制运行模式配置表单的显示和隐藏。
function setRuntimeConfigFormVisible(visible) {
  const form = $('#runtimeConfigForm');
  if (!form) return;
  form.hidden = !visible;
  form.classList.toggle('is-visible', Boolean(visible));
}

// 让运行模式开关做一次震动提示，强调需要用户先开启或配置模式。
function shakeRuntimeModeToggle() {
  const toggle = $('#runtimeModeToggle');
  if (!toggle) return;
  toggle.classList.remove('is-shaking');
  void toggle.offsetWidth;
  toggle.classList.add('is-shaking');
  window.setTimeout(() => toggle.classList.remove('is-shaking'), 450);
}

// 让运行模式配置表单做一次震动提示，强调当前需要用户立即修正配置。
function shakeRuntimeConfigForm() {
  const form = $('#runtimeConfigForm');
  if (!form) return;
  form.classList.remove('is-shaking');
  void form.offsetWidth;
  form.classList.add('is-shaking');
  window.setTimeout(() => form.classList.remove('is-shaking'), 450);
}

// 判断当前错误是否应该直接引导用户重新填写 LLM 配置。
function shouldPromptRuntimeConfig(error) {
  const message = String(error?.message || error || '');
  if (error?.payload?.show_runtime_config) return true;
  return /connection error|api key|鉴权|认证|provider|quota|rate limit|llm/i.test(message);
}

// 在 LLM 配置不可用时，拉起运行模式配置表单并记录待重试动作。
function promptRuntimeConfigFromError(error, retryAction = null) {
  const payload = error?.payload || {};
  if (payload.runtime_payload) applyRuntimePayload(payload.runtime_payload);
  state.runtime.forceConfigPrompt = true;
  state.runtime.runtimeErrorMessage = payload.reason || String(error?.message || '当前 LLM 配置不可用，请重新填写。');
  state.pendingRuntimeRetryAction = typeof retryAction === 'function' ? retryAction : null;
  updateRuntimeUi();
  shakeRuntimeModeToggle();
  shakeRuntimeConfigForm();
  document.getElementById('runtimeModePanel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const focusTarget = state.runtime.savedBaseUrl ? $('#runtimeConfigKey') : ($('#runtimeConfigUrl') || $('#runtimeConfigKey'));
  focusTarget?.focus();
}

// 处理无 Key 逻辑模式下点击智能助手面板的提示逻辑。
function handleAssistantLockedClick() {
  shakeRuntimeModeToggle();
  showToast('当前不可用', '请开启LLM Agent模式才能使用智能会话助手。', 'error');
  document.getElementById('runtimeModePanel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// 根据后端返回的运行模式信息刷新页面提示、开关状态和聊天面板可用性。
function updateRuntimeUi() {
  $('#runtimeModeBadge').textContent = `运行模式：${state.runtime.modeLabel}`;
  $('#runtimeModeTitle').textContent = state.runtime.modeTitle;
  $('#runtimeModeDesc').textContent = state.runtime.modeDescription;
  $('#runtimeTokenBadge').textContent = state.runtime.tokenPolicy;
  $('#runtimeSwitchHint').textContent = state.runtime.switchHint;
  $('#runtimeSwitchText').textContent = state.runtime.switchChecked ? 'LLM Agent 模式' : '无 Key 逻辑模式';
  $('#assistantModeTag').textContent = state.runtime.chatAvailable ? 'LLM Agent 已启用' : '仅 LLM 模式可用';
  $('#assistantPanelDesc').textContent = state.runtime.chatAvailable
    ? '助手会在对话里自主调用选题、视频解析、热门样本等工具。'
    : '当前处于无 Key 逻辑模式，智能会话助手会被禁用，切到 LLM Agent 模式后才可使用。';
  $('#assistantHint').textContent = state.runtime.chatAvailable
    ? '助手会结合当前页面里的选题输入或视频链接一起理解你的问题。'
    : '当前为无 Key 逻辑模式。开启上方开关后，助手才会进入真正的 LLM Agent 链路。';

  const toggle = $('#runtimeModeToggle');
  if (toggle) {
    toggle.classList.toggle('is-on', state.runtime.switchChecked);
    toggle.setAttribute('aria-pressed', state.runtime.switchChecked ? 'true' : 'false');
  }

  const savedConfigSummary = $('#runtimeConfigSummary');
  if (savedConfigSummary) {
    if (state.runtime.hasSavedConfig) {
      const parts = [
        state.runtime.savedProvider || '自定义 provider',
        state.runtime.savedModel || '默认模型',
        state.runtime.savedBaseUrl || '',
      ].filter(Boolean);
      const sourceText = state.runtime.savedConfigSource === 'env' ? '来自 .env' : '来自页面填写';
      savedConfigSummary.textContent = `已保存配置：${parts.join(' / ')} · ${sourceText}`;
    } else {
      savedConfigSummary.textContent = '当前没有已保存的 LLM 配置';
    }
  }

  const formVisible = Boolean(state.runtime.forceConfigPrompt || (state.runtime.requiresConfig && !state.runtime.chatAvailable));
  setRuntimeConfigFormVisible(formVisible);
  $('#runtimeConfigHint').textContent = state.runtime.runtimeErrorMessage
    ? `当前 LLM 配置调用失败：${state.runtime.runtimeErrorMessage}。请改填可用的 URL、Key 和模型供应商后重试。`
    : state.runtime.hasSavedConfig
      ? '当前已经保存过一组配置，关闭开关后再次打开会直接复用。'
      : '当前没有已保存配置，打开开关时需要先填写 URL、Key 和模型供应商。';

  const assistantPanel = $('#assistantPanel');
  const assistantOverlay = $('#assistantLockOverlay');
  if (assistantPanel) assistantPanel.classList.toggle('is-disabled', !state.runtime.chatAvailable);
  if (assistantOverlay) assistantOverlay.hidden = state.runtime.chatAvailable;

  const input = $('#assistantMessage');
  input.disabled = false;
  input.placeholder = state.runtime.chatAvailable
    ? '例如：帮我分析这个视频为什么没有起量；或者：我想做颜值向舞蹈账号，第一条视频该拍什么'
    : '当前为无 Key 逻辑模式。开启上方 LLM Agent 开关后，这里才可真正发起智能会话。';

  updateAssistantButton();
  renderAssistant();
}

// 切换运行模式开关，并按是否已有配置决定直接生效还是展示配置表单。
async function toggleRuntimeMode() {
  const nextEnabled = !state.runtime.switchChecked;
  try {
    const data = await requestJson('/api/runtime-mode', { enabled: nextEnabled });
    applyRuntimePayload(data);
    updateRuntimeUi();
    if (data.requires_config) {
      showToast('还缺配置', '当前没有已保存的 LLM 配置，请先填写 URL、Key 和模型供应商。', 'error');
      setStatus('请先填写 LLM 配置', 'error');
      $('#runtimeConfigUrl')?.focus();
      return;
    }
    if (nextEnabled) {
      showToast('已开启', '当前已经切到 LLM Agent 模式。');
      setStatus('已切换到 LLM Agent 模式', 'success');
    } else {
      state.pendingRuntimeRetryAction = null;
      showToast('已关闭', '当前已经切回无 Key 逻辑模式。');
      setStatus('已切换到无 Key 逻辑模式', 'success');
    }
  } catch (error) {
    shakeRuntimeModeToggle();
    showToast('切换失败', error.message, 'error');
  }
}

// 提交运行时 LLM 配置，并在保存成功后立即开启 LLM Agent 模式。
async function submitRuntimeConfig(event) {
  event.preventDefault();
  const payload = {
    base_url: ($('#runtimeConfigUrl').value || '').trim(),
    api_key: ($('#runtimeConfigKey').value || '').trim(),
    provider: ($('#runtimeConfigProvider').value || '').trim(),
    model: ($('#runtimeConfigModel').value || '').trim(),
  };
  if (!payload.base_url || !payload.api_key || !payload.provider) {
    showToast('缺少配置', '请完整填写 URL、Key 和模型供应商。', 'error');
    return;
  }

  const submitButton = $('#runtimeConfigSubmitBtn');
  if (submitButton) submitButton.disabled = true;
  try {
    const data = await requestJson('/api/runtime-llm-config', payload);
    applyRuntimePayload(data);
    state.runtime.forceConfigPrompt = false;
    state.runtime.runtimeErrorMessage = '';
    updateRuntimeUi();
    $('#runtimeConfigKey').value = '';
    showToast('配置已保存', '已保存当前 LLM 配置，并切到 LLM Agent 模式。');
    setStatus('已切换到 LLM Agent 模式', 'success');
    const retryAction = state.pendingRuntimeRetryAction;
    state.pendingRuntimeRetryAction = null;
    if (typeof retryAction === 'function') {
      window.setTimeout(() => retryAction(), 160);
    }
  } catch (error) {
    shakeRuntimeConfigForm();
    showToast('保存失败', error.message, 'error');
    setStatus('LLM 配置保存失败', 'error');
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
}

// 重置模块区域的 hover 和过渡样式。
function syncModuleHover() {
  const heroCard = $('.hero-card');
  const modeBanner = $('.mode-banner');
  if (heroCard) {
    heroCard.style.removeProperty('display');
    heroCard.style.removeProperty('maxHeight');
    heroCard.style.removeProperty('opacity');
    heroCard.style.removeProperty('filter');
    heroCard.style.removeProperty('transform');
    heroCard.style.removeProperty('marginBottom');
    heroCard.style.removeProperty('pointerEvents');
  }
  if (modeBanner) {
    modeBanner.style.removeProperty('display');
    modeBanner.style.removeProperty('maxHeight');
    modeBanner.style.removeProperty('opacity');
    modeBanner.style.removeProperty('filter');
    modeBanner.style.removeProperty('transform');
    modeBanner.style.removeProperty('marginBottom');
    modeBanner.style.removeProperty('pointerEvents');
  }
  $$('.module-panel').forEach(panel => {
    panel.style.removeProperty('--panel-fade-opacity');
    panel.style.removeProperty('--panel-fade-size');
    panel.style.removeProperty('transform');
    panel.style.removeProperty('opacity');
    panel.classList.remove('is-expanded', 'is-compressed');
  });
}

// 重置全局滚动场景相关状态。
function updateGlobalScrollScene() {
  state.moduleSplit = 0.5;
  state.moduleSwapProgress = 0;
  state.introCollapse = 0;
  syncModuleHover();
}

// 触发一次全局滚动场景更新。
function scheduleGlobalScrollSceneUpdate() {
  updateGlobalScrollScene();
}

// 初始化全局滚动场景。
function initGlobalScrollScene() {
  updateGlobalScrollScene();
}

// 判断当前缓存的视频解析结果是否仍对应同一个链接。
function isResolvedForUrl(url) {
  return Boolean(state.videoResolved && state.videoResolvedUrl === String(url || '').trim());
}

// 从后端读取运行模式信息并同步到前端状态。
async function loadRuntimeInfo() {
  try {
    const data = await requestGetJson('/api/runtime-info');
    applyRuntimePayload(data);
    updateRuntimeUi();
  } catch (error) {
    showToast('模式读取失败', error.message || '请检查后端服务', 'error');
  }
}

// 调用后端解析视频链接，并刷新预览区和缓存结果。
async function resolveVideoLink(url, seq = ++state.videoResolveSeq, options = {}) {
  const currentUrl = String(url || '').trim();
  if (!currentUrl) {
    state.videoResolved = null;
    state.videoResolvedUrl = '';
    $('#videoPreview').innerHTML = videoPreview(null);
    renderWorkspaceOutline();
    return null;
  }
  $('#videoPreview').innerHTML = videoPreview(isResolvedForUrl(currentUrl) ? state.videoResolved : null, { loading: true });
  renderWorkspaceOutline();
  if (!options.silent) setStatus('正在解析视频链接', 'loading');
  try {
    const data = await requestJson('/api/resolve-bili-link', { url: currentUrl });
    if (seq !== state.videoResolveSeq || ($('#videoLink').value || '').trim() !== currentUrl) return null;
    state.videoResolved = data;
    state.videoResolvedUrl = currentUrl;
    $('#videoPreview').innerHTML = videoPreview(data);
    renderWorkspaceOutline();
    if (!options.silent) setStatus('视频信息已解析', 'success');
    return data;
  } catch (error) {
    if (seq !== state.videoResolveSeq || ($('#videoLink').value || '').trim() !== currentUrl) return null;
    state.videoResolved = null;
    state.videoResolvedUrl = '';
    $('#videoPreview').innerHTML = videoPreview(null, { error: error.message });
    renderWorkspaceOutline();
    if (!options.silent) {
      setStatus('视频链接解析失败', 'error');
      showToast('解析失败', error.message, 'error');
    }
    throw error;
  }
}

// 对视频链接输入做防抖解析，避免每次输入都立刻请求。
function scheduleVideoResolve() {
  const url = ($('#videoLink').value || '').trim();
  state.videoResolveSeq += 1;
  if (state.videoResolveTimer) clearTimeout(state.videoResolveTimer);
  if (!url) {
    state.videoResolved = null;
    state.videoResolvedUrl = '';
    $('#videoPreview').innerHTML = videoPreview(null);
    renderWorkspaceOutline();
    return;
  }
  const seq = state.videoResolveSeq;
  state.videoResolveTimer = setTimeout(() => {
    resolveVideoLink(url, seq, { silent: true }).catch(() => {});
  }, 550);
}

// 计算当前工作台应展示的目录项列表。
function getOutlineItems(module = state.activeModule) {
  return [];
  const common = [
    { id: 'workspaceOverview', title: '工作台概览' },
    { id: 'runtimeModePanel', title: '运行模式' },
  ];
  const moduleSpecific = module === 'create'
    ? [
        { id: 'draftVideoModule', title: '内容创作' },
        { id: 'creatorTopicsSection', title: '热门选题建议' },
        { id: 'creatorCopySection', title: '自动生成文案' },
        { id: 'creatorResult', title: '生成结果' },
      ]
    : [
        { id: 'publishedVideoModule', title: '视频分析' },
        { id: 'videoPreviewSection', title: '视频信息预览' },
        { id: 'videoPerformanceSection', title: '表现判断' },
        { id: 'videoCoreInfoSection', title: '核心信息' },
        { id: 'videoReasonSection', title: '原因分析' },
        { id: 'videoTopicSection', title: '优先题材' },
        { id: 'videoOptimizeSection', title: '优化建议' },
        { id: 'videoCopySection', title: '优化文案' },
        { id: 'videoReferenceSection', title: '参考视频' },
        { id: 'videoResult', title: '分析结果' },
      ];

  const seen = new Set();
  return [...common, ...moduleSpecific]
    .filter(item => item.id && !seen.has(item.id) && document.getElementById(item.id))
    .filter(item => {
      seen.add(item.id);
      return true;
    });
}

// 根据滚动位置更新目录高亮项。
function updateOutlineActiveState() {
  return;
  if (!$('#workspaceOutline')) return;
  const items = getOutlineItems();
  if (!items.length) return;
  const topLine = 156;
  let activeId = items[0].id;
  items.forEach(item => {
    const el = document.getElementById(item.id);
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.top <= topLine) activeId = item.id;
  });
  state.activeOutlineId = activeId;
  $$('#workspaceOutline [data-outline-id]').forEach(button => {
    button.classList.toggle('is-active', button.dataset.outlineId === activeId);
  });
}

// 渲染工作台右侧目录，并绑定点击跳转。
function renderWorkspaceOutline() {
  return;
  const box = $('#workspaceOutline');
  if (!box) return;
  const items = getOutlineItems();
  box.innerHTML = `
    <div class="workspace-outline__label">标题目录</div>
    <div class="workspace-outline__list">
      ${items.map(item => `
        <button
          class="workspace-outline__item ${state.activeOutlineId === item.id ? 'is-active' : ''}"
          type="button"
          data-outline-id="${escapeHtml(item.id)}"
        >${escapeHtml(item.title)}</button>
      `).join('')}
    </div>
  `;
  box.querySelectorAll('[data-outline-id]').forEach(button => {
    button.addEventListener('click', () => {
      const target = document.getElementById(button.dataset.outlineId || '');
      if (!target) return;
      state.activeOutlineId = button.dataset.outlineId || '';
      box.querySelectorAll('[data-outline-id]').forEach(node => {
        node.classList.toggle('is-active', node === button);
      });
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
  updateOutlineActiveState();
}

// 切换当前激活的功能模块，并按需聚焦输入控件。
function setActiveModule(module, options = {}) {
  const next = ['analyze', 'create', 'knowledge'].includes(module) ? module : 'analyze';
  state.activeModule = next;
  const grid = $('#moduleGrid');
  if (grid) {
    grid.dataset.active = next;
  }
  $$('[data-module-tab]').forEach(button => {
    button.classList.toggle('is-active', button.dataset.moduleTab === next);
  });
  renderWorkspaceOutline();
  if (options.focus) {
    const target = grid?.querySelector(`.module-panel[data-module="${next}"] input, .module-panel[data-module="${next}"] textarea, .module-panel[data-module="${next}"] select`);
    if (target) target.focus({ preventScroll: true });
  }
}

// 提交内容创作请求并渲染返回的选题与文案结果。
async function runCreatorModule() {
  const payload = {
    field: ($('#creatorField').value || '').trim(),
    direction: ($('#creatorDirection').value || '').trim(),
    idea: ($('#creatorIdea').value || '').trim(),
    partition: $('#creatorPartition').value || 'knowledge',
    style: $('#creatorStyle').value || '干货',
  };
  if (!payload.field && !payload.direction && !payload.idea) {
    showToast('缺少输入', '请至少输入领域、方向、想法中的一项。', 'error');
    return;
  }
  setButtonLoading('creatorRunBtn', true);
  setStatus('正在生成选题与文案', 'loading');
  $('#creatorResult').innerHTML = loadingCard('正在生成选题与文案', '系统会先整理你的方向，再结合热门结构生成更自然的选题和文案。', ['整理方向', '分析热门结构', '生成选题', '生成文案']);
  try {
    const data = await requestJson('/api/module-create', payload);
    $('#creatorResult').innerHTML = creatorResult(data);
    bindCopyButtons($('#creatorResult'));
    if (data.llm_warning) showToast('LLM 已回退', 'Agent 中枢失败，已自动回退到直接 LLM 生成。', 'error');
    setStatus('选题与文案已生成', 'success');
  } catch (error) {
    $('#creatorResult').innerHTML = infoCard('生成失败', error.message, 'danger');
    setStatus('选题与文案生成失败', 'error');
    showToast('生成失败', error.message, 'error');
  } finally {
    setButtonLoading('creatorRunBtn', false);
  }
}

// 提交视频分析请求并渲染解析与分析结果。
async function runAnalyzeModule() {
  const url = ($('#videoLink').value || '').trim();
  if (!url) {
    showToast('缺少链接', '请先输入 B 站视频链接。', 'error');
    return;
  }
  setButtonLoading('videoAnalyzeBtn', true);
  setStatus('正在分析视频', 'loading');
  $('#videoResult').innerHTML = loadingCard('正在分析视频', '先校验当前视频信息，再判断它更像爆款还是播放偏低，并生成对应建议。', ['校验视频信息', '判断表现', '分析原因', '输出建议']);
  try {
    if (!isResolvedForUrl(url)) await resolveVideoLink(url, ++state.videoResolveSeq, { silent: true });
    const data = await requestJson('/api/module-analyze', { url, resolved: state.videoResolved });
    if (data.resolved) {
      state.videoResolved = data.resolved;
      state.videoResolvedUrl = url;
      $('#videoPreview').innerHTML = videoPreview(data.resolved);
    }
    $('#videoResult').innerHTML = videoResult(data);
    bindCopyButtons($('#videoResult'));
    if (data.llm_warning) showToast('LLM 已回退', 'Agent 中枢失败，已自动回退到直接 LLM 分析。', 'error');
    setStatus('视频分析已完成', 'success');
  } catch (error) {
    $('#videoResult').innerHTML = infoCard('分析失败', error.message, 'danger');
    setStatus('视频分析失败', 'error');
    showToast('分析失败', error.message, 'error');
  } finally {
    setButtonLoading('videoAnalyzeBtn', false);
  }
}

// 收集当前页面输入，作为助手对话的上下文。
function chatContext() {
  return {
    field: ($('#creatorField').value || '').trim(),
    direction: ($('#creatorDirection').value || '').trim(),
    idea: ($('#creatorIdea').value || '').trim(),
    partition: $('#creatorPartition').value || 'knowledge',
    style: $('#creatorStyle').value || '干货',
    videoLink: ($('#videoLink').value || '').trim(),
  };
}

// 发送助手消息并把返回结果写入对话记录。
async function sendAssistantMessage(forced = '') {
  if (!state.runtime.chatAvailable) {
    handleAssistantLockedClick();
    return;
  }
  const input = $('#assistantMessage');
  const message = (forced || input.value || '').trim();
  if (!message) {
    toggleVoiceInput();
    return;
  }
  state.chatHistory.push({ role: 'user', content: message });
  state.chatPending = true;
  input.value = '';
  autosize(input);
  updateAssistantButton();
  renderAssistant();
  setStatus('智能助手正在思考', 'loading');
  try {
    const data = await requestJson('/api/chat', {
      message,
      history: state.chatHistory.map(item => ({ role: item.role, content: item.content })),
      context: chatContext(),
    });
    state.chatHistory.push({
      role: 'assistant',
      content: data.reply || '暂无回复',
      actions: Array.isArray(data.suggested_next_actions) ? data.suggested_next_actions : [],
      references: Array.isArray(data.reference_links) ? data.reference_links : [],
    });
    setStatus('智能助手已回复', 'success');
  } catch (error) {
    state.chatHistory.push({ role: 'assistant', content: `请求失败：${error.message}`, error: true });
    setStatus('智能助手请求失败', 'error');
    showToast('智能助手失败', error.message, 'error');
  } finally {
    state.chatPending = false;
    renderAssistant();
    updateAssistantButton();
  }
}

// 初始化浏览器语音识别能力并同步输入框状态。
function initSpeechRecognition() {
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Ctor) return;
  const recognition = new Ctor();
  recognition.lang = 'zh-CN';
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.onstart = () => {
    state.isListening = true;
    updateAssistantButton();
    showToast('语音输入已开始', '请直接说话，识别结果会写入输入框。');
  };
  recognition.onresult = event => {
    const input = $('#assistantMessage');
    input.value = Array.from(event.results).map(item => item[0]?.transcript || '').join('');
    autosize(input);
    updateAssistantButton();
  };
  recognition.onerror = event => {
    if (event.error && event.error !== 'aborted') showToast('语音输入失败', `浏览器返回：${event.error}`, 'error');
  };
  recognition.onend = () => {
    state.isListening = false;
    updateAssistantButton();
  };
  state.recognition = recognition;
}

// 在开始和停止语音输入之间切换。
function toggleVoiceInput() {
  if (!state.runtime.chatAvailable) {
    handleAssistantLockedClick();
    return;
  }
  if (!state.recognition) {
    showToast('当前浏览器不支持', '浏览器不支持语音输入，请直接输入文字。', 'error');
    return;
  }
  try {
    if (state.isListening) state.recognition.stop();
    else state.recognition.start();
  } catch (error) {
    showToast('语音输入失败', '语音识别暂时不可用，请稍后再试。', 'error');
  }
}

// 绑定页面主要按钮、输入框和快捷交互事件。
function initEvents() {
  $('#creatorRunBtn').addEventListener('click', runCreatorModule);
  $('#videoAnalyzeBtn').addEventListener('click', runAnalyzeModule);
  $('#clearResultsBtn').addEventListener('click', clearResults);
  $('#runtimeModeToggle').addEventListener('click', toggleRuntimeMode);
  $('#runtimeConfigForm').addEventListener('submit', submitRuntimeConfig);
  $('#assistantLockOverlay').addEventListener('click', handleAssistantLockedClick);
  $('#videoLink').addEventListener('input', scheduleVideoResolve);
  $('#knowledgeActionHost')?.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.id === 'knowledgeUploadBtn') uploadKnowledgeFile();
    if (button.id === 'knowledgeUpdateBtn') updateKnowledgeBase();
    if (button.id === 'knowledgeSampleBtn') loadKnowledgeSamples();
    if (button.id === 'knowledgeSearchBtn') searchKnowledgeContent();
  });
  $('#knowledgeActionHost')?.addEventListener('input', event => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;
    if (target.id === 'knowledgeUpdateLimit') {
      state.knowledgeForm.updateLimit = Math.max(1, Math.min(20, Number(target.value || 10) || 10));
    }
    if (target.id === 'knowledgeViewLimit') {
      state.knowledgeForm.viewLimit = Math.max(1, Math.min(20, Number(target.value || 6) || 6));
    }
    if (target.id === 'knowledgeSearchInput') {
      state.knowledgeForm.searchQuery = target.value || '';
    }
  });
  $('#knowledgeActionHost')?.addEventListener('change', event => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    if (target.id === 'knowledgeSearchInput') {
      state.knowledgeForm.searchQuery = target.value || '';
    }
  });
  $('#knowledgeActionHost')?.addEventListener('keydown', event => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.id === 'knowledgeSearchInput' && event.key === 'Enter') {
      event.preventDefault();
      searchKnowledgeContent();
    }
  });
  $$('[data-knowledge-subtab]').forEach(button => {
    button.addEventListener('click', () => setKnowledgeSubtab(button.dataset.knowledgeSubtab || 'upload'));
  });
  $$('[data-module-tab]').forEach(button => {
    button.addEventListener('click', () => {
      setActiveModule(button.dataset.moduleTab || 'analyze', { focus: true });
    });
  });

  const assistantInput = $('#assistantMessage');
  autosize(assistantInput);
  assistantInput.addEventListener('input', () => {
    autosize(assistantInput);
    updateAssistantButton();
  });
  assistantInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendAssistantMessage();
    }
  });

  $('#assistantSendBtn').addEventListener('click', () => {
    ($('#assistantMessage').value || '').trim() ? sendAssistantMessage() : toggleVoiceInput();
  });

  $$('.assistant-prompt').forEach(button => {
    button.addEventListener('click', () => {
      const prompt = button.dataset.prompt || '';
      if (state.runtime.chatAvailable) {
        sendAssistantMessage(prompt);
        return;
      }
      $('#assistantMessage').value = prompt;
      autosize($('#assistantMessage'));
      updateAssistantButton();
      $('#assistantMessage').focus();
    });
  });
}

// 读取指定任务当前的进度百分比。
function currentProgressPercent(key) {
  return getProgressPercent(key, 0);
}

// 生成带百分比和步骤状态的加载卡片。
function loadingCard(title, desc, steps = [], percent = 0) {
  const safePercent = clampProgressValue(percent);
  const activeIndex = steps.length ? Math.min(steps.length - 1, Math.floor((safePercent / 100) * steps.length)) : -1;
  return `
    <section class="loading-card">
      <div class="block-title">
        <div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(desc)}</p></div>
        <span class="type-badge progress-percent">${formatProgressLabel(safePercent)}</span>
      </div>
      <div class="bili-progress"><div class="bili-progress__bar" style="width:${safePercent}%"></div></div>
      ${steps.length ? `<div class="bili-progress__steps">${steps.map((step, i) => `<div class="progress-step ${i < activeIndex ? 'is-done' : ''} ${i === activeIndex ? 'is-active' : ''}"><span class="progress-step__dot"></span><span>${escapeHtml(step)}</span></div>`).join('')}</div>` : ''}
    </section>
  `;
}

// 把加载卡片渲染到指定容器。
function renderProgressInto(containerId, title, desc, steps, percent) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = loadingCard(title, desc, steps, percent);
}

// 渲染带实时进度的助手思考占位气泡。
function assistantPendingBubble() {
  const percent = currentProgressPercent('assistant');
  return `
    <article class="chat-row chat-row--assistant">
      <div class="chat-bubble chat-bubble--assistant chat-bubble--pending">
        <div class="chat-bubble__head"><strong class="chat-bubble__name">智能助手</strong><span class="meta-line">正在思考</span></div>
        ${loadingCard('Agent 正在思考', '正在结合当前页面输入、工具结果和上下文组织回答。', ['理解问题', '调用工具', '组织回答'], percent)}
      </div>
    </article>
  `;
}

// 用当前聊天状态重新渲染助手区域，并绑定动态按钮。
function renderAssistant() {
  const box = $('#assistantResult');
  if (!box) return;
  if (!state.chatHistory.length && !state.chatPending) {
    box.innerHTML = assistantEmptyState();
    return;
  }
  box.innerHTML = `
    <div class="assistant-thread">
      ${state.chatHistory.map(item => `
        <article class="chat-row chat-row--${escapeHtml(item.role)}">
          <div class="chat-bubble chat-bubble--${escapeHtml(item.role)} ${item.error ? 'chat-bubble--error' : ''}">
            <div class="chat-bubble__head">
              <strong class="chat-bubble__name">${item.role === 'assistant' ? '智能助手' : '你'}</strong>
              ${item.role === 'assistant' ? `<button class="copy-btn" data-copy="${escapeHtml(item.content || item.fullContent || '')}" data-copy-label="回答">复制</button>` : ''}
            </div>
            <div class="rich-text">${rich(item.content || '')}${item.typing ? '<span class="typing-caret"></span>' : ''}</div>
            ${item.actions?.length ? `<div class="assistant-actions">${assistantActionButtons(item.actions)}</div>` : ''}
            ${item.references?.length ? `<div class="chat-links"><div class="meta-line">可直接打开的参考视频</div>${referenceGrid(item.references, true)}</div>` : ''}
          </div>
        </article>
      `).join('')}
      ${state.chatPending ? assistantPendingBubble() : ''}
    </div>
  `;
  bindCopyButtons(box);
  box.querySelectorAll('[data-assistant-action]').forEach(button => {
    if (button.dataset.boundClick === '1') return;
    button.dataset.boundClick = '1';
    button.addEventListener('click', () => {
      const prompt = button.dataset.assistantAction || '';
      if (!prompt || state.chatPending || state.chatTyping) return;
      sendAssistantMessage(prompt);
    });
  });
  requestAnimationFrame(() => {
    box.scrollTop = box.scrollHeight;
  });
}

// 根据聊天可用性、请求状态和输入内容刷新发送按钮。
function updateAssistantButton() {
  const input = $('#assistantMessage');
  const button = $('#assistantSendBtn');
  const icon = $('#assistantActionIcon');
  if (!input || !button || !icon) return;
  button.disabled = !state.runtime.chatAvailable || state.chatPending || state.chatTyping;
  button.classList.toggle('is-listening', state.isListening);
  icon.innerHTML = input.value.trim() ? SEND_ICON : MIC_ICON;
  button.setAttribute('aria-label', input.value.trim() ? '发送消息' : '语音输入');
}

// 清空所有模块结果、进度状态和当前对话记录。
function clearResults() {
  stopProgressJob('creator');
  stopProgressJob('analyze');
  stopProgressJob('assistant');
  state.videoResolved = null;
  state.videoResolvedUrl = '';
  state.videoResolveSeq += 1;
  state.chatPending = false;
  state.chatTyping = false;
  state.chatHistory = [];
  if (state.videoResolveTimer) {
    window.clearTimeout(state.videoResolveTimer);
    state.videoResolveTimer = null;
  }
  $('#creatorResult').innerHTML = '<div class="empty-state"><h4>还没有生成结果</h4><p>输入领域、方向和想法后，点击“一键生成选题与文案”。</p></div>';
  $('#videoResult').innerHTML = '<div class="empty-state"><h4>还没有分析结果</h4><p>输入视频链接后，点击“一键解析并分析视频”。</p></div>';
  $('#videoPreview').innerHTML = videoPreview(null);
  state.knowledgeResults.upload = knowledgePlaceholder('upload');
  state.knowledgeResults.sync = state.knowledgeStatus ? knowledgeSyncDefaultResult(state.knowledgeStatus) : knowledgePlaceholder('sync');
  state.knowledgeResults.view = knowledgePlaceholder('view');
  state.knowledgeResults.search = knowledgePlaceholder('search');
  setKnowledgeSubtab(state.knowledgeActiveSubtab);
  renderAssistant();
  renderWorkspaceOutline();
  updateAssistantButton();
  setStatus('已清空结果', 'success');
}

// 以打字机效果逐字显示助手回复。
async function typeAssistantReply(messageItem) {
  if (!messageItem) return;
  const fullText = messageItem.fullContent || messageItem.content || '';
  messageItem.content = '';
  messageItem.typing = true;
  state.chatTyping = true;
  renderAssistant();
  updateAssistantButton();

  for (let index = 0; index < fullText.length; index += 1) {
    messageItem.content += fullText[index];
    renderAssistant();
    await sleep(fullText[index] === '\n' ? 0 : 12);
  }

  messageItem.typing = false;
  delete messageItem.fullContent;
  state.chatTyping = false;
  renderAssistant();
  updateAssistantButton();
}

// 以带进度条的方式执行内容创作请求并展示结果。
async function runCreatorModule() {
  const payload = {
    field: ($('#creatorField').value || '').trim(),
    direction: ($('#creatorDirection').value || '').trim(),
    idea: ($('#creatorIdea').value || '').trim(),
    partition: $('#creatorPartition').value || 'knowledge',
    style: $('#creatorStyle').value || '干货',
  };
  if (!payload.field && !payload.direction && !payload.idea) {
    showToast('缺少输入', '请至少输入领域、方向、想法中的一项。', 'error');
    return;
  }

  const title = '正在生成选题与文案';
  const desc = '系统会先整理你的方向，再结合热门结构生成更自然的选题和文案。';
  const steps = ['整理方向', '分析热门结构', '生成选题', '生成文案'];

  setButtonLoading('creatorRunBtn', true);
  setStatus(title, 'loading');
  startProgressJob('creator', percent => renderProgressInto('creatorResult', title, desc, steps, percent), {
    start: 6,
    max: 92,
    minStep: 0.45,
    durationMs: 12000,
    interval: 180,
  });

  try {
    const data = await requestJson('/api/module-create', payload);
    stopProgressJob('creator', 100);
    $('#creatorResult').innerHTML = creatorResult(data);
    renderWorkspaceOutline();
    bindCopyButtons($('#creatorResult'));
    if (data.llm_warning) showToast('LLM 已回退', 'Agent 中枢失败，已自动回退到直接 LLM 生成。', 'error');
    setStatus('选题与文案已生成', 'success');
  } catch (error) {
    stopProgressJob('creator', 100);
    $('#creatorResult').innerHTML = infoCard('生成失败', error.message, 'danger');
    renderWorkspaceOutline();
    setStatus('选题与文案生成失败', 'error');
    showToast('生成失败', error.message, 'error');
    if (shouldPromptRuntimeConfig(error)) {
      promptRuntimeConfigFromError(error, () => runCreatorModule());
    }
  } finally {
    stopProgressJob('creator');
    setButtonLoading('creatorRunBtn', false);
  }
}

// 以带进度条的方式执行视频分析请求并展示结果。
async function runAnalyzeModule() {
  const url = ($('#videoLink').value || '').trim();
  if (!url) {
    showToast('缺少链接', '请先输入 B 站视频链接。', 'error');
    return;
  }

  const title = '正在分析视频';
  const desc = '先校验当前视频信息，再判断它更像爆款还是播放偏低，并生成对应建议。';
  const steps = ['校验视频信息', '判断表现', '分析原因', '输出建议'];

  setButtonLoading('videoAnalyzeBtn', true);
  setStatus(title, 'loading');
  startProgressJob('analyze', percent => renderProgressInto('videoResult', title, desc, steps, percent), {
    start: 8,
    max: 93,
    minStep: 0.5,
    durationMs: 13500,
    interval: 180,
  });

  try {
    if (!isResolvedForUrl(url)) await resolveVideoLink(url, ++state.videoResolveSeq, { silent: true });
    const data = await requestJson('/api/module-analyze', { url, resolved: state.videoResolved });
    stopProgressJob('analyze', 100);
    if (data.resolved) {
      state.videoResolved = data.resolved;
      state.videoResolvedUrl = url;
      $('#videoPreview').innerHTML = videoPreview(data.resolved);
    }
    $('#videoResult').innerHTML = videoResult(data);
    renderWorkspaceOutline();
    bindCopyButtons($('#videoResult'));
    if (data.llm_warning) showToast('LLM 已回退', 'Agent 中枢失败，已自动回退到直接 LLM 分析。', 'error');
    setStatus('视频分析已完成', 'success');
  } catch (error) {
    stopProgressJob('analyze', 100);
    $('#videoResult').innerHTML = infoCard('分析失败', error.message, 'danger');
    renderWorkspaceOutline();
    setStatus('视频分析失败', 'error');
    showToast('分析失败', error.message, 'error');
    if (shouldPromptRuntimeConfig(error)) {
      promptRuntimeConfigFromError(error, () => runAnalyzeModule());
    }
  } finally {
    stopProgressJob('analyze');
    setButtonLoading('videoAnalyzeBtn', false);
  }
}

// 发送助手消息，展示进度，并以打字机效果输出回复。
async function sendAssistantMessage(forced = '') {
  if (!state.runtime.chatAvailable) {
    handleAssistantLockedClick();
    return;
  }
  const input = $('#assistantMessage');
  const message = (forced || input.value || '').trim();
  if (!message) {
    toggleVoiceInput();
    return;
  }

  state.chatHistory.push({ role: 'user', content: message });
  state.chatPending = true;
  input.value = '';
  autosize(input);
  updateAssistantButton();
  renderAssistant();
  setStatus('智能助手正在思考', 'loading');
  startProgressJob('assistant', () => renderAssistant(), {
    start: 5,
    max: 91,
    minStep: 0.45,
    durationMs: 9000,
    interval: 160,
  });

  try {
    const data = await requestJson('/api/chat', {
      message,
      history: state.chatHistory.map(item => ({ role: item.role, content: item.fullContent || item.content })),
      context: chatContext(),
    });

    stopProgressJob('assistant', 100);
    state.chatPending = false;
    const assistantItem = {
      role: 'assistant',
      content: '',
      fullContent: data.reply || '暂无回复',
      actions: Array.isArray(data.suggested_next_actions) ? data.suggested_next_actions : [],
      references: Array.isArray(data.reference_links) ? data.reference_links : [],
      typing: true,
    };
    state.chatHistory.push(assistantItem);
    renderAssistant();
    setStatus('智能助手正在输出回答', 'loading');
    await typeAssistantReply(assistantItem);
    setStatus('智能助手已回复', 'success');
  } catch (error) {
    stopProgressJob('assistant', 100);
    state.chatPending = false;
    state.chatHistory.push({ role: 'assistant', content: `请求失败：${error.message}`, error: true });
    setStatus('智能助手请求失败', 'error');
    showToast('智能助手失败', error.message, 'error');
    if (shouldPromptRuntimeConfig(error)) {
      promptRuntimeConfigFromError(error);
    }
    renderAssistant();
  } finally {
    stopProgressJob('assistant');
    updateAssistantButton();
  }
}

// 渲染支持进度条的最新视频预览区。
function videoPreview(data, options = {}) {
  const resolved = data || {};
  const stats = resolved.stats || {};
  const loading = Boolean(options.loading);
  const error = options.error || '';
  const title = loading ? '正在自动解析视频信息' : error ? '视频链接解析失败' : data ? '已自动解析当前视频信息' : '当前视频信息预览';
  const note = loading
    ? '系统正在根据你输入的 B 站视频链接提取标题、分区、UP 主和互动数据。'
    : error
      ? error
      : data
        ? '这些字段来自当前视频链接的自动解析结果，点击下方按钮会基于这些真实信息继续分析。'
        : '粘贴视频链接后，这里会自动显示标题、类型、播放、点赞、投币、收藏、评论和分享。';
  const progress = loading
    ? (options.progress != null
      ? options.progress
      : (currentProgressPercent('analyze') || currentProgressPercent('resolve') || 0))
    : 0;
  const hasProgress = progress > 0;

  return `
    <section class="copy-block" id="videoPreviewSection">
      <div class="block-title">
        <div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(note)}</p></div>
        <span class="type-badge ${error ? 'type-badge--danger' : ''}">${loading ? '自动解析中' : data ? '已解析' : '待解析'}</span>
      </div>
      ${loading ? `<div class="bili-progress"><div class="bili-progress__bar ${hasProgress ? '' : 'bili-progress__bar--indeterminate'}" ${hasProgress ? `style="width:${clampProgressValue(progress)}%"` : ''}></div></div>` : ''}
      <div class="summary-strip">
        ${previewCard('视频标题', resolved.title || '', '根据视频链接自动解析当前视频标题')}
        ${previewCard('视频类型', resolved.partition_label || resolved.partition || '', '根据视频链接自动解析分区和视频类型')}
        ${previewCard('UP 主', resolved.up_name || '', '根据视频链接自动解析对应 UP 主')}
        ${previewCard('BV 号', resolved.bv_id || '', '根据视频链接自动解析对应 BV 号')}
      </div>
      <div class="summary-strip summary-strip--metrics">
        ${previewCard('播放量', data ? num(stats.view) : '', '根据视频链接自动解析公开播放量')}
        ${previewCard('点赞量', data ? num(stats.like) : '', '根据视频链接自动解析公开点赞量')}
        ${previewCard('投币量', data ? num(stats.coin) : '', '根据视频链接自动解析公开投币量')}
        ${previewCard('收藏量', data ? num(stats.favorite) : '', '根据视频链接自动解析公开收藏量')}
        ${previewCard('评论量', data ? num(stats.reply) : '', '根据视频链接自动解析公开评论量')}
        ${previewCard('分享量', data ? num(stats.share) : '', '根据视频链接自动解析公开分享量')}
      </div>
    </section>
  `;
}

// 初始化页面默认状态、事件绑定和运行模式信息。
function init() {
  setActiveModule(state.activeModule);
  state.knowledgeResults.upload = knowledgePlaceholder('upload');
  state.knowledgeResults.sync = knowledgePlaceholder('sync');
  state.knowledgeResults.view = knowledgePlaceholder('view');
  state.knowledgeResults.search = knowledgePlaceholder('search');
  setKnowledgeSubtab(state.knowledgeActiveSubtab);
  $('#videoPreview').innerHTML = videoPreview(null);
  renderWorkspaceOutline();
  renderAssistant();
  initEvents();
  initSpeechRecognition();
  initGlobalScrollScene();
  loadRuntimeInfo();
  loadKnowledgeBaseStatus();
  updateAssistantButton();
  bindCopyButtons(document);
  initCoverMedia();
}

window.addEventListener('DOMContentLoaded', init);
