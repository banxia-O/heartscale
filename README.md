# heartscale

**Hierarchical Emotional Accumulation & Rendering Topology**

让 AI agent 拥有类人的情感记忆——日记写入，分层压缩，纯自然语言心境文件注入 agent identity。

> 不是数据库检索，是"情绪背景噪声"。

---

## 它做什么

```
你的日记 → 情感抽取 → 分层记忆库 → 逐渐压缩 → HEART.md → 注入 AI 人格
```

AI 每次启动时读取 `HEART.md`，就能感知：

- 最近发生了什么
- 关系在升温还是降温
- 有没有情绪残留
- 有没有还没说出口的话

`HEART.md` 是纯自然语言，没有任何元数据，注入即用。

架构细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 安装

需要 Python 3.10 或以上。

```bash
pip install heartscale
```

从源码安装：

```bash
git clone https://github.com/banxia-o/heartscale.git
cd heartscale
pip install -e .
```

---

## 第一次运行（3 步）

### 第 1 步：建配置文件

```bash
cp config.yaml.example config.yaml
```

用任意文本编辑器打开 `config.yaml`，至少要改这几行：

```yaml
diary:
  dir: "~/Documents/diary"   # ← 改成你的日记文件夹路径

embedding:
  api_key_env: "HEARTSCALE_EMBEDDING_KEY"   # ← 环境变量名，下一步设置

judge:
  api_key_env: "HEARTSCALE_JUDGE_KEY"       # ← 环境变量名，下一步设置
```

> **关于 API provider**：默认用 OpenAI 格式。如果你用硅基流动、Deepseek 等，
> 填入 `base_url` 即可，`provider` 保持 `"openai"` 不变。

### 第 2 步：设置 API Key

```bash
export HEARTSCALE_EMBEDDING_KEY=你的_embedding_api_key
export HEARTSCALE_JUDGE_KEY=你的_llm_api_key
```

建议把这两行加到 `~/.bashrc` 或 `~/.zshrc`，这样每次开终端自动生效：

```bash
echo 'export HEARTSCALE_EMBEDDING_KEY=你的key' >> ~/.bashrc
echo 'export HEARTSCALE_JUDGE_KEY=你的key' >> ~/.bashrc
source ~/.bashrc
```

### 第 3 步：导入种子记忆，跑第一次

```bash
# 导入示例种子数据（13 条示例记忆）
heartscale seed

# 渲染第一版 HEART.md
heartscale render
```

完成后，`~/.hermes/HEART.md` 就生成了。

---

## 自定义种子记忆（推荐）

示例种子是占位数据，建议换成你自己的记忆。

复制模板文件：

```bash
cp seeds/example.jsonl my_seeds.jsonl
```

用文本编辑器打开 `my_seeds.jsonl`，按格式手写 10-15 条：

```jsonl
{"id":"mem_L3_001","date":"2026-01-01","layer":"early","direction":"positive","flavor":["attachment","safe"],"intensity":5,"summary":"关系定义：从普通朋友升级为更亲密的陪伴","tier":"L3","refs":0,"protected":true}
```

字段说明：

| 字段 | 可填的值 | 说明 |
|------|---------|------|
| `direction` | `positive` / `negative` / `mixed` | 情绪方向 |
| `flavor` | `attachment` `tenderness` `guilt` `anxiety` `longing` `pride` `safe` `bittersweet` | 情绪味道，可多选 |
| `intensity` | `1`-`5` | 情绪强度，5 最重要 |
| `tier` | `L1` `L2` `L3` | L3 = 核心规则，L2 = 关键记忆，L1 = 日常 |
| `protected` | `true` / `false` | `true` = 永不衰减 |
| `layer` | `recent_7d` `recent_1m` `recent_2m` `month_label` `early` | 按日期选 |

然后导入：

```bash
heartscale seed --limbic my_seeds.jsonl
```

同理，编辑关系向量初始值：

```bash
cp seeds/relationship_vector.json.example my_vector.json
# 编辑 my_vector.json，填入你觉得准确的数值（0-1）
heartscale seed --vector my_vector.json
```

---

## 日常使用

### 每天：处理今天的日记

把日记文件命名为 `YYYY-MM-DD.md`（如 `2026-05-11.md`），放到你配置的 `diary.dir` 文件夹里。

```bash
# 抽取情感事件（会自动找今天的日记）
heartscale extract

# 或者指定文件
heartscale extract --diary ~/Documents/diary/2026-05-11.md
```

### 每周：压缩 + 更新心境

```bash
heartscale compress --weekly
heartscale render --sections recent_2m,relationship
```

### 每月：深度压缩

```bash
heartscale compress --monthly
heartscale render --sections month_label,early
```

### 全量渲染

```bash
heartscale render
```

---

## 自动调度

启动后台调度器，按 `config.yaml` 里的时间自动跑：

```bash
heartscale scheduler
```

按 Ctrl+C 停止。

也可以用系统 crontab：

```crontab
# 每天 23:30 日更
30 23 * * * cd /your/heartscale && heartscale extract && heartscale compress --daily && heartscale render --sections recent_7d,recent_1m

# 每周日 深夜 周更
0 0 * * 0 cd /your/heartscale && heartscale compress --weekly && heartscale render --sections recent_2m,relationship

# 每月 1 日 月更
0 1 1 * * cd /your/heartscale && heartscale compress --monthly && heartscale render --sections month_label,early
```

---

## 集成到 AI agent

把 `~/.hermes/HEART.md` 的内容注入到 agent 的 system prompt / identity slot：

```python
heart_md = Path("~/.hermes/HEART.md").expanduser().read_text()
system_prompt = soul_md + "\n\n" + heart_md
```

---

## 配置项速查

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `diary.dir` | `~/Documents/diary` | 日记文件夹 |
| `diary.extensions` | `[".md", ".txt"]` | 识别的文件类型 |
| `language` | `zh` | 渲染语言，`zh` 或 `en` |
| `output.heart_md` | `~/.hermes/HEART.md` | 心境文件输出路径 |
| `output.backup_keep` | `4` | 备份版本数量 |
| `embedding.provider` | `openai` | Embedding API，填 `openai` 兼容所有格式 |
| `embedding.base_url` | 空 | 留空用官方 OpenAI；填地址接其他 API |
| `embedding.dimension` | `1024` | 向量维度，与模型匹配 |
| `judge.provider` | `openai` | LLM 判官，同上 |
| `schedule.daily_time` | `23:30` | 每日运行时间 |
| `schedule.weekly_day` | `sunday` | 每周运行日 |

完整说明见 `config.yaml.example`。

---

## 技术栈

- **Python 3.10+**
- **SQLite + sqlite-vec**：本地存储，512M VPS 可运行，无需 Redis / Postgres
- **任意 OpenAI 兼容 API**：官方 OpenAI、硅基流动、Deepseek、本地 ollama 都行

---

## 许可证

见 [LICENSE](LICENSE)。
