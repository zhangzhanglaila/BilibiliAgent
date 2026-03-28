const state = {
  loadingKey: '',
  progressJob: null,
  videoResolved: null,
  videoResolveError: '',
  videoResolveTimer: null,
  videoResolveSeq: 0,
  runtime: {
    mode: 'rules',
    llmEnabled: false,
    chatAvailable: false,
    modeLabel: '无 Key 规则模式',
    modeTitle: '当前运行中：无 Key 逻辑模式',
    modeDescription: '',
    tokenPolicy: '',
    switchHint: '',
  },
  chatHistory: [],
};

function $(selector) {
  return document.querySelector(selector);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatRichText(value) {
  return escapeHtml(value).replace(/\n/g, '<br>');
}

function formatNumber(value) {
  const num = Number(value || 0);
  return Number.isFinite(num) ? num.toLocaleString('zh-CN') : '0';
}

function formatPercent(value) {
  const num = Number(value || 0);
  return `${(num * 100).toFixed(2)}%`;
}

function setStatus(text, type = 'idle') {
  const pill = $('#globalStatusPill');
  const statusText = $('#statusText');
  const modeText = $('#currentModeText');
  if (!pill || !statusText || !modeText) return;

  pill.classList.remove('is-loading', 'is-success', 'is-error');
  if (type === 'loading') pill.classList.add('is-loading');
  if (type === 'success') pill.classList.add('is-success');
  if (type === 'error') pill.classList.add('is-error');

  statusText.textContent = text;
  modeText.textContent = text;
}

function showToast(title, message, type = 'success') {
  const stack = $('#toastStack');
  if (!stack) return;

  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;
  toast.innerHTML = `
    <div class="toast__title">${escapeHtml(title)}</div>
    <div>${escapeHtml(message)}</div>
  `;
  stack.appendChild(toast);

  window.setTimeout(() => {
    toast.remove();
  }, 2600);
}

function setButtonLoading(buttonId, isLoading) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  btn.disabled = isLoading;
  btn.classList.toggle('is-loading', isLoading);
}

async function requestJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.success) {
    throw new Error(data.error || '请求失败');
  }
  return data.data;
}

async function requestGetJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok || !data.success) {
    throw new Error(data.error || '请求失败');
  }
  return data.data;
}

async function copyText(text, label = '内容') {
  try {
    await navigator.clipboard.writeText(text);
    showToast('复制成功', `${label}已复制到剪贴板`, 'success');
  } catch (error) {
    showToast('复制失败', '当前浏览器不支持自动复制，请手动复制', 'error');
  }
}

function bindCopyButtons(scope = document) {
  scope.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', () => {
      copyText(btn.dataset.copy, btn.dataset.copyLabel || '内容');
    });
  });
}

function renderTags(tags = []) {
  if (!tags.length) return '<p class="section-note">暂无关键词。</p>';
  return `<div class="tag-list">${tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join('')}</div>`;
}

function renderIdeaCards(topicResult) {
  const ideas = topicResult?.ideas || [];
  if (!ideas.length) {
    return '<div class="info-card"><h4>暂无选题建议</h4><p>当前没有可展示的选题结果。</p></div>';
  }

  return `
    <div class="topic-grid">
      ${ideas.map((idea, index) => `
        <article class="topic-card">
          <div class="topic-card__head">
            <div>
              <div class="meta-line">TOP ${index + 1}</div>
              <h4>${escapeHtml(idea.topic)}</h4>
            </div>
            <span class="type-badge">${escapeHtml(idea.video_type || '干货')}</span>
          </div>
          <p>${escapeHtml(idea.reason || '')}</p>
          ${renderTags(idea.keywords || [])}
        </article>
      `).join('')}
    </div>
  `;
}

function renderReferenceVideos(items = [], title = '可直接参考的高表现视频', description = '点击可直接打开对应 B 站页面') {
  if (!items.length) {
    return '';
  }

  return `
    <section class="copy-block">
      <div class="block-title">
        <div>
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(description)}</p>
        </div>
      </div>
      <div class="topic-grid">
        ${items.map((item, index) => `
          <article class="copy-card">
            <div class="card-head">
              <div>
                <div class="meta-line">参考视频 ${index + 1}</div>
                <h4>${escapeHtml(item.title || '未命名视频')}</h4>
              </div>
              <a class="copy-btn" href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer">打开链接</a>
            </div>
            <p>${escapeHtml(item.author || '未知UP')} · 播放 ${formatNumber(item.view)} · 点赞率 ${formatPercent(item.like_rate)}</p>
          </article>
        `).join('')}
      </div>
    </section>
  `;
}

