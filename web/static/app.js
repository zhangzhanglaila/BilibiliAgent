const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 1 1-6 0V6a3 3 0 0 1 3-3z"></path><path d="M19 11a7 7 0 0 1-14 0"></path><path d="M12 18v3"></path><path d="M8 21h8"></path></svg>';
const SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13"></path><path d="m22 2-7 20-4-9-9-4 20-7Z"></path></svg>';

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
  chatPending: false,
  chatTyping: false,
  chatHistory: [],
  runtime: {
    mode: 'rules',
    llmEnabled: false,
    chatAvailable: false,
    modeLabel: '规则模式',
    modeTitle: '当前运行中：规则模式',
    modeDescription: '',
    tokenPolicy: '',
    switchHint: '',
  },
  recognition: null,
  isListening: false,
};

const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function rich(value) {
  return escapeHtml(value || '').replace(/\n/g, '<br>');
}

function num(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '0';
}

function pct(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function coverUrl(url) {
  const v = String(url || '').trim();
  if (!v) return '';
  if (v.startsWith('//')) return `https:${v}`;
  if (v.startsWith('http://')) return `https://${v.slice('http://'.length)}`;
  return v;
}

const COVER_RETRY_LIMIT = 2;
const COVER_RETRY_BASE_DELAY_MS = 900;

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

function finalizeCoverFallback(frame, img) {
  if (img) {
    img.hidden = true;
    img.style.display = 'none';
    img.removeAttribute('src');
  }
  setCoverFrameState(frame, 'fallback');
}

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

function showToast(title, message, type = 'success') {
  const stack = $('#toastStack');
  if (!stack) return;
  const node = document.createElement('div');
  node.className = `toast toast--${type}`;
  node.innerHTML = `<div class="toast__title">${escapeHtml(title)}</div><div>${escapeHtml(message)}</div>`;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 2800);
}

function setButtonLoading(id, loading) {
  const button = document.getElementById(id);
  if (!button) return;
  button.disabled = loading;
  button.classList.toggle('is-loading', loading);
}

function autosize(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
}

function sleep(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function clampPercent(value) {
  return Math.max(0, Math.min(100, Math.round(value || 0)));
}

function clampProgressValue(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

function formatProgressLabel(value) {
  const safe = clampProgressValue(value);
  if (safe >= 100) return '100%';
  return `${safe.toFixed(1)}%`;
}

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

function getProgressPercent(key, fallback = 0) {
  return clampProgressValue(state.progressJobs[key]?.percent ?? fallback);
}

async function requestJson(url, payload) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || '请求失败');
    return data.data;
  } catch (error) {
    if (error instanceof Error && /Failed to fetch/i.test(error.message)) {
      throw new Error('接口请求失败，请检查 Flask 服务是否在运行，或后端是否正在重启。');
    }
    throw error;
  }
}

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

async function copyText(text, label = '内容') {
  try {
    await navigator.clipboard.writeText(text);
    showToast('复制成功', `${label}已复制到剪贴板`);
  } catch (error) {
    showToast('复制失败', '当前浏览器不支持自动复制，请手动复制', 'error');
  }
}

function bindCopyButtons(scope = document) {
  scope.querySelectorAll('[data-copy]').forEach(button => {
    if (button.dataset.copyBound === '1') return;
    button.dataset.copyBound = '1';
    button.addEventListener('click', () => copyText(button.dataset.copy || '', button.dataset.copyLabel || '内容'));
  });
}

function tags(items = []) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  return list.length
    ? `<div class="tag-list">${list.map(item => `<span class="tag">${escapeHtml(item)}</span>`).join('')}</div>`
    : '<p class="section-note">暂无标签</p>';
}

function loadingCard(title, desc, steps = []) {
  return `
    <section class="loading-card">
      <div class="block-title">
        <div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(desc)}</p></div>
        <span class="type-badge">处理中</span>
      </div>
      <div class="bili-progress"><div class="bili-progress__bar bili-progress__bar--indeterminate"></div></div>
      ${steps.length ? `<div class="bili-progress__steps">${steps.map((step, i) => `<div class="progress-step ${i === 0 ? 'is-active' : ''}"><span class="progress-step__dot"></span><span>${escapeHtml(step)}</span></div>`).join('')}</div>` : ''}
    </section>
  `;
}

function infoCard(title, text, tone = '') {
  return `<div class="info-card ${tone ? `info-card--${tone}` : ''}"><h4>${escapeHtml(title)}</h4><p>${escapeHtml(text)}</p></div>`;
}

function previewCard(label, value, hint = '根据视频链接自动解析') {
  const ok = value !== undefined && value !== null && String(value).trim() !== '';
  return `<div class="stat-card preview-card" title="${escapeHtml(hint)}"><h4>${escapeHtml(label)}</h4><span class="stat-card__value ${ok ? '' : 'is-placeholder'}">${ok ? escapeHtml(value) : '待解析'}</span></div>`;
}

