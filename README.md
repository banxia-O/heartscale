# heartscale

**Hierarchical Emotional Accumulation & Rendering Topology**

让 AI agent 拥有类人的情感记忆——日记写入，分层压缩，自然语言心境文件注入 agent identity。

---

## 它能做什么

```
你的日记 → 情感抽取 → 分层记忆库 → 逐渐压缩 → HEART.md → 注入 AI 人格
```

AI 每次启动时读取 `HEART.md`，就能感知：最近发生了什么、关系在升温还是降温、有没有情绪残留。不是检索历史对话，而是像人一样"记住了某种感觉"。

架构细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 安装

```bash
pip install heartscale
```

或者从源码安装：

```bash
git clone https://github.com/banxia-o/heartscale.git
cd heartscale
pip install -e .
```

---

## 第一次运行（3 步）

### 第 1 步：复制配置文件

```bash
cp config.yaml.example config.yaml
```

用任意文本编辑器打开 `config.yaml`，填写：
- `diary.dir`：你的日记文件夹路径
- `embedding.api_key_env` 对应的环境变量（你的 Embedding API Key）
- `judge.api_key_env` 对应的环境变量（你的 LLM API Key）

### 第 2 步：设置 API Key 环境变量

```bash
export HEARTSCALE_EMBEDDING_KEY=你的embedding_api_key
export HEARTSCALE_JUDGE_KEY=你的llm_api_key
```

> 建议把这两行加到 `~/.bashrc` 或 `~/.zshrc` 里，这样每次开终端都自动生效。

### 第 3 步：写入种子记忆，启动

```bash
# 复制种子数据模板
cp seeds/example.jsonl ~/.hermes/limbic.jsonl
cp seeds/relationship_vector.json.example ~/.hermes/relationship_vector.json

# 编辑种子数据（按你的真实情况修改，或直接用示例跑通流程）
# nano ~/.hermes/limbic.jsonl

# 渲染第一版 HEART.md
heartscale render --config config.yaml
```

第一次运行完成后，`~/.hermes/HEART.md` 就生成了。

---

## 日常使用

```bash
# 每天：从今天的日记里抽取情感事件
heartscale extract --config config.yaml

# 每周：压缩记忆 + 更新 HEART.md
heartscale compress --config config.yaml
heartscale render --config config.yaml

# 自动调度（按 config.yaml 里的时间自动跑）
heartscale scheduler --config config.yaml
```

---

## 配置文件说明

| 字段 | 说明 |
|------|------|
| `diary.dir` | 日记文件夹，支持 `.md` 和 `.txt` |
| `language` | 输出语言，`zh` 中文 / `en` 英文 |
| `embedding.provider` | Embedding API，填 `openai` 可对接所有 OpenAI 兼容格式 |
| `embedding.base_url` | 留空用官方 OpenAI；填地址接硅基流动等兼容 API |
| `judge.provider` | LLM 判官，同上 |
| `output.heart_md` | HEART.md 输出路径 |

完整说明见 `config.yaml.example`。

---

## 技术栈

- Python 3.10+
- SQLite + sqlite-vec（本地存储，512M VPS 可运行）
- 任意 OpenAI 兼容格式的 Embedding 和 LLM API

---

## 许可证

见 [LICENSE](LICENSE)。
