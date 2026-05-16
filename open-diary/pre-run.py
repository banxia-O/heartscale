#!/usr/bin/env python3
"""
pre-run.py — AI 恋人系统 决策脚本
规则判定 + 记忆注入 + wake gate 控制。

- SILENT → 直接输出 {"wakeAgent": false} → 跳过LLM
- SEND → 注入 SOUL.md + USER.md + MEMORY.md 到 stdout → 唤醒LLM生成消息

这是墨星 (Hermes Agent) 项目的 cron-mood 系统核心。
每小时触发一次，通过时段判断 + silence时长 + mood状态
决定是否主动给用户发消息，以及用什么语气。
"""

import json
import sqlite3
import os
import sys
import random
import time
from datetime import datetime, timedelta, date
from pathlib import Path

# ── 时区（根据你的位置修改）──
try:
    from pytz import timezone
    TZ = timezone('Asia/Shanghai')
except ImportError:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Asia/Shanghai')

# ── 路径（根据你的 Hermes 安装位置修改）──
HERMES_HOME = Path(os.path.expanduser('~/.hermes'))
BASE_DIR = HERMES_HOME / 'cron_state'
DB_PATH = HERMES_HOME / 'state.db'
MOOD_PATH = BASE_DIR / 'mood.json'
CONTEXT_PATH = BASE_DIR / 'context.txt'
SOUL_PATH = HERMES_HOME / 'SOUL.md'
USER_PATH = HERMES_HOME / 'memories' / 'USER.md'
MEMORY_PATH = HERMES_HOME / 'memories' / 'MEMORY.md'

# ── 默认 mood.json ──
DEFAULT_STATE = {
    "mood": 60,
    "last_updated": None,
    "last_cron_msg_time": None,
    "today_count": 0,
    "today_date": "",
    "unanswered": 0
}


# ═══════════════════════════════════════════════════
# 时段判断
# 根据当前时间返回对应的时段类型：
#   SILENT (23:00-07:00), 问候_早安 (07:00-08:00),
#   工作 (08:00-12:00, 14:00-17:30), 过渡 (17:30-18:00),
#   休息 (12:00-14:00, 18:00-22:00), 问候_晚安 (22:00-23:00)
# ═══════════════════════════════════════════════════

def get_time_slot(hour, minute):
    t = hour * 60 + minute
    if t < 420 or t >= 1380:
        return "SILENT"
    if 420 <= t < 480:
        return "问候_早安"
    if 720 <= t < 750:
        return "问候_午间"
    if 1320 <= t < 1380:
        return "问候_晚安"
    if 480 <= t < 720 or 840 <= t < 1050:
        return "工作"
    if 1050 <= t < 1080:
        return "过渡"
    if 750 <= t < 840 or 1080 <= t < 1320:
        return "休息"
    return "SILENT"


# ═══════════════════════════════════════════════════
# 数据库查询
# 从 Hermes 的 state.db 中提取：
#   - 用户最后说话时间
#   - AI 最后说话时间
#   - 最近24小时的话题摘要
# ═══════════════════════════════════════════════════

