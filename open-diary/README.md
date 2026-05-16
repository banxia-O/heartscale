# open-diary: 墨星日记系统开源版

墨星 (Hermes Agent) 的情感日记系统核心文件，脱敏后开源。

## 文件说明

| 文件 | 用途 |
|------|------|
| `pre-run.py` | cron-mood 决策脚本。每小时触发，根据时段/silence时长/mood状态决定是否主动发消息。SILENT → 跳过LLM零token，SEND → 注入记忆文件唤醒LLM |
| `heartscale-daily.sh` | HeartScale 日处理脚本。在日记cron (23:59) 之后运行 (00:05)，提取新日记情感数据 → 压缩 → 渲染 HEART.md |
| `diary-cron-prompt.md` | 每日日记 cron job 的完整 prompt。定义了日记的写入规则、证据约束、YAML frontmatter 打标、自然语言输出格式 |

## 架构

```
cron-mood (@hourly)
  │  pre-run.py → 决策 SILENT/SEND
  │
cron-日记 (59 23 * * *)
  │  LLM → session_search(今日对话) → 自检 → 写入 Obsidian
  │
heartscale-daily (5 0 * * *)
  │  heartscale-daily.sh → extract → compress → render HEART.md
  │
Syncthing → 手机 Obsidian
```

## 使用前提

- **Hermes Agent** 运行中（需要 `state.db` 和 `memories/` 目录）
- **Obsidian Vault** 通过 Syncthing 同步到 VPS
- **HeartScale** 已安装（`pip install heartscale`）
- cron job 配置在 Hermes Agent 中，不在此仓库

## 脱敏说明

以下内容已从文件中移除：
- 用户真实姓名、职业信息
- API keys / tokens
- 硬编码的个人路径（替换为 `~/.hermes/` 和占位符注释）
- 具体的 IP 地址和服务器配置

核心决策逻辑、数学公式、时段划分规则保持完整。

## 相关

- [墨星](https://github.com/banxia-O) — Hermes Agent 实例
- [HeartScale](https://github.com/banxia-O/heartscale) — 情感记忆压缩引擎
