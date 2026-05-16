#!/bin/bash
# heartscale daily: extract + compress + render recent sections
# Runs at 00:05 daily, after diary cron (23:59)
#
# 这是墨星 (Hermes Agent) 的 HeartScale 日处理脚本。
# 配合 emotional-diary-system 使用，
# 每日从新日记中提取情感数据、压缩、渲染 HEART.md。

set -e

HEARTSCALE="/usr/local/bin/heartscale"
CONFIG="/tmp/heartscale/config.yaml"

# Source env vars (HEARTSCALE_* variables)
export $(grep -v '^#' /root/.hermes/.env | grep -E '^HEARTSCALE_' | xargs 2>/dev/null)

echo "[heartscale-daily] $(date -Iseconds) starting"

# Step 1: Extract emotions from new diary entries
# 修改为你的日记目录路径
DIARY_DIR="/home/hermes/obsidian-vault/墨星日记"
NEW_COUNT=0
for f in $(find "$DIARY_DIR" -name "*.md" -mtime -1 2>/dev/null | sort); do
    BASENAME=$(basename "$f")
    # Skip already-processed (check if in limbic.jsonl)
    if [ -f /root/.hermes/limbic.jsonl ] && grep -q "$BASENAME" /root/.hermes/limbic.jsonl 2>/dev/null; then
        continue
    fi
    echo "[heartscale-daily] extracting $BASENAME"
    $HEARTSCALE --config "$CONFIG" extract --diary "$f" 2>&1
    NEW_COUNT=$((NEW_COUNT + 1))
done

if [ $NEW_COUNT -gt 0 ]; then
    echo "[heartscale-daily] $NEW_COUNT new diary files processed"
    # Step 2: Daily compression
    echo "[heartscale-daily] compressing..."
    $HEARTSCALE --config "$CONFIG" compress --daily 2>&1
    # Step 3: Render daily sections
    echo "[heartscale-daily] rendering..."
    $HEARTSCALE --config "$CONFIG" render --sections "recent_7d,recent_1m" 2>&1
    echo "[heartscale-daily] done — HEART.md updated"
else
    echo "[heartscale-daily] no new diaries, skipping"
fi