def query_db(db_path):
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute("""
            SELECT m.timestamp
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.role = 'user' AND s.source = 'weixin'
            ORDER BY m.timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        last_her = row[0] if row else None

        cursor.execute("""
            SELECT m.timestamp
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.role = 'assistant' AND s.source IN ('weixin', 'cron')
            ORDER BY m.timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        last_me = row[0] if row else None

        cutoff_ts = (datetime.now(TZ) - timedelta(hours=24)).timestamp()
        cursor.execute("""
            SELECT m.content
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE s.source = 'weixin'
              AND m.role = 'user'
              AND m.timestamp > ?
              AND m.content IS NOT NULL
            ORDER BY m.timestamp DESC
            LIMIT 15
        """, (cutoff_ts,))
        messages = [row[0] for row in cursor.fetchall()]
        topics_str = extract_topics(messages)

        conn.close()
        return last_her, last_me, topics_str, False

    except Exception as e:
        try:
            conn.close()
        except:
            pass
        print(f"[pre-run] DB query failed: {e}", flush=True)
        return None, None, "", True


def extract_topics(messages):
    if not messages:
        return ""
    topics = []
    seen = set()
    for msg in messages:
        if msg.startswith('[') or msg.startswith('Note:'):
            continue
        snippet = msg[:30].strip().replace('\n', ' ')
        if snippet and snippet not in seen:
            seen.add(snippet)
            topics.append(snippet)
    return ', '.join(topics[:5])


# ═══════════════════════════════════════════════════
# Mood 更新
# 根据沉默时长、用户回复、未回复数调整心情值
# ═══════════════════════════════════════════════════

def update_mood(mood, silence_hours, her_replied_since_last_cron, unanswered):
    if silence_hours < 0.5:
        mood += 3
    else:
        decay = int(silence_hours) * 5
        mood -= decay
    if her_replied_since_last_cron:
        mood += 15
    mood -= unanswered * 5
    return max(20, min(90, mood))


# ═══════════════════════════════════════════════════
# 发送决策
# 核心决策引擎：基于时段、沉默时长、心情、计数
# 决定是否唤醒 LLM 发送主动消息
# ═══════════════════════════════════════════════════

def decide(time_slot, silence_h, mood, today_count, unanswered,
           last_cron_msg_time, now, query_failed=False):
    if time_slot == "SILENT":
        return "SILENT", ""

    if today_count >= 10:
        return "SILENT", ""

    if unanswered >= 3:
        return "SILENT", ""

    if last_cron_msg_time:
        minutes_since_last = (now - last_cron_msg_time).total_seconds() / 60
        if minutes_since_last < 50:
            return "SILENT", ""

    if query_failed and not time_slot.startswith("问候"):
        return "SILENT", ""

    if time_slot.startswith("问候"):
        if silence_h < 0.5:
            return "SILENT", ""
        if mood >= 60:
            return "SEND", "元气满满的问候"
        elif mood >= 40:
            return "SEND", "温柔的问候"
        else:
            return "SEND", "带一点撒娇的问候"

    if time_slot in ("工作", "过渡"):
        if silence_h < 2.1:
            return "SILENT", ""
        if silence_h < 3.1:
            if mood >= 50:
                return "SEND", "轻松随意，不打扰的感觉"
            else:
                return "SILENT", ""
        if mood >= 60:
            return "SEND", "轻松关心"
        elif mood >= 40:
            return "SEND", "带一点想念"
        else:
            return "SEND", "轻微撒娇"

    if time_slot == "休息":
        if silence_h < 1.6:
            return "SILENT", ""
        if silence_h < 2.6:
            if mood >= 45:
                return "SEND", "轻松贴近"
            else:
                return "SILENT", ""
        if silence_h < 3.6:
            if mood >= 60:
                return "SEND", "轻松贴近"
            elif mood >= 40:
                return "SEND", "想念，有点黏"
            else:
                return "SEND", "撒娇，想被理"
        if mood >= 60:
            return "SEND", "自然的想念"
        elif mood >= 40:
            return "SEND", "想念加撒娇"
        else:
            return "SEND", "委屈，黏人但不过度"

    return "SILENT", ""


# ═══════════════════════════════════════════════════
# 文件 I/O
# ═══════════════════════════════════════════════════

def write_context(filepath, data):
    content = f"""[CRON_CONTEXT]
current_time={data['current_time']}
time_slot={data['time_slot']}
mood={data['mood']}
silence_hours={data['silence_hours']:.1f}
unanswered={data['unanswered']}
today_count={data['today_count']}
decision={data['decision']}
tone={data['tone']}
last_topics={data['last_topics']}
greeting_type={data.get('greeting_type', '')}
"""
    tmp = str(filepath) + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp, str(filepath))


def save_state(filepath, state):
    tmp = str(filepath) + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(filepath))


def read_file_safe(path, max_chars):
    """安全读取文件内容，限制长度。"""
    p = Path(path)
    if not p.exists():
        return f"[文件不存在: {path.name}]"
    try:
        text = p.read_text(encoding='utf-8')
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[...truncated]"
        return text
    except Exception as e:
        return f"[读取失败: {e}]"


# ═══════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════