function renderCopyResult(copyResult) {
  const titles = copyResult?.titles || [];
  const script = copyResult?.script || [];
  const description = copyResult?.description || '';
  const tags = copyResult?.tags || [];
  const pinned = copyResult?.pinned_comment || '';

  return `
    <div class="copy-layout">
      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>高流量标题</h4>
            <p>结合当前方向自动生成的标题备选</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(titles.join('\n'))}" data-copy-label="标题集合">一键复制</button>
        </div>
        <div class="copy-title-grid">
          ${titles.length ? titles.map((title, index) => `
            <article class="copy-card">
              <div class="card-head">
                <div>
                  <div class="meta-line">标题 ${index + 1}</div>
                  <h4>${escapeHtml(title)}</h4>
                </div>
                <button class="copy-btn" data-copy="${escapeHtml(title)}" data-copy-label="标题 ${index + 1}">复制</button>
              </div>
            </article>
          `).join('') : '<div class="info-card"><p>暂无标题结果。</p></div>'}
        </div>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>文案脚本</h4>
            <p>可直接拆成视频段落使用</p>
          </div>
          <button
            class="copy-btn"
            data-copy="${escapeHtml(script.map(item => `[${item.duration || ''}] ${item.section || ''}: ${item.content || ''}`).join('\n'))}"
            data-copy-label="完整脚本"
          >
            一键复制
          </button>
        </div>
        <div class="script-list">
          ${script.length ? script.map((item, index) => `
            <article class="script-item">
              <div class="script-item__meta">
                <div style="display:flex;align-items:center;gap:12px;">
                  <span class="script-item__index">${index + 1}</span>
                  <strong>${escapeHtml(item.section || '片段')}</strong>
                </div>
                <span class="script-item__time">${escapeHtml(item.duration || '')}</span>
              </div>
              <p>${escapeHtml(item.content || '')}</p>
              <div>
                <button class="copy-btn" data-copy="${escapeHtml(item.content || '')}" data-copy-label="脚本片段 ${index + 1}">复制片段</button>
              </div>
            </article>
          `).join('') : '<div class="info-card"><p>暂无脚本结果。</p></div>'}
        </div>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>简介与标签</h4>
            <p>适合直接放到发布页里</p>
          </div>
        </div>
        <article class="copy-card">
          <div class="card-head">
            <div>
              <div class="meta-line">视频简介</div>
              <h4>简介文案</h4>
            </div>
            <button class="copy-btn" data-copy="${escapeHtml(description)}" data-copy-label="视频简介">复制简介</button>
          </div>
          <p>${escapeHtml(description || '暂无简介结果。')}</p>
          <div class="spacer-xs"></div>
          ${renderTags(tags)}
        </article>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>置顶评论</h4>
            <p>用于引导互动和收集下一期方向</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(pinned)}" data-copy-label="置顶评论">复制评论</button>
        </div>
        <article class="dark-card">
          <p class="rich-text">${escapeHtml(pinned || '暂无置顶评论结果。')}</p>
        </article>
      </section>
    </div>
  `;
}

function renderCreatorResult(data) {
  const normalizedProfile = data.normalized_profile || data.seed_topic || '未填写';
  const userQuestion = data.seed_topic || normalizedProfile || '未填写';
  return `
    <div class="result-stack">
      <div class="summary-strip">
        <div class="stat-card">
          <h4>整理后的方向</h4>
          <p>${escapeHtml(normalizedProfile)}</p>
        </div>
        <div class="stat-card">
          <h4>当前问题</h4>
          <p>${escapeHtml(userQuestion)}</p>
        </div>
        <div class="stat-card">
          <h4>推荐主选题</h4>
          <p>${escapeHtml(data.chosen_topic || '暂无')}</p>
        </div>
        <div class="stat-card">
          <h4>文案风格</h4>
          <p>${escapeHtml(data.style || '干货')}</p>
        </div>
      </div>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>热门选题建议</h4>
            <p>基于你的方向和当前热门结构，自动整理出更值得做的切口。</p>
          </div>
        </div>
        ${renderIdeaCards(data.topic_result)}
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>自动生成文案</h4>
            <p>围绕主选题生成标题、脚本、简介和置顶评论。</p>
          </div>
        </div>
        ${renderCopyResult(data.copy_result)}
      </section>
    </div>
  `;
}

