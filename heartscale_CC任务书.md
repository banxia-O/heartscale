# heartscale — CC 开发任务书

## 你是谁

你在帮半夏（编程小白）把一份 AI 人格记忆架构设计落地为可运行的 Python 开源项目。

## 项目目标

**heartscale** 是一个开源工具，让 AI agent 拥有类人的情感记忆系统：日记 → 情感抽取 → 分层压缩 → 自然语言心境文件，注入 agent identity slot。

仓库地址待建，目标发布到 GitHub。

## 第一步：通读架构文档

项目根目录下有 `docs/ARCHITECTURE.md`（即附件「墨星_HEART_架构设计_v2.1.md」的脱敏版）。

**请先完整阅读这份文档，然后自行规划实现步骤。**

不要一口气写完所有代码。请拆成多个明确的步骤，每步有可验证的产出，一步完成并测试通过后再进入下一步。

## 核心约束

### 1. 开源脱敏

文档中所有写死的具体服务（硅基流动、Qwen3-Embedding-8B、DS Flash）必须抽象为可配置项：

```yaml
# config.yaml 示例
embedding:
  provider: "openai"  # 或 siliconflow / local / 任意 OpenAI 兼容 API
  model: "text-embedding-3-small"
  api_key_env: "HEARTSCALE_EMBEDDING_KEY"
  dimension: 1024

judge:
  provider: "openai"  # 或 deepseek / anthropic / 任意 OpenAI 兼容 API
  model: "gpt-4o-mini"
  api_key_env: "HEARTSCALE_JUDGE_KEY"
```

用户只需填自己的 provider 和 key 就能跑。默认配置用 OpenAI 兼容格式，降低上手门槛。

### 2. 冷启动友好

提供种子数据模板（`seeds/example.jsonl`），让用户手写 10-15 条种子记忆就能启动，不依赖历史数据。

### 3. 半夏是编程小白

README 要写清楚：怎么装、怎么配、怎么跑第一次。假设读者只会 `pip install` 和改 yaml。

### 4. 最小依赖

512M VPS 要能跑。SQLite + sqlite-vec 本地存储，不引入 Redis、Postgres 等重依赖。

## 技术栈

- Python 3.10+
- SQLite + sqlite-vec
- YAML 配置
- cron 调度（系统 crontab 或内置 scheduler，你来定）

## 仓库结构建议（你可以调整）

```
heartscale/
├── README.md
├── config.yaml.example
├── docs/
│   └── ARCHITECTURE.md
├── seeds/
│   └── example.jsonl
├── heartscale/
│   ├── __init__.py
│   ├── config.py          # 配置加载
│   ├── extract.py         # 情感抽取（日记 → limbic 条目）
│   ├── limbic.py          # 级联压缩（各层筛选、合并、淘汰）
│   ├── render.py          # HEART.md 渲染（limbic → 自然语言）
│   ├── db.py              # SQLite + sqlite-vec 操作
│   ├── providers/         # embedding / judge 的 provider 适配
│   │   ├── base.py
│   │   ├── openai_compat.py
│   │   └── ...
│   └── scheduler.py       # cron 调度逻辑
├── tests/
│   └── ...
└── pyproject.toml
```

## 关键提醒

- 架构文档第三节的「级联压缩筛选规则」是 v2.1 新增的核心逻辑，务必仔细看
- 第十节「记忆保护」机制要贯穿所有压缩步骤，protected 条目跳过一切衰减和淘汰
- HEART.md 是纯自然语言输出，不能泄露任何元数据（intensity、flavor 等标签）
- 局部更新而非全量重写——只重写变化的时间段

## 你的输出

1. 先给出你的实现计划（分几步、每步做什么、怎么验证）
2. 等我确认后再开始写代码
3. 每步完成后暂停，等我确认再继续