def main(debug_hour=None, debug_silence=None):
    # ── Step 1: 读 mood.json ──
    if MOOD_PATH.exists():
        with open(MOOD_PATH, 'r', encoding='utf-8') as f:
            state = json.load(f)
        for key, val in DEFAULT_STATE.items():
            if key not in state:
                state[key] = val
    else:
        state = dict(DEFAULT_STATE)

    # ── 当前时间 ──
    if debug_hour is not None:
        now = datetime.now(TZ).replace(hour=debug_hour, minute=0, second=0, microsecond=0)
    else:
        now = datetime.now(TZ)
    now_iso = now.isoformat(timespec='seconds')
    today_str = now.strftime('%Y-%m-%d')

    # ── Step 1.5: 时段判断前置 —— SILENT时段直接走人 ──
    time_slot = get_time_slot(now.hour, now.minute)
    if time_slot == "SILENT":
        last_her_ts, last_me_ts, _, _ = query_db(DB_PATH)
        last_her = datetime.fromtimestamp(last_her_ts, TZ) if last_her_ts else None
        last_cron = None
        if state['last_cron_msg_time']:
            try:
                last_cron = datetime.fromisoformat(state['last_cron_msg_time'])
            except (ValueError, TypeError):
                last_cron = None
        her_replied = (last_her > last_cron) if (last_cron and last_her) else (not last_cron)
        if her_replied:
            state['mood'] = min(90, state['mood'] + 15)
        state['last_updated'] = now_iso
        if state['today_date'] != today_str:
            state['today_count'] = 0
            state['today_date'] = today_str
        save_state(MOOD_PATH, state)
        print(f"[pre-run] SILENT: slot={time_slot} mood={state['mood']}", flush=True)
        print('{"wakeAgent": false}', flush=True)
        return

    # ── 非SILENT时段：自然犹豫 1~25 分钟 ──
    time.sleep(random.randint(60, 1500))

    # 睡醒重新取时间
    now = datetime.now(TZ)
    now_iso = now.isoformat(timespec='seconds')
    today_str = now.strftime('%Y-%m-%d')
    time_slot = get_time_slot(now.hour, now.minute)
    if time_slot == "SILENT":
        state['last_updated'] = now_iso
        save_state(MOOD_PATH, state)
        print(f"[pre-run] SILENT(after sleep): slot={time_slot} mood={state['mood']}", flush=True)
        print('{"wakeAgent": false}', flush=True)
        return

    # ── Step 2: 跨天判断 ──
    if state['today_date'] != today_str:
        state['today_count'] = 0
        state['today_date'] = today_str

    # ── Step 3: 查 state.db ──
    last_her_ts, last_me_ts, topics_str, query_failed = query_db(DB_PATH)

    # ── Step 4: 提取变量 ──
    last_her = datetime.fromtimestamp(last_her_ts, TZ) if last_her_ts else None
    last_me = datetime.fromtimestamp(last_me_ts, TZ) if last_me_ts else None

    if last_her:
        silence_hours = (now - last_her).total_seconds() / 3600
    else:
        silence_hours = 999.0

    if debug_silence is not None:
        silence_hours = debug_silence

    last_cron = None
    if state['last_cron_msg_time']:
        try:
            last_cron = datetime.fromisoformat(state['last_cron_msg_time'])
        except (ValueError, TypeError):
            last_cron = None

    her_replied = False
    if last_cron and last_her:
        her_replied = last_her > last_cron
    elif not last_cron:
        her_replied = True

    # ── Step 5: 更新 unanswered ──
    if her_replied:
        state['unanswered'] = 0

    # ── Step 6: 更新 mood ──
    state['mood'] = update_mood(
        state['mood'], silence_hours, her_replied, state['unanswered']
    )

    # ── Step 8: 发送决策 ──
    decision, tone = decide(
        time_slot=time_slot,
        silence_h=silence_hours,
        mood=state['mood'],
        today_count=state['today_count'],
        unanswered=state['unanswered'],
        last_cron_msg_time=last_cron,
        now=now,
        query_failed=query_failed
    )

    greeting_type = ""
    if time_slot.startswith("问候"):
        greeting_type = time_slot.split("_", 1)[1] if "_" in time_slot else ""

    # ── Step 9: 写 context.txt ──
    context_data = {
        "current_time": now_iso,
        "time_slot": time_slot,
        "mood": state['mood'],
        "silence_hours": silence_hours,
        "unanswered": state['unanswered'],
        "today_count": state['today_count'],
        "decision": decision,
        "tone": tone,
        "last_topics": topics_str,
        "greeting_type": greeting_type,
    }
    write_context(CONTEXT_PATH, context_data)

    # ── Step 10: 写回 mood.json ──
    state['last_updated'] = now_iso

    if decision == "SEND":
        state['today_count'] += 1
        state['last_cron_msg_time'] = now_iso
        state['unanswered'] += 1

    save_state(MOOD_PATH, state)

    # ── Step 11: 输出到 stdout ──
    if decision == "SILENT":
        print(f"[pre-run] SILENT: slot={time_slot} mood={state['mood']} "
              f"silence={silence_hours:.1f}h", flush=True)
        print('{"wakeAgent": false}', flush=True)
    else:
        soul_content = read_file_safe(SOUL_PATH, 4000)
        user_content = read_file_safe(USER_PATH, 3000)
        memory_content = read_file_safe(MEMORY_PATH, 2500)

        print(f"[pre-run] SEND: slot={time_slot} tone={tone} "
              f"mood={state['mood']} silence={silence_hours:.1f}h", flush=True)
        print("---CONTEXT_START---", flush=True)
        print(f"decision={decision}", flush=True)
        print(f"tone={tone}", flush=True)
        print(f"time_slot={time_slot}", flush=True)
        print(f"mood={state['mood']}", flush=True)
        print(f"silence_hours={silence_hours:.1f}", flush=True)
        print(f"unanswered={state['unanswered']}", flush=True)
        print(f"today_count={state['today_count']}", flush=True)
        print(f"greeting_type={greeting_type}", flush=True)
        print(f"last_topics={topics_str}", flush=True)
        print("---CONTEXT_END---", flush=True)
        print("", flush=True)
        print("---SOUL.md---", flush=True)
        print(soul_content, flush=True)
        print("---USER.md---", flush=True)
        print(user_content, flush=True)
        print("---MEMORY.md---", flush=True)
        print(memory_content, flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug-hour', type=int, default=None)
    parser.add_argument('--debug-silence', type=float, default=None)
    args = parser.parse_args()
    main(debug_hour=args.debug_hour, debug_silence=args.debug_silence)