function renderMetricCard(label, value, hint = '') {
  return `
    <div class="stat-card" title="${escapeHtml(hint || '根据视频链接自动解析')}">
      <h4>${escapeHtml(label)}</h4>
      <span class="stat-card__value">${escapeHtml(value)}</span>
      ${hint ? `<p>${escapeHtml(hint)}</p>` : ''}
    </div>
  `;
}

function renderPreviewCard(label, value, hint = '根据视频链接自动解析') {
  const display = value ? escapeHtml(value) : '待自动解析';
  return `
    <div class="stat-card preview-card" title="${escapeHtml(hint)}">
      <h4>${escapeHtml(label)}</h4>
      <span class="stat-card__value">${display}</span>
      <p>${escapeHtml(hint)}</p>
    </div>
  `;
}

function renderVideoPreview(data, options = {}) {
  const resolved = data || {};
  const stats = resolved.stats || {};
  const loading = Boolean(options.loading);
  const error = options.error || '';
  const title = loading ? '正在自动解析视频信息' : error ? '自动解析失败' : data ? '已自动解析当前视频信息' : '等待自动解析视频信息';
  const note = loading
    ? '系统正在根据你输入的 B 站视频链接提取标题、分区、播放、点赞等真实信息。'
    : error
      ? error
      : data
        ? '这些字段来自当前视频链接的自动解析结果，点击下面按钮会基于这些信息继续分析。'
        : '粘贴完整视频链接后，这里会自动显示标题、分区、播放、点赞、收藏等信息。';

  return `
    <section class="copy-block">
      <div class="block-title">
        <div>
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(note)}</p>
        </div>
        ${loading ? '<span class="type-badge">自动解析中</span>' : data ? '<span class="type-badge">已解析</span>' : '<span class="type-badge">待解析</span>'}
      </div>
      ${loading ? `
        <div class="bili-progress">
          <div class="bili-progress__bar bili-progress__bar--indeterminate"></div>
        </div>
      ` : ''}
      <div class="summary-strip">
        ${renderPreviewCard('视频标题', resolved.title || '', '根据视频链接自动解析当前视频标题')}
        ${renderPreviewCard('视频类型', resolved.partition_label || resolved.partition || '', '根据视频链接自动解析分区/视频类型')}
        ${renderPreviewCard('UP 主', resolved.up_name || '', '根据视频链接自动解析 UP 主信息')}
        ${renderPreviewCard('BV 号', resolved.bv_id || '', '根据视频链接自动解析 BV 号')}
      </div>
      <div class="summary-strip">
        ${renderPreviewCard('播放量', data ? formatNumber(stats.view) : '', '根据视频链接自动解析公开播放量')}
        ${renderPreviewCard('点赞量', data ? formatNumber(stats.like) : '', '根据视频链接自动解析公开点赞量')}
        ${renderPreviewCard('投币 / 收藏', data ? `${formatNumber(stats.coin)} / ${formatNumber(stats.favorite)}` : '', '根据视频链接自动解析公开投币和收藏')}
        ${renderPreviewCard('评论 / 分享', data ? `${formatNumber(stats.reply)} / ${formatNumber(stats.share)}` : '', '根据视频链接自动解析公开评论和分享')}
      </div>
    </section>
  `;
}

function renderVideoMetrics(resolved) {
  const stats = resolved?.stats || {};
  return `
    <div class="summary-strip">
      ${renderMetricCard('播放', formatNumber(stats.view), '当前公开播放数据')}
      ${renderMetricCard('点赞', formatNumber(stats.like), `点赞率 ${formatPercent(stats.like_rate)}`)}
      ${renderMetricCard('投币 / 收藏', `${formatNumber(stats.coin)} / ${formatNumber(stats.favorite)}`, '反映内容认可度和收藏价值')}
      ${renderMetricCard('评论 / 分享', `${formatNumber(stats.reply)} / ${formatNumber(stats.share)}`, '反映讨论和传播意愿')}
    </div>
  `;
}