function metricCard(label, value, hint = '') {
  return `<div class="stat-card" title="${escapeHtml(hint)}"><h4>${escapeHtml(label)}</h4><span class="stat-card__value">${escapeHtml(value)}</span>${hint ? `<p>${escapeHtml(hint)}</p>` : ''}</div>`;
}

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

function referenceGrid(items = [], compact = false) {
  const list = Array.isArray(items) ? items.filter(item => item && item.url) : [];
  if (!list.length) return '';
  return `<div class="reference-grid ${compact ? 'reference-grid--chat' : ''}">${list.map(item => {
    const cover = coverUrl(item.cover);
    const title = item.title || '未命名视频';
    return `
      <a class="reference-card ${compact ? 'reference-card--chat' : ''}" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">
        <div class="reference-card__thumb">${renderCoverMedia(cover, title, compact ? 'reference-chat' : 'reference')}</div>
        <div class="reference-card__body">
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(item.author || '未知 UP')}</p>
          <div class="reference-card__meta"><span>播放 ${num(item.view)}</span><span>点赞率 ${pct(item.like_rate)}</span></div>
        </div>
      </a>
    `;
  }).join('')}</div>`;
}

function referenceSection(items = [], title = '可直接参考的高表现视频', desc = '点击卡片可直接打开当前做得好的视频页面。') {
  const grid = referenceGrid(items, false);
  return `
    <section class="copy-block" id="videoReferenceSection">
      <div class="block-title"><div><h4>${escapeHtml(title)}</h4><p>${escapeHtml(desc)}</p></div></div>
      ${grid || '<div class="info-card"><h4>暂未找到强相关参考视频</h4><p>当前已优先按视频标题和主题检索同题材高表现视频；如果这一区块为空，通常是公开搜索结果过少或题材过窄。</p></div>'}
    </section>
  `;
}

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

function bulletList(items = []) {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) return infoCard('暂无内容', '当前没有可展示的分析项。');
  return `<div class="analysis-list">${list.map(item => `<article class="analysis-item"><span class="analysis-item__dot"></span><p>${escapeHtml(item)}</p></article>`).join('')}</div>`;
}

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

function assistantEmptyState() {
  return `
    <div class="empty-state">
      <h4>${state.runtime.chatAvailable ? '还没有对话内容' : '当前为规则模式'}</h4>
      <p>${state.runtime.chatAvailable ? '你可以直接像聊天一样提问，助手会结合当前页面上下文作答。' : '配置 LLM_API_KEY 后，右侧对话助手会切到真正的 LLM Agent 链路。'}</p>
    </div>
  `;
}

function assistantPendingBubble() {
  return `
    <article class="chat-row chat-row--assistant">
      <div class="chat-bubble chat-bubble--assistant chat-bubble--pending">
        <div class="chat-bubble__head"><strong class="chat-bubble__name">智能助手</strong><span class="meta-line">正在思考</span></div>
        ${loadingCard('Agent 正在思考', '正在结合当前页面输入、工具结果和上下文组织回答。', ['理解问题', '调用工具', '组织回答'])}
      </div>
    </article>
  `;
}

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
              ${item.role === 'assistant' ? `<button class="copy-btn" data-copy="${escapeHtml(item.content || '')}" data-copy-label="回答">复制</button>` : ''}
            </div>
            <div class="rich-text">${rich(item.content || '')}</div>
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

function updateRuntimeUi() {
  $('#runtimeModeBadge').textContent = `运行模式：${state.runtime.modeLabel}`;
  $('#runtimeModeTitle').textContent = state.runtime.modeTitle;
  $('#runtimeModeDesc').textContent = state.runtime.modeDescription;
  $('#runtimeTokenBadge').textContent = state.runtime.tokenPolicy;
  $('#runtimeSwitchHint').textContent = state.runtime.switchHint;
  $('#assistantModeTag').textContent = state.runtime.chatAvailable ? 'LLM Agent 已启用' : '仅 LLM 模式可用';
  $('#assistantPanelDesc').textContent = state.runtime.chatAvailable
    ? '助手会在对话里自主调用选题、视频解析和热门样本等工具。'
    : '当前未配置 LLM_API_KEY，右侧对话助手不会调用模型，也不会消耗 token。';
  $('#assistantHint').textContent = state.runtime.chatAvailable
    ? '助手会结合当前页面里的选题输入或视频链接一起理解你的问题。'
    : '想启用对话助手，请在 .env 中配置 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 后重启服务。';
  const input = $('#assistantMessage');
  input.disabled = !state.runtime.chatAvailable;
  input.placeholder = state.runtime.chatAvailable
    ? '例如：帮我分析这个视频为什么没有起量；或者：我想做颜值向舞蹈账号，第一条视频该拍什么'
    : '当前为规则模式。配置 LLM_API_KEY 后这里会启用对话。';
  updateAssistantButton();
  renderAssistant();
}

