const state = {
  topic: null,
  copy: null,
  operate: null,
  optimize: null,
  pipeline: null,
  loading: false,
  form: {
    bili_link: '',
    partition_name: 'knowledge',
    up_ids: [546195, 15263701, 777536],
    style: '干货',
    bv_id: 'BV1xx411c7mD',
    manual_topic: 'AI 视频剪辑提效',
  },
};

const ACTION_LABELS = {
  topic: '选题 Agent',
  copy: '文案 Agent',
  operate: '运营 Agent',
  optimize: '优化 Agent',
  pipeline: '全流程',
};

function $(selector) {
  return document.querySelector(selector);
}

function $all(selector) {
  return Array.from(document.querySelectorAll(selector));
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function parseUpIds(value) {
  return value
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .map(item => Number(item))
    .filter(item => !Number.isNaN(item));
}

function payload() {
  return {
    partition: state.form.partition_name,
    up_ids: state.form.up_ids,
    style: state.form.style,
    bv_id: state.form.bv_id,
    topic: state.form.manual_topic,
    dry_run: true,
  };
}

function syncFormState() {
  const biliLinkEl = $('#biliLink');
  const partitionEl = $('#partition');
  const upIdsEl = $('#upIds');
  const styleEl = $('#style');
  const bvIdEl = $('#bvId');
  const topicEl = $('#topic');

  state.form.bili_link = biliLinkEl ? biliLinkEl.value.trim() : state.form.bili_link;
  state.form.partition_name = partitionEl ? partitionEl.value.trim() : state.form.partition_name;
  state.form.up_ids = upIdsEl ? parseUpIds(upIdsEl.value) : state.form.up_ids;
  state.form.style = styleEl ? styleEl.value : state.form.style;
  state.form.bv_id = bvIdEl ? bvIdEl.value.trim() : state.form.bv_id;
  state.form.manual_topic = topicEl ? topicEl.value.trim() : state.form.manual_topic;
}

async function resolveBiliLink(link) {
  const response = await fetch('/api/resolve-bili-link', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: link }),
  });
  const data = await response.json();
  if (!response.ok || !data.success) {
    throw new Error(data.error || '链接解析失败');
  }
  return data.data;
}

function applyResolvedData(resolved) {
  state.form.bv_id = resolved.bv_id || state.form.bv_id;
  state.form.up_ids = resolved.mid ? [resolved.mid] : state.form.up_ids;
  state.form.partition_name = resolved.partition || state.form.partition_name;

  const bvIdEl = $('#bvId');
  const upIdsEl = $('#upIds');
  const partitionEl = $('#partition');
  if (bvIdEl) bvIdEl.value = state.form.bv_id;
  if (upIdsEl) upIdsEl.value = state.form.up_ids.join(',');
  if (partitionEl) partitionEl.value = state.form.partition_name;
}

async function handleBiliLinkInput(link) {
  if (!link || !/BV[0-9A-Za-z]+/i.test(link)) return;
  try {
    setStatus('正在解析B站视频链接...', 'loading');
    const resolved = await resolveBiliLink(link);
    applyResolvedData(resolved);
    setStatus('链接解析完成', 'success');
    showToast('解析成功', '已自动填充表单', 'success');
  } catch (error) {
    setStatus('链接解析失败', 'error');
    showToast('解析失败', error.message || '请检查链接是否有效', 'error');
  }
}

function setStatus(text, type = 'idle') {
  const pill = $('#globalStatusPill');
  const statusText = $('#statusText');
  const modeText = $('#currentModeText');

  pill.classList.remove('is-loading', 'is-success', 'is-error');
  if (type === 'loading') pill.classList.add('is-loading');
  if (type === 'success') pill.classList.add('is-success');
  if (type === 'error') pill.classList.add('is-error');

  statusText.textContent = text;
  modeText.textContent = text;
}

function setResultMeta(text) {
  $('#resultMetaText').textContent = text;
}

function setLoading(action, isLoading) {
  state.loading = isLoading;
  $all('.action-btn').forEach(btn => {
    const match = btn.dataset.action === action;
    btn.disabled = isLoading;
    btn.classList.toggle('is-loading', isLoading && match);
  });
}