function renderBulletList(items = []) {
  if (!items.length) {
    return '<div class="info-card"><p>暂无内容。</p></div>';
  }
  return `
    <div class="analysis-list">
      ${items.filter(Boolean).map(item => `
        <article class="analysis-item">
          <span class="analysis-item__dot"></span>
          <p>${escapeHtml(item)}</p>
        </article>
      `).join('')}
    </div>
  `;
}

function renderSimpleTopics(topics = [], title = '后续可做方向') {
  return `
    <section class="copy-block">
      <div class="block-title">
        <div>
          <h4>${escapeHtml(title)}</h4>
          <p>围绕当前视频继续做内容延展</p>
        </div>
      </div>
      <div class="topic-grid">
        ${topics.length ? topics.map((topic, index) => `
          <article class="copy-card">
            <div class="card-head">
              <div>
                <div class="meta-line">方向 ${index + 1}</div>
                <h4>${escapeHtml(topic)}</h4>
              </div>
              <button class="copy-btn" data-copy="${escapeHtml(topic)}" data-copy-label="后续方向 ${index + 1}">复制</button>
            </div>
          </article>
        `).join('') : '<div class="info-card"><p>暂无后续方向建议。</p></div>'}
      </div>
    </section>
  `;
}

function renderVideoResult(data) {
  const resolved = data.resolved || {};
  const performance = data.performance || {};
  const analysis = data.analysis || {};
  const optimizeResult = data.optimize_result || {};
  const copyResult = data.copy_result;
  const referenceVideos = data.reference_videos || [];
  const llmWarning = data.llm_warning || '';

  const headerSummary = `
    <div class="summary-strip">
      <div class="stat-card">
        <h4>视频标题</h4>
        <p>${escapeHtml(resolved.title || '未知标题')}</p>
      </div>
      <div class="stat-card">
        <h4>UP 主 / 分区</h4>
        <p>${escapeHtml(resolved.up_name || '未知UP')} / ${escapeHtml(resolved.partition_label || resolved.partition || '未知')}</p>
      </div>
      <div class="stat-card">
        <h4>结果判断</h4>
        <p>${escapeHtml(performance.label || '待判断')}</p>
      </div>
      <div class="stat-card">
        <h4>BV 号</h4>
        <p>${escapeHtml(resolved.bv_id || '-')}</p>
      </div>
    </div>
  `;

  const hotSection = performance.is_hot ? `
    <section class="copy-block">
      <div class="block-title">
        <div>
          <h4>为什么它能火</h4>
          <p>${escapeHtml(performance.summary || '')}</p>
        </div>
      </div>
      ${renderBulletList(analysis.analysis_points || [])}
    </section>
    ${renderSimpleTopics(analysis.followup_topics || [], '继续放大的后续选题')}
  ` : '';

  const lowSection = !performance.is_hot ? `
    <section class="copy-block">
      <div class="block-title">
        <div>
          <h4>为什么现在播放偏低</h4>
          <p>${escapeHtml(performance.summary || '')}</p>
        </div>
      </div>
      ${renderBulletList(analysis.analysis_points || [])}
    </section>

    <section class="copy-block">
      <div class="block-title">
        <div>
          <h4>优化建议</h4>
          <p>针对标题、封面和内容节奏给出可执行调整。</p>
        </div>
      </div>
      <div class="summary-strip">
        ${renderMetricCard('优化标题', (analysis.title_suggestions || []).join(' / ') || '暂无', '建议先做标题 AB 测试')}
        ${renderMetricCard('封面方向', analysis.cover_suggestion || '暂无', '先强化结果感和反差感')}
      </div>
      ${renderBulletList(analysis.content_suggestions || optimizeResult.content_suggestions || [])}
    </section>

    ${renderSimpleTopics(analysis.next_topics || [], '建议尝试的新选题方向')}

    ${copyResult ? `
      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>当前主题可直接参考的新文案</h4>
            <p>围绕当前视频核心主题，重新生成一版更适合优化后的文案结构。</p>
          </div>
        </div>
        ${renderCopyResult(copyResult)}
      </section>
    ` : ''}
  ` : '';

  return `
    <div class="result-stack">
      ${llmWarning ? `<article class="info-card"><h4>LLM 分析提示</h4><p>${escapeHtml(llmWarning)}</p></article>` : ''}
      ${headerSummary}
      ${renderVideoMetrics(resolved)}
      ${hotSection}
      ${lowSection}
      ${renderReferenceVideos(referenceVideos, '现在做得好的同类视频', '点击即可跳转到当前表现更好的参考视频页面')}
    </div>
  `;
}