function updateAssistantButton() {
  const input = $('#assistantMessage');
  const button = $('#assistantSendBtn');
  const icon = $('#assistantActionIcon');
  if (!input || !button || !icon) return;
  button.disabled = !state.runtime.chatAvailable;
  button.classList.toggle('is-listening', state.isListening);
  icon.innerHTML = input.value.trim() ? SEND_ICON : MIC_ICON;
  button.setAttribute('aria-label', input.value.trim() ? '发送消息' : '语音输入');
}

function clearResults() {
  state.videoResolved = null;
  state.videoResolvedUrl = '';
  state.videoResolveSeq += 1;
  state.chatHistory = [];
  state.chatPending = false;
  if (state.videoResolveTimer) clearTimeout(state.videoResolveTimer);
  $('#creatorResult').innerHTML = '<div class="empty-state"><h4>还没有生成结果</h4><p>输入领域、方向和想法后，点击“一键生成选题与文案”。</p></div>';
  $('#videoResult').innerHTML = '<div class="empty-state"><h4>还没有分析结果</h4><p>输入视频链接后，点击“一键解析并分析视频”。</p></div>';
  $('#videoPreview').innerHTML = videoPreview(null);
  renderAssistant();
  setStatus('已清空结果', 'success');
}

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

function updateGlobalScrollScene() {
  state.moduleSplit = 0.5;
  state.moduleSwapProgress = 0;
  state.introCollapse = 0;
  syncModuleHover();
}

function scheduleGlobalScrollSceneUpdate() {
  updateGlobalScrollScene();
}

function initGlobalScrollScene() {
  updateGlobalScrollScene();
}

function isResolvedForUrl(url) {
  return Boolean(state.videoResolved && state.videoResolvedUrl === String(url || '').trim());
}

async function loadRuntimeInfo() {
  try {
    const data = await requestGetJson('/api/runtime-info');
    state.runtime = {
      mode: data.mode || 'rules',
      llmEnabled: Boolean(data.llm_enabled),
      chatAvailable: Boolean(data.chat_available),
      modeLabel: data.mode_label || '规则模式',
      modeTitle: data.mode_title || '当前运行中：规则模式',
      modeDescription: data.mode_description || '',
      tokenPolicy: data.token_policy || '',
      switchHint: data.switch_hint || '',
    };
    updateRuntimeUi();
  } catch (error) {
    showToast('模式读取失败', error.message || '请检查后端服务', 'error');
  }
}

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

function setActiveModule(module, options = {}) {
  const next = module === 'create' ? 'create' : 'analyze';
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

async function sendAssistantMessage(forced = '') {
  if (!state.runtime.chatAvailable) {
    showToast('当前不可用', '请先配置 LLM_API_KEY 并重启服务。', 'error');
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

function toggleVoiceInput() {
  if (!state.runtime.chatAvailable) return;
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

function initEvents() {
  $('#creatorRunBtn').addEventListener('click', runCreatorModule);
  $('#videoAnalyzeBtn').addEventListener('click', runAnalyzeModule);
  $('#clearResultsBtn').addEventListener('click', clearResults);
  $('#videoLink').addEventListener('input', scheduleVideoResolve);
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

function currentProgressPercent(key) {
  return getProgressPercent(key, 0);
}

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

function renderProgressInto(containerId, title, desc, steps, percent) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = loadingCard(title, desc, steps, percent);
}

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
  renderAssistant();
  renderWorkspaceOutline();
  updateAssistantButton();
  setStatus('已清空结果', 'success');
}

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
  } finally {
    stopProgressJob('creator');
    setButtonLoading('creatorRunBtn', false);
  }
}

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
  } finally {
    stopProgressJob('analyze');
    setButtonLoading('videoAnalyzeBtn', false);
  }
}

async function sendAssistantMessage(forced = '') {
  if (!state.runtime.chatAvailable) {
    showToast('当前不可用', '请先配置 LLM_API_KEY 并重启服务。', 'error');
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
    renderAssistant();
  } finally {
    stopProgressJob('assistant');
    updateAssistantButton();
  }
}

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

function init() {
  setActiveModule(state.activeModule);
  $('#videoPreview').innerHTML = videoPreview(null);
  renderWorkspaceOutline();
  renderAssistant();
  initEvents();
  initSpeechRecognition();
  initGlobalScrollScene();
  loadRuntimeInfo();
  updateAssistantButton();
  bindCopyButtons(document);
  initCoverMedia();
}

window.addEventListener('DOMContentLoaded', init);