function switchTab(tab) {
  $all('.tab-btn').forEach(btn => {
    btn.classList.toggle('is-active', btn.dataset.tab === tab);
  });
  $all('.tab-panel').forEach(panel => {
    panel.classList.toggle('is-active', panel.dataset.panel === tab);
  });
}

function showEmpty(tab, visible) {
  const empty = document.querySelector(`[data-empty="${tab}"]`);
  const panel = document.getElementById(`${tab}Panel`);
  if (!empty || !panel) return;
  empty.classList.toggle('hidden', !visible);
  panel.classList.toggle('hidden', visible);
}

function showToast(title, message, type = 'success') {
  const stack = $('#toastStack');
  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;
  toast.innerHTML = `
    <div class="toast__title">${escapeHtml(title)}</div>
    <div>${escapeHtml(message)}</div>
  `;
  stack.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 2600);
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

function bindStepToggles(scope = document) {
  scope.querySelectorAll('.step-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.pipeline-step');
      if (!card) return;
      const collapsed = card.classList.toggle('is-collapsed');
      btn.textContent = collapsed ? '展开' : '收起';
    });
  });
}

function cardTags(tags = []) {
  if (!tags.length) return '';
  return `<div class="tag-list">${tags.map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join('')}</div>`;
}

function renderTopicResult(data) {
  const ideas = data?.ideas || [];
  if (!ideas.length) {
    return `<div class="info-card"><h4>暂无选题</h4><p>当前没有可展示的选题数据。</p></div>`;
  }

  return `
    <div class="summary-strip">
      <div class="stat-card">
        <h4>选题数量</h4>
        <span class="stat-card__value">${ideas.length}</span>
        <p>已筛选出高潜力方向</p>
      </div>
      <div class="stat-card">
        <h4>数据来源</h4>
        <span class="stat-card__value">${escapeHtml(data.source_count ?? 0)}</span>
        <p>热榜、分区与同类 UP 数据</p>
      </div>
      <div class="stat-card">
        <h4>当前重点</h4>
        <span class="stat-card__value">${escapeHtml(ideas[0]?.video_type || '干货')}</span>
        <p>优先建议先做第 1 个选题</p>
      </div>
    </div>
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
          ${cardTags(idea.keywords || [])}
        </article>
      `).join('')}
    </div>
  `;
}

function renderCopyResult(data) {
  const titles = data?.titles || [];
  const script = data?.script || [];
  const description = data?.description || '';
  const tags = data?.tags || [];
  const pinned = data?.pinned_comment || '';

  return `
    <div class="copy-layout">
      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>标题区</h4>
            <p>3 个备选标题，可直接复制使用</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(titles.join('\n'))}" data-copy-label="标题集合">一键复制</button>
        </div>
        <div class="copy-title-grid">
          ${titles.map((title, index) => `
            <article class="copy-card">
              <div class="card-head">
                <div>
                  <div class="meta-line">标题 ${index + 1}</div>
                  <h4>${escapeHtml(title)}</h4>
                </div>
                <button class="copy-btn" data-copy="${escapeHtml(title)}" data-copy-label="标题 ${index + 1}">复制</button>
              </div>
            </article>
          `).join('')}
        </div>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>脚本区</h4>
            <p>按分镜 / 段落展示，包含时长建议</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(script.map(item => `[${item.duration}] ${item.section}: ${item.content}`).join('\n'))}" data-copy-label="完整脚本">一键复制</button>
        </div>
        <div class="script-list">
          ${script.map((item, index) => `
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
          `).join('')}
        </div>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>简介区</h4>
            <p>适合直接粘贴到视频简介</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(description)}" data-copy-label="视频简介">复制简介</button>
        </div>
        <article class="dark-card">
          <p class="rich-text">${escapeHtml(description)}</p>
        </article>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>标签区</h4>
            <p>精准关键词、热门标签与同类标签</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(tags.join(', '))}" data-copy-label="标签">复制标签</button>
        </div>
        <article class="copy-card">
          ${cardTags(tags)}
        </article>
      </section>

      <section class="copy-block">
        <div class="block-title">
          <div>
            <h4>置顶评论区</h4>
            <p>用于引导互动和收集后续选题方向</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(pinned)}" data-copy-label="置顶评论">复制评论</button>
        </div>
        <article class="dark-card">
          <p class="rich-text">${escapeHtml(pinned)}</p>
        </article>
      </section>
    </div>
  `;
}

function renderOperateActionCard(action, type) {
  const isDanger = type === 'delete';
  const titleMap = {
    reply: '回复建议',
    delete: '垃圾评论处理',
    like: '点赞动作',
    follow: '关注动作',
  };
  return `
    <article class="comment-card ${isDanger ? 'is-danger' : ''}">
      <div class="card-head">
        <div>
          <div class="meta-line">${titleMap[type] || '互动动作'}</div>
          <h4>${escapeHtml(action.action || 'action')}</h4>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <span class="dry-badge">dry_run=${escapeHtml(action.dry_run)}</span>
          ${isDanger ? '<span class="warn-badge">建议重点处理</span>' : ''}
        </div>
      </div>
      <div class="comment-bubble">${escapeHtml(action.message || '')}</div>
      <div class="reply-bubble">目标：${escapeHtml(action.target || '')}</div>
    </article>
  `;
}

function renderOperateResult(data) {
  const replies = data?.replies || [];
  const deletions = data?.deletions || [];
  const likes = data?.likes || [];
  const follows = data?.follows || [];

  return `
    <div class="operate-section">
      <div class="summary-strip">
        <div class="stat-card">
          <h4>处理汇总</h4>
          <p>${escapeHtml(data?.summary || '暂无汇总')}</p>
        </div>
        <div class="stat-card">
          <h4>回复建议</h4>
          <span class="stat-card__value">${replies.length}</span>
          <p>建议优先处理高价值互动</p>
        </div>
        <div class="stat-card">
          <h4>垃圾评论</h4>
          <span class="stat-card__value">${deletions.length}</span>
          <p>已用红色高亮标识</p>
        </div>
        <div class="stat-card">
          <h4>点赞 / 关注</h4>
          <span class="stat-card__value">${likes.length} / ${follows.length}</span>
          <p>默认保持 dry_run 安全模式</p>
        </div>
      </div>

      <div class="copy-block">
        <div class="block-title">
          <div><h4>回复建议</h4><p>对话式卡片展示，适合直接参考回复</p></div>
        </div>
        <div class="comment-list">
          ${replies.length ? replies.map(item => renderOperateActionCard(item, 'reply')).join('') : '<div class="info-card"><p>暂无回复建议。</p></div>'}
        </div>
      </div>

      <div class="copy-block">
        <div class="block-title">
          <div><h4>垃圾评论过滤</h4><p>引战、辱骂、广告类内容会单独标红</p></div>
        </div>
        <div class="comment-list">
          ${deletions.length ? deletions.map(item => renderOperateActionCard(item, 'delete')).join('') : '<div class="info-card"><p>未识别到垃圾评论。</p></div>'}
        </div>
      </div>

      <div class="optimize-grid">
        <div class="copy-card">
          <div class="card-head"><div><h4>点赞动作</h4></div></div>
          <div class="comment-list">
            ${likes.length ? likes.map(item => renderOperateActionCard(item, 'like')).join('') : '<p>暂无点赞动作。</p>'}
          </div>
        </div>
        <div class="copy-card">
          <div class="card-head"><div><h4>关注动作</h4></div></div>
          <div class="comment-list">
            ${follows.length ? follows.map(item => renderOperateActionCard(item, 'follow')).join('') : '<p>暂无关注动作。</p>'}
          </div>
        </div>
      </div>
    </div>
  `;
}

function numberFromPercent(text) {
  const match = String(text || '').match(/(\d+(?:\.\d+)?)%/);
  return match ? Math.min(100, Number(match[1])) : 50;
}

function renderOptimizeResult(data) {
  const summary = data?.benchmark_summary || '';
  const completion = numberFromPercent(summary.match(/完播率\s*(\d+(?:\.\d+)?)%/)?.[0] || '50%');
  const likeRate = numberFromPercent(summary.match(/点赞率\s*(\d+(?:\.\d+)?)%/)?.[0] || '50%');
  const titlePower = data?.optimized_titles?.length ? 78 : 45;

  return `
    <div class="optimize-layout">
      <section class="optimize-section">
        <div class="block-title">
          <div>
            <h4>数据概览</h4>
            <p>通过小卡片和进度条快速查看当前优化重点</p>
          </div>
        </div>
        <div class="summary-strip">
          <div class="stat-card">
            <h4>诊断概览</h4>
            <p>${escapeHtml(data?.diagnosis || '暂无诊断')}</p>
          </div>
          <div class="stat-card">
            <h4>基准总结</h4>
            <p>${escapeHtml(summary)}</p>
          </div>
        </div>
        <div class="optimize-card">
          <div class="progress-group">
            <div class="progress-item">
              <div class="progress-item__meta"><span>标题吸引力</span><strong>${titlePower}%</strong></div>
              <div class="progress-track"><div class="progress-bar" style="width:${titlePower}%"></div></div>
            </div>
            <div class="progress-item">
              <div class="progress-item__meta"><span>互动潜力</span><strong>${likeRate}%</strong></div>
              <div class="progress-track"><div class="progress-bar" style="width:${likeRate}%"></div></div>
            </div>
            <div class="progress-item">
              <div class="progress-item__meta"><span>内容节奏</span><strong>${completion}%</strong></div>
              <div class="progress-track"><div class="progress-bar" style="width:${completion}%"></div></div>
            </div>
          </div>
        </div>
      </section>

      <section class="optimize-section">
        <div class="block-title">
          <div>
            <h4>标题优化</h4>
            <p>2 个优化后标题，可直接复制做 AB 测试</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml((data?.optimized_titles || []).join('\n'))}" data-copy-label="优化标题">一键复制</button>
        </div>
        <div class="optimize-grid">
          ${(data?.optimized_titles || []).map((title, index) => `
            <article class="copy-card">
              <div class="card-head">
                <div>
                  <div class="meta-line">优化标题 ${index + 1}</div>
                  <h4>${escapeHtml(title)}</h4>
                </div>
                <button class="copy-btn" data-copy="${escapeHtml(title)}" data-copy-label="优化标题 ${index + 1}">复制</button>
              </div>
            </article>
          `).join('')}
        </div>
      </section>

      <section class="optimize-section">
        <div class="block-title">
          <div>
            <h4>封面建议</h4>
            <p>突出核心亮点、结果感与反差感</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml(data?.cover_suggestion || '')}" data-copy-label="封面建议">复制</button>
        </div>
        <article class="dark-card">
          <p class="rich-text">${escapeHtml(data?.cover_suggestion || '')}</p>
        </article>
      </section>

      <section class="optimize-section">
        <div class="block-title">
          <div>
            <h4>内容调整</h4>
            <p>针对开头、节奏、互动点的具体可执行建议</p>
          </div>
          <button class="copy-btn" data-copy="${escapeHtml((data?.content_suggestions || []).join('\n'))}" data-copy-label="内容调整建议">复制</button>
        </div>
        <div class="comment-list">
          ${(data?.content_suggestions || []).map((item, index) => `
            <article class="optimize-card">
              <div class="card-head">
                <div>
                  <div class="meta-line">建议 ${index + 1}</div>
                  <h4>内容调整项</h4>
                </div>
              </div>
              <p>${escapeHtml(item)}</p>
            </article>
          `).join('')}
        </div>
      </section>
    </div>
  `;
}

function pipelineStep(title, badge, body, collapsed = false) {
  return `
    <article class="pipeline-step ${collapsed ? 'is-collapsed' : ''}">
      <div class="step-head">
        <div>
          <span class="agent-badge">${escapeHtml(badge)}</span>
          <h4 style="margin-top:10px;">${escapeHtml(title)}</h4>
        </div>
        <button class="step-toggle" type="button">${collapsed ? '展开' : '收起'}</button>
      </div>
      <div class="pipeline-step__body">
        ${body}
      </div>
    </article>
  `;
}

function renderPipelineResult(data) {
  return `
    <div class="pipeline-steps">
      ${pipelineStep('Step 1 · 选题分析', '选题 Agent', renderTopicResult(data?.topic_result || {}), false)}
      ${pipelineStep('Step 2 · 文案生成', '文案 Agent', renderCopyResult(data?.copywriting_result || {}), true)}
      ${pipelineStep('Step 3 · 互动运营', '运营 Agent', renderOperateResult(data?.operation_result || {}), true)}
      ${pipelineStep('Step 4 · 数据优化', '优化 Agent', renderOptimizeResult(data?.optimization_result || {}), true)}
    </div>
  `;
}

function renderToPanel(tab, html) {
  const panel = document.getElementById(`${tab}Panel`);
  if (!panel) return;
  panel.innerHTML = html;
  showEmpty(tab, false);
  bindCopyButtons(panel);
  bindStepToggles(panel);
}

function clearAllResults() {
  ['topic', 'copy', 'operate', 'optimize', 'pipeline'].forEach(tab => {
    state[tab] = null;
    const panel = document.getElementById(`${tab}Panel`);
    if (panel) panel.innerHTML = '';
    showEmpty(tab, true);
  });
  setResultMeta('暂无结果');
  setStatus('等待执行', 'idle');
  showToast('已清空', '结果区已重置', 'success');
}

async function runAction(action) {
  if (state.loading) return;

  syncFormState();
  const requestPayload = payload();
  setLoading(action, true);
  setStatus(`正在生成 ${ACTION_LABELS[action]}...`, 'loading');
  setResultMeta(`当前执行：${ACTION_LABELS[action]}`);
  switchTab(action);

  try {
    const response = await fetch(`/api/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload),
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || '请求失败');
    }

    state[action] = data.data;

    if (action === 'topic') renderToPanel('topic', renderTopicResult(data.data));
    if (action === 'copy') renderToPanel('copy', renderCopyResult(data.data));
    if (action === 'operate') renderToPanel('operate', renderOperateResult(data.data));
    if (action === 'optimize') renderToPanel('optimize', renderOptimizeResult(data.data));
    if (action === 'pipeline') renderToPanel('pipeline', renderPipelineResult(data.data));

    setStatus(`${ACTION_LABELS[action]} 已完成`, 'success');
    setResultMeta(`最近更新：${ACTION_LABELS[action]}`);
    showToast('生成完成', `${ACTION_LABELS[action]}结果已更新`, 'success');
  } catch (error) {
    setStatus(`${ACTION_LABELS[action]} 执行失败`, 'error');
    setResultMeta('执行失败，请检查输入或稍后重试');
    showToast('执行失败', error.message || '发生未知错误', 'error');
  } finally {
    setLoading(action, false);
  }
}

function initTabs() {
  $all('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function initActions() {
  $all('.action-btn').forEach(btn => {
    btn.addEventListener('click', () => runAction(btn.dataset.action));
  });

  $('#clearResultsBtn').addEventListener('click', clearAllResults);
}

function bindFormState() {
  const formBindings = {
    biliLink: value => {
      state.form.bili_link = value.trim();
      handleBiliLinkInput(state.form.bili_link);
    },
    partition: value => {
      state.form.partition_name = value.trim();
    },
    upIds: value => {
      state.form.up_ids = parseUpIds(value);
    },
    style: value => {
      state.form.style = value;
    },
    bvId: value => {
      state.form.bv_id = value.trim();
    },
    topic: value => {
      state.form.manual_topic = value.trim();
    },
  };

  Object.entries(formBindings).forEach(([id, updater]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const handler = event => updater(event.target.value);
    el.addEventListener('input', handler);
    el.addEventListener('change', handler);
    updater(el.value);
  });

  syncFormState();
}

function init() {
  bindFormState();
  initTabs();
  initActions();
  setStatus('等待执行', 'idle');
  setResultMeta('暂无结果');
}

init();