function getCurrentChatContext() {
  return {
    field: $('#creatorField')?.value.trim() || '',
    direction: $('#creatorDirection')?.value.trim() || '',
    idea: $('#creatorIdea')?.value.trim() || '',
    partition: $('#creatorPartition')?.value || 'knowledge',
    style: $('#creatorStyle')?.value || '干货',
    videoLink: $('#videoLink')?.value.trim() || '',
  };
}

function renderChatResult() {
  const container = $('#assistantResult');
  if (!container) return;

  if (!state.chatHistory.length) {
    container.innerHTML = `
      <div class="empty-state">
        <h4>还没有对话内容</h4>
        <p>${escapeHtml(state.runtime.chatAvailable
          ? '现在可以直接自然语言提问，Agent 会按意图调用工具后回答。'
          : '当前是无 Key 规则模式，聊天助手会在配置 LLM_API_KEY 后启用。')}</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="assistant-thread">
      ${state.chatHistory.map((item, index) => `
        <article class="chat-row chat-row--${escapeHtml(item.role)}">
          <div class="chat-bubble chat-bubble--${escapeHtml(item.role)}">
            <div class="chat-bubble__head">
              <div class="meta-line">${item.role === 'user' ? '你' : '智能助手'}</div>
              ${item.role === 'assistant'
                ? `<button class="copy-btn" data-copy="${escapeHtml(item.content || '')}" data-copy-label="助手回复 ${index}">复制</button>`
                : ''}
            </div>
            <p class="rich-text">${formatRichText(item.content || '')}</p>
            ${item.tools?.length ? `<div class="tag-list">${item.tools.map(tool => `<span class="tag">工具: ${escapeHtml(tool)}</span>`).join('')}</div>` : ''}
            ${item.references?.length ? `
              <div class="chat-links">
                ${item.references.map((link, linkIndex) => `
                  <a class="copy-btn" href="${escapeHtml(link.url || '#')}" target="_blank" rel="noopener noreferrer">
                    参考视频 ${linkIndex + 1}
                  </a>
                `).join('')}
              </div>
            ` : ''}
            ${item.actions?.length ? `<div class="assistant-actions">${item.actions.map(action => `<div class="analysis-item"><span class="analysis-item__dot"></span><p>${escapeHtml(action)}</p></div>`).join('')}</div>` : ''}
          </div>
        </article>
      `).join('')}
    </div>
  `;
  bindCopyButtons(container);
  container.scrollTop = container.scrollHeight;
}

function renderProgressCard(title, progress, steps = []) {
  return `
    <div class="result-stack">
      <article class="loading-card">
        <div class="loading-card__head">
          <div>
            <div class="meta-line">处理中</div>
            <h4>${escapeHtml(title)}</h4>
          </div>
          <span class="type-badge">${Math.max(1, Math.min(99, Math.round(progress)))}%</span>
        </div>
        <div class="bili-progress">
          <div class="bili-progress__bar" style="width:${Math.max(6, Math.min(96, progress))}%;"></div>
        </div>
        <div class="analysis-list">
          ${steps.map((item, index) => `
            <article class="analysis-item ${index === steps.length - 1 ? 'analysis-item--active' : ''}">
              <span class="analysis-item__dot"></span>
              <p>${escapeHtml(item)}</p>
            </article>
          `).join('')}
        </div>
      </article>
    </div>
  `;
}

function stopProgress() {
  if (state.progressJob?.timer) {
    window.clearInterval(state.progressJob.timer);
  }
  state.progressJob = null;
}

function startProgress(targetId, title, stages) {
  stopProgress();
  const container = document.getElementById(targetId);
  if (!container) return;

  state.progressJob = {
    targetId,
    title,
    stages: stages.slice(),
    progress: 12,
    activeCount: 1,
    timer: null,
  };

  const render = () => {
    const current = state.progressJob;
    const target = document.getElementById(targetId);
    if (!current || !target) return;
    target.innerHTML = renderProgressCard(current.title, current.progress, current.stages.slice(0, current.activeCount));
  };

  render();
  state.progressJob.timer = window.setInterval(() => {
    const current = state.progressJob;
    if (!current) return;
    current.progress = Math.min(92, current.progress + Math.random() * 11);
    const nextCount = Math.min(current.stages.length, Math.max(1, Math.ceil((current.progress / 100) * current.stages.length)));
    current.activeCount = Math.max(current.activeCount, nextCount);
    render();
  }, 700);
}

function finishProgress() {
  if (!state.progressJob) return;
  state.progressJob.progress = 100;
  state.progressJob.activeCount = state.progressJob.stages.length;
  const container = document.getElementById(state.progressJob.targetId);
  if (container) {
    container.innerHTML = renderProgressCard(state.progressJob.title, 100, state.progressJob.stages);
  }
  window.setTimeout(() => {
    stopProgress();
  }, 220);
}

function updateRuntimeUi() {
  const runtimePanel = $('#runtimeModePanel');
  const runtimeBadge = $('#runtimeModeBadge');
  const runtimeTitle = $('#runtimeModeTitle');
  const runtimeDesc = $('#runtimeModeDesc');
  const runtimeTokenBadge = $('#runtimeTokenBadge');
  const runtimeSwitchHint = $('#runtimeSwitchHint');
  const assistantModeTag = $('#assistantModeTag');
  const panelDesc = $('#assistantPanelDesc');
  const hint = $('#assistantHint');
  const sendBtn = $('#assistantSendBtn');
  const input = $('#assistantMessage');

  if (runtimePanel) {
    runtimePanel.classList.remove('mode-banner--rules', 'mode-banner--llm');
    runtimePanel.classList.add(state.runtime.mode === 'llm_agent' ? 'mode-banner--llm' : 'mode-banner--rules');
  }

  if (runtimeBadge) {
    runtimeBadge.textContent = `运行模式：${state.runtime.modeLabel || '未知模式'}`;
  }
  if (runtimeTitle) {
    runtimeTitle.textContent = state.runtime.modeTitle || '当前运行模式未知';
  }
  if (runtimeDesc) {
    runtimeDesc.textContent = state.runtime.modeDescription || '';
  }
  if (runtimeTokenBadge) {
    runtimeTokenBadge.textContent = state.runtime.tokenPolicy || '';
  }
  if (runtimeSwitchHint) {
    runtimeSwitchHint.textContent = state.runtime.switchHint || '';
  }
  if (assistantModeTag) {
    assistantModeTag.textContent = state.runtime.chatAvailable ? 'LLM Agent 已启用' : '仅 LLM 模式可用';
  }
  if (panelDesc) {
    panelDesc.textContent = state.runtime.modeDescription || '';
  }
  if (hint) {
    hint.textContent = state.runtime.chatAvailable
      ? '聊天助手会结合当前页面里的选题输入或视频链接一起理解你的问题。'
      : '当前未检测到 LLM_API_KEY，聊天面板仅展示占位说明，不会发送任何模型请求。';
  }
  if (sendBtn) {
    sendBtn.disabled = !state.runtime.chatAvailable;
  }
  if (input) {
    input.disabled = !state.runtime.chatAvailable;
  }
}

function resetVideoPreview() {
  state.videoResolved = null;
  state.videoResolveError = '';
  const container = $('#videoPreview');
  if (container) {
    container.innerHTML = renderVideoPreview(null);
  }
}

async function autoResolveVideoLink(force = false) {
  const input = $('#videoLink');
  const url = input?.value.trim() || '';
  const container = $('#videoPreview');
  if (!container) return null;

  if (!url) {
    resetVideoPreview();
    return null;
  }

  const seq = ++state.videoResolveSeq;
  container.innerHTML = renderVideoPreview(null, { loading: true });
  try {
    const data = await requestJson('/api/resolve-bili-link', { url });
    if (seq !== state.videoResolveSeq && !force) {
      return null;
    }
    state.videoResolved = data;
    state.videoResolveError = '';
    container.innerHTML = renderVideoPreview(data);
    return data;
  } catch (error) {
    if (seq !== state.videoResolveSeq && !force) {
      return null;
    }
    state.videoResolved = null;
    state.videoResolveError = error.message || '视频链接自动解析失败';
    container.innerHTML = renderVideoPreview(null, { error: state.videoResolveError });
    return null;
  }
}

function scheduleVideoResolve() {
  if (state.videoResolveTimer) {
    window.clearTimeout(state.videoResolveTimer);
  }
  state.videoResolveTimer = window.setTimeout(() => {
    autoResolveVideoLink();
  }, 650);
}

async function loadRuntimeInfo() {
  try {
    const data = await requestGetJson('/api/runtime-info');
    state.runtime = {
      mode: data.mode || 'rules',
      llmEnabled: Boolean(data.llm_enabled),
      chatAvailable: Boolean(data.chat_available),
      modeLabel: data.mode_label || '无 Key 规则模式',
      modeTitle: data.mode_title || '当前运行模式未知',
      modeDescription: data.mode_description || '',
      tokenPolicy: data.token_policy || '',
      switchHint: data.switch_hint || '',
    };
  } catch (error) {
    state.runtime = {
      mode: 'rules',
      llmEnabled: false,
      chatAvailable: false,
      modeLabel: '无 Key 规则模式',
      modeTitle: '当前运行中：无 Key 逻辑模式',
      modeDescription: '运行模式读取失败，默认按无 Key 规则模式处理。',
      tokenPolicy: '不会消耗 token，聊天助手当前关闭。',
      switchHint: '如果要切到 LLM 模式，填写 .env 里的 LLM_API_KEY 后重启服务。',
    };
  }
  updateRuntimeUi();
  renderChatResult();
}

async function runCreatorModule() {
  if (state.loadingKey) return;

  const field = $('#creatorField')?.value.trim() || '';
  const direction = $('#creatorDirection')?.value.trim() || '';
  const idea = $('#creatorIdea')?.value.trim() || '';
  const partition = $('#creatorPartition')?.value || 'knowledge';
  const style = $('#creatorStyle')?.value || '干货';

  if (!field && !direction && !idea) {
    showToast('缺少输入', '请至少填写领域、方向或想法中的一项', 'error');
    return;
  }

  try {
    state.loadingKey = 'creator';
    setButtonLoading('creatorRunBtn', true);
    setStatus('正在生成选题与文案...', 'loading');
    startProgress('creatorResult', '正在生成模块一结果', [
      '整理你的领域、方向和想法',
      '抓取当前分区热点与同类样本',
      '生成更容易起量的选题方向',
      '输出标题、脚本、简介和标签',
    ]);

    const data = await requestJson('/api/module-create', {
      field,
      direction,
      idea,
      partition,
      style,
    });

    const container = $('#creatorResult');
    if (container) {
      finishProgress();
      container.innerHTML = renderCreatorResult(data);
      bindCopyButtons(container);
    }

    setStatus('模块一结果已更新', 'success');
    showToast('生成完成', '已生成选题与文案结果', 'success');
  } catch (error) {
    setStatus('模块一执行失败', 'error');
    showToast('生成失败', error.message || '发生未知错误', 'error');
  } finally {
    stopProgress();
    state.loadingKey = '';
    setButtonLoading('creatorRunBtn', false);
  }
}

async function runVideoModule() {
  if (state.loadingKey) return;

  const url = $('#videoLink')?.value.trim() || '';
  if (!url) {
    showToast('缺少链接', '请先输入 B 站视频链接', 'error');
    return;
  }

  try {
    let resolved = state.videoResolved;
    if (!resolved) {
      resolved = await autoResolveVideoLink(true);
    }
    if (!resolved) {
      throw new Error(state.videoResolveError || '当前视频链接还没有解析成功');
    }

    state.loadingKey = 'video';
    setButtonLoading('videoAnalyzeBtn', true);
    setStatus('正在解析并分析视频...', 'loading');
    startProgress('videoResult', '正在分析模块二结果', [
      '提取视频标题、播放、点赞、投币、收藏等真实信息',
      '抓取同类样本与热点视频',
      '判断更像爆款还是播放偏低',
      '生成分析结论和优化建议',
    ]);

    const data = await requestJson('/api/module-analyze', { url, resolved });
    const container = $('#videoResult');
    if (container) {
      finishProgress();
      container.innerHTML = renderVideoResult(data);
      bindCopyButtons(container);
    }

    setStatus('模块二结果已更新', 'success');
    showToast('分析完成', '已返回视频解析和优化结果', 'success');
  } catch (error) {
    setStatus('模块二执行失败', 'error');
    showToast('分析失败', error.message || '发生未知错误', 'error');
  } finally {
    stopProgress();
    state.loadingKey = '';
    setButtonLoading('videoAnalyzeBtn', false);
  }
}

async function sendAssistantMessage() {
  if (state.loadingKey) return;
  if (!state.runtime.chatAvailable) {
    showToast('功能未启用', '当前是无 Key 规则模式，聊天助手仅在配置 LLM_API_KEY 后可用', 'error');
    return;
  }

  const input = $('#assistantMessage');
  const message = input?.value.trim() || '';
  if (!message) {
    showToast('缺少内容', '请输入你想让智能助手处理的问题', 'error');
    return;
  }

  state.chatHistory.push({ role: 'user', content: message });
  renderChatResult();
  if (input) input.value = '';

  try {
    state.loadingKey = 'assistant';
    setButtonLoading('assistantSendBtn', true);
    setStatus('智能助手正在思考...', 'loading');
    startProgress('assistantResult', '智能助手正在思考', [
      '理解你的问题和当前页面上下文',
      '按意图选择要调用的工具',
      '提取参考视频与样本数据',
      '组织回答并给出下一步建议',
    ]);

    const data = await requestJson('/api/chat', {
      message,
      history: state.chatHistory.map(item => ({ role: item.role, content: item.content })),
      context: getCurrentChatContext(),
    });

    state.chatHistory.push({
      role: 'assistant',
      content: data.reply || '助手没有返回有效内容。',
      actions: data.suggested_next_actions || [],
      tools: data.agent_trace || [],
      references: data.reference_links || [],
    });
    finishProgress();
    renderChatResult();
    setStatus('智能助手已回复', 'success');
  } catch (error) {
    setStatus('智能助手执行失败', 'error');
    showToast('对话失败', error.message || '发生未知错误', 'error');
  } finally {
    stopProgress();
    state.loadingKey = '';
    setButtonLoading('assistantSendBtn', false);
    updateRuntimeUi();
  }
}

function clearAllResults() {
  stopProgress();
  if (state.videoResolveTimer) {
    window.clearTimeout(state.videoResolveTimer);
    state.videoResolveTimer = null;
  }
  const creatorResult = $('#creatorResult');
  const videoResult = $('#videoResult');

  if (creatorResult) {
    creatorResult.innerHTML = `
      <div class="empty-state">
        <h4>还没有生成结果</h4>
        <p>输入领域、方向和想法后，点击“一键生成选题与文案”。</p>
      </div>
    `;
  }

  if (videoResult) {
    videoResult.innerHTML = `
      <div class="empty-state">
        <h4>还没有分析结果</h4>
        <p>输入视频链接后，点击“一键解析并分析视频”。</p>
      </div>
    `;
  }

  resetVideoPreview();

  state.chatHistory = [];
  renderChatResult();

  setStatus('等待操作', 'idle');
  showToast('已清空', '模块结果和聊天记录都已重置', 'success');
}

async function init() {
  $('#creatorRunBtn')?.addEventListener('click', runCreatorModule);
  $('#videoAnalyzeBtn')?.addEventListener('click', runVideoModule);
  $('#assistantSendBtn')?.addEventListener('click', sendAssistantMessage);
  $('#assistantMessage')?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendAssistantMessage();
    }
  });
  document.querySelectorAll('.assistant-prompt').forEach(button => {
    button.addEventListener('click', () => {
      const input = $('#assistantMessage');
      if (input) {
        input.value = button.dataset.prompt || '';
        input.focus();
      }
    });
  });
  $('#videoLink')?.addEventListener('input', scheduleVideoResolve);
  $('#videoLink')?.addEventListener('blur', () => autoResolveVideoLink(true));
  $('#clearResultsBtn')?.addEventListener('click', clearAllResults);
  setStatus('等待操作', 'idle');
  resetVideoPreview();
  await loadRuntimeInfo();
}

init();
