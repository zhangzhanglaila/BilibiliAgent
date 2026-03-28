const state = {
  loadingKey: '',
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
    <div class="stat-card">
      <h4>${escapeHtml(label)}</h4>
      <span class="stat-card__value">${escapeHtml(value)}</span>
      ${hint ? `<p>${escapeHtml(hint)}</p>` : ''}
    </div>
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
      ${headerSummary}
      ${renderVideoMetrics(resolved)}
      ${hotSection}
      ${lowSection}
    </div>
  `;
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

    const data = await requestJson('/api/module-create', {
      field,
      direction,
      idea,
      partition,
      style,
    });

    const container = $('#creatorResult');
    if (container) {
      container.innerHTML = renderCreatorResult(data);
      bindCopyButtons(container);
    }

    setStatus('模块一结果已更新', 'success');
    showToast('生成完成', '已生成选题与文案结果', 'success');
  } catch (error) {
    setStatus('模块一执行失败', 'error');
    showToast('生成失败', error.message || '发生未知错误', 'error');
  } finally {
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
    state.loadingKey = 'video';
    setButtonLoading('videoAnalyzeBtn', true);
    setStatus('正在解析并分析视频...', 'loading');

    const data = await requestJson('/api/module-analyze', { url });
    const container = $('#videoResult');
    if (container) {
      container.innerHTML = renderVideoResult(data);
      bindCopyButtons(container);
    }

    setStatus('模块二结果已更新', 'success');
    showToast('分析完成', '已返回视频解析和优化结果', 'success');
  } catch (error) {
    setStatus('模块二执行失败', 'error');
    showToast('分析失败', error.message || '发生未知错误', 'error');
  } finally {
    state.loadingKey = '';
    setButtonLoading('videoAnalyzeBtn', false);
  }
}

function clearAllResults() {
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

  setStatus('等待操作', 'idle');
  showToast('已清空', '两个模块的结果区都已重置', 'success');
}

function init() {
  $('#creatorRunBtn')?.addEventListener('click', runCreatorModule);
  $('#videoAnalyzeBtn')?.addEventListener('click', runVideoModule);
  $('#clearResultsBtn')?.addEventListener('click', clearAllResults);
  setStatus('等待操作', 'idle');
}

init();
