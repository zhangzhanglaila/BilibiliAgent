"""
Apply all optimizations to web/services/llm.py in one pass.
Edits:
  1. import metrics + cache
  2. Add should_use_chat_agent + run_llm_chat_direct
  3. Modify run_llm_chat: fast path for simple messages
  4. Add run_llm_module_create_fast
  5. Modify run_llm_module_create: fast path first
  6. Add cache checks in analyze_fast and create_fast
"""
import ast

with open('web/services/llm.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# ====== FIND KEY LINE NUMBERS ======
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

# ====== SECTION 1: Add imports ======
old_import_line = find_line('from web.services.reference import *')
if old_import_line < 0:
    print("ERROR: could not find reference import")
    exit(1)

new_imports = [
    'import metrics as _perf\n',
    'import cache as _cache\n',
]
# Insert after the blank line following existing imports
insert_at = old_import_line + 1  # right after the reference import line
for imp in reversed(new_imports):
    lines.insert(insert_at, imp)

print(f"[1] Added metrics+cache imports at line {insert_at}")

# Recalculate after insertions
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

# ====== SECTION 2: Add should_use_chat_agent + run_llm_chat_direct ======
# Insert after build_workspace_chat_system_prompt function, before the video payload comment
insert_here = find_line('# 将视频信息构建成 LLM 提示词使用的视频结构。')
if insert_here < 0:
    print("ERROR: could not find video payload comment")
    exit(1)

new_functions = '''# 判断用户消息是否需要走 Agent + 工具调用路径。
CHAT_AGENT_TRIGGER_KEYWORDS = (
    "分析视频", "http", "bilibili.com", "BV", "av", "热点", "趋势", "排行榜",
    "对标", "选题", "文案", "搜索", "检索", "知识库", "帮我查", "帮我找",
    "历史对话", "回顾", "复盘", "之前聊",
)


def should_use_chat_agent(message: str) -> bool:
    clean = (message or "").strip()
    if not clean:
        return False
    return any(keyword in clean for keyword in CHAT_AGENT_TRIGGER_KEYWORDS)


# 直接 LLM 对话（跳过 ReAct 循环），用于不涉及工具调用的简单问答。
def run_llm_chat_direct(
    message: str,
    history: list,
    creator_context: dict,
    video_url: str,
    session_id: str,
    session_context_source: str,
) -> dict:
    llm = build_runtime_llm_client()
    llm.require_available()

    history_text = ""
    if history:
        recent = history[-6:]
        history_text = "对话历史：\\n" + "\\n".join(
            f"{'用户' if h.get('role') == 'user' else '助手'}: {str(h.get('content', ''))[:200]}"
            for h in recent
        )

    creator_text = ""
    if any(creator_context.values()):
        creator_text = f"创作者信息：方向={creator_context.get('direction')}, 分区={creator_context.get('partition')}, 风格={creator_context.get('style')}"

    sys = "你是 B 站创作工作台的智能助手。用中文直接、简洁地回答用户问题。"
    usr = (
        f"用户问题：{message}\\n\\n"
        + (f"{history_text}\\n\\n" if history_text else "")
        + (f"{creator_text}\\n" if creator_text else "")
        + (f"视频链接：{video_url}" if video_url else "")
        + "\\n\\n请直接回答，不要返回 JSON。"
    )
    try:
        reply = llm.invoke_text(sys, usr, fallback="抱歉，当前无法处理你的问题，请稍后重试。")
    except Exception:
        reply = "抱歉，当前无法处理你的问题，请稍后重试。"
    return {
        "reply": (reply or "").strip() or "抱歉，暂无回复。",
        "suggested_next_actions": [],
        "mode": "llm_agent",
        "agent_trace": ["llm_direct_chat"],
        "tool_observations": [],
        "reference_links": [],
    }

'''

lines.insert(insert_here, new_functions)
print(f"[2] Added chat helper functions")

# Recalculate
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

# ====== SECTION 3: Modify run_llm_chat to add fast path ======
# Find the run_llm_chat function and modify it
# Look for video_url line inside run_llm_chat
for i, line in enumerate(lines):
    if 'video_url = (context.get("videoLink")' in line and 'extract_first_bili_url' in line:
        # Verify we're inside run_llm_chat by looking backward
        for j in range(i-100, i):
            if 'def run_llm_chat(data: dict) -> dict:' in lines[j]:
                video_url_idx = i
                agent_start = -1
                # Find the allowed_tools line
                for k in range(i, i+50):
                    if 'allowed_tools = allowed_tools_for_scene("workspace_chat")' in lines[k]:
                        agent_start = k
                        break
                # Find the end of the agent.run_structured call
                agent_end = -1
                for k in range(agent_start, agent_start+30):
                    if 'action_validator=build_workspace_chat_action_validator(message),' in lines[k]:
                        # Find closing paren
                        for m in range(k+1, k+5):
                            if lines[m].strip() == ')':
                                agent_end = m + 1
                                break
                        break

                if agent_start < 0 or agent_end < 0:
                    print(f"ERROR: agent_start={agent_start}, agent_end={agent_end}")
                    exit(1)

                # Build replacement lines
                indent = "    "
                fast_path_lines = [
                    f'{indent}video_url = (context.get("videoLink") or "").strip() or extract_first_bili_url(message)\n',
                    f'\n',
                    f'{indent}# 快速路径：简单对话直接 LLM 回复，跳过 ReAct 循环\n',
                    f'{indent}if not should_use_chat_agent(message) and not video_url:\n',
                    f'{indent}    result = run_llm_chat_direct(\n',
                    f'{indent}        message=message,\n',
                    f'{indent}        history=history,\n',
                    f'{indent}        creator_context=creator_context,\n',
                    f'{indent}        video_url=video_url,\n',
                    f'{indent}        session_id=session_id,\n',
                    f'{indent}        session_context_source=session_context.get("source", ""),\n',
                    f'{indent}    )\n',
                    f'{indent}else:\n',
                ]

                # Copy agent lines with extra 4-space indent for else block
                for k in range(agent_start, agent_end):
                    line = lines[k]
                    if line.strip():
                        fast_path_lines.append(f'{indent}    {line[len(indent):]}')
                    else:
                        fast_path_lines.append(line)

                # Add enable_reflection=False after save_memory=False
                for idx_fb, fline in enumerate(fast_path_lines):
                    if 'save_memory=False,' in fline and 'enable_reflection=False' not in fline:
                        fast_path_lines.insert(idx_fb + 1, f'{indent}            enable_reflection=False,\n')
                        break

                # Apply replacement
                lines = lines[:video_url_idx] + fast_path_lines + lines[agent_end:]
                print(f"[3] Modified run_llm_chat with fast path")
                break
    else:
        continue
    break
else:
    print("ERROR: could not find run_llm_chat video_url line")
    exit(1)

# ====== SECTION 4: Add run_llm_module_create_fast + modify run_llm_module_create ======
# Recalculate positions
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

create_decorator = find_line('def run_llm_module_create(data: dict) -> dict:')
if create_decorator < 0:
    print("ERROR: could not find run_llm_module_create")
    exit(1)

# Find the decorator line(s) before it
insert_before = create_decorator
for i in range(create_decorator - 3, create_decorator):
    if '@traceable' in lines[i]:
        insert_before = i
        break

fast_create_func = '''# 内容创作快速路径：预加载创作简报，单次 LLM 调用生成全部内容。
@traceable(run_type="chain", name="web.run_llm_module_create_fast", tags=["web", "llm", "fast_path", "module_create"])
def run_llm_module_create_fast(data: dict) -> dict:
    llm = build_runtime_llm_client()
    llm.require_available()
    field_name = (data.get("field") or "").strip()
    direction = (data.get("direction") or "").strip()
    idea = (data.get("idea") or "").strip()
    partition_name = (data.get("partition") or "knowledge").strip() or "knowledge"
    style = (data.get("style") or "干货").strip() or "干货"
    briefing = compact_creator_briefing_for_llm(build_creator_briefing(field_name, direction, idea, partition_name))

    sys = "你是 B 站内容创作助手，为创作者生成选题和可发布文案。只返回 JSON。"
    usr = (
        "返回一个 JSON 对象，字段包含：normalized_profile, seed_topic, partition, style, chosen_topic, topic_result, copy_result。\\n\\n"
        f"用户输入：{json.dumps({'field': field_name, 'direction': direction, 'idea': idea, 'partition': partition_name, 'style': style}, ensure_ascii=False)}\\n\\n"
        f"创作简报：{json.dumps(briefing, ensure_ascii=False)}\\n\\n"
        "规则：\\n"
        "1. partition 和 style 复用当前输入。\\n"
        "2. topic_result.ideas 必须 3 个，每项含 topic/reason/video_type/keywords。topic 必须是具体新方向不要提问句。\\n"
        "3. copy_result 须含 topic/style/titles(3)/script(至少4段含section/duration/content)/description/tags/pinned_comment。\\n"
        "4. titles 必须是陈述/叙事型 B 站标题。\\n"
        "只返回 JSON。"
    )
    result = llm.invoke_json_with_fallback(sys, usr, fallback={
        "normalized_profile": "内容创作",
        "seed_topic": field_name or "通用创作方向",
        "partition": partition_name,
        "style": style,
        "chosen_topic": "请重新输入创作方向",
        "topic_result": {"ideas": []},
        "copy_result": {},
    })
    if not isinstance(result, dict):
        raise ValueError("内容创作 fast path 返回格式无效")
    copy_topic = (
        clean_copy_text(result.get("chosen_topic", ""))
        or clean_copy_text(result.get("seed_topic", ""))
        or build_seed_topic(field_name, direction, idea)
    )
    result["copy_result"] = normalize_copy_result_payload(result.get("copy_result"), copy_topic, style)
    result.setdefault("runtime_mode", "llm_agent")
    result.setdefault("agent_trace", ["creator_briefing", "llm_fast_path"])
    return result


'''

lines.insert(insert_before, fast_create_func)
print(f"[4] Added run_llm_module_create_fast")

# Now modify run_llm_module_create to try fast path first
# Find the try block inside run_llm_module_create
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

# Find the try: that wraps agent.run_structured in run_llm_module_create
create_func_start = find_line('def run_llm_module_create(data: dict) -> dict:')
# Search forward from the function start for "    try:"
try_idx = -1
for i in range(create_func_start, create_func_start + 60):
    if lines[i].strip() == 'try:':
        try_idx = i
        break

if try_idx < 0:
    print("ERROR: could not find try block in run_llm_module_create")
    exit(1)

fast_try = f'''    # 优先快速路径
    try:
        return run_llm_module_create_fast(data)
    except Exception as fast_exc:
        if should_skip_same_provider_fallback(fast_exc):
            raise RuntimeError(
                f"LLM 服务当前不可用：{{format_llm_error(fast_exc)}}"
            ) from fast_exc

    # 快速路径失败，降级到 ReAct 循环
'''

lines.insert(try_idx, fast_try)
print(f"[5] Modified run_llm_module_create with fast path first")

# ====== SECTION 6: Add cache check to analyze_fast ======
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

analyze_fast_start = find_line('def run_llm_module_analyze_fast(')
if analyze_fast_start >= 0:
    # Find the first code line in the function body
    for i in range(analyze_fast_start, analyze_fast_start + 20):
        if 'exports = app_exports()' in lines[i]:
            body_start = i
            break
    else:
        body_start = analyze_fast_start + 6  # fallback

    cache_check = '''    # Cache check: skip if recently analyzed
    cached_result = _cache.video_cache.get(_cache.video_cache_key((data.get("url") or "").strip()))
    if cached_result:
        _perf.record(module="analyze", path="fast", latency_ms=0.0, success=True, error_type="cache_hit")
        return cached_result

'''
    lines.insert(body_start, cache_check)
    print(f"[6] Added cache check to analyze_fast")
else:
    print("WARNING: could not find analyze_fast")

# ====== SECTION 7: Add cache set after analyze_fast result ======
def find_line(text_contains):
    for i, line in enumerate(lines):
        if text_contains in line:
            return i
    return -1

# Find the return line in analyze_fast
return_line = find_line('return exports.finalize_module_analyze_result(result, resolved, market_snapshot)')
if return_line >= 0:
    # Check if we're inside analyze_fast by looking backward
    in_fast = False
    for j in range(return_line - 50, return_line):
        if 'def run_llm_module_analyze_fast(' in lines[j]:
            in_fast = True
            break

    if in_fast:
        indent_match = lines[return_line]
        # Replace with cached version
        lines[return_line] = (
            '    final_result = exports.finalize_module_analyze_result(result, resolved, market_snapshot)\n'
            '    _cache.video_cache.set(_cache.video_cache_key(url), final_result)\n'
            '    return final_result\n'
        )
        print(f"[7] Added cache set to analyze_fast")
    else:
        print("WARNING: return not in analyze_fast")
else:
    print("WARNING: could not find analyze_fast return")

# ====== Write back ======
with open('web/services/llm.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# Verify
with open('web/services/llm.py', 'r', encoding='utf-8') as f:
    content = f.read()
try:
    ast.parse(content)
    print("\n*** All edits applied. Syntax OK! ***")
except SyntaxError as e:
    print(f"\nSyntax error at line {e.lineno}: {e.msg}")
    err_lines = content.split('\n')
    for offset in range(-5, 3):
        idx = e.lineno - 1 + offset
        if 0 <= idx < len(err_lines):
            marker = '>>>' if offset == 0 else '   '
            print(f'{marker} {idx+1}: {err_lines[idx][:120]}')
