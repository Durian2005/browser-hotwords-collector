# 浏览器/搜索入口高热度词条采集（hotwords）

按源配置声明式组织的热榜采集骨架。**V3**：
5 个源（百度 4 榜 + B站 + 微博 + 抖音 + 头条）、**跨源共现**、
**持续上榜追踪**、**趋势看板（含搜索筛选）**，实测 **280 条/轮、status=ok**。

参考 Scrapling 的能力边界设计，但**只用它的 `Fetcher`**（TLS 指纹 + 统一 API），
不引入 StealthyFetcher / adaptive —— 本项目的源几乎全是 JSON 接口，
反爬对抗与 DOM 自适应都用不上（详见"选型说明"）。

![趋势看板](docs/dashboard.png)

---

## 快速开始

```bash
# 1. 安装依赖（建议先建虚拟环境）
pip install -r requirements.txt

# 2. 常用命令
python -m hotwords sources    # 列出已配置的源
python -m hotwords once       # 跑一轮采集（自动更新持续追踪）
python -m hotwords track      # 持续上榜榜（"真热度"核心）
python -m hotwords cross      # 跨源共现榜
python -m hotwords trend      # 生成趋势看板 dashboard.html
python -m hotwords report     # 数据概览
python -m hotwords run        # 常驻调度（APScheduler）
python -m hotwords alerts     # 查看告警

python tests/test_basic.py    # 11/11 通过
```

Windows 下双击 `run.bat` 会出现交互菜单（选 1~6），
也支持 `run.bat once / track / cross / trend / report / test`。
若 `python` 不在 PATH 上，可先 `set HOTWORDS_PY=<你的解释器绝对路径>`。

更详细的使用说明、典型场景与排查手册见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 三个"热度"视角（别混淆）

| 视角 | 问题 | 方法 | 抗噪性 |
|---|---|---|---|
| **单源排名** | 这个词在这个平台排第几 | 直接读 rank | ❌ 平台各自为政 |
| **跨源共现** | 多个平台是否同时上榜 | 源内分位 × `1+log2(源数)` | ⚠️ 单轮可能是巧合 |
| **持续上榜** ⭐ | 是否**连续多轮**都在榜 | `persistence_score` | ✅ **最强信号** |

**为什么持续度最强**：某词"刚好两源同时出现"可能是一次巧合，
但**连续 5 轮都在榜**几乎不可能是噪声。

`persistence_score`（0~150）权重设计：
连续轮数 35% + 历史最长连续 20%（抗短期中断）+ 上榜占比 20%
+ 最佳名次 15% + 跨源广度 10%。

`track_persistence()` 在每轮采集后**增量更新**（不全表重算）：
- 本轮上榜 → `total_rounds+1`，若上轮也在榜则 `streak+1`
- 本轮未上榜 → `streak` 归零（**断档检测**）

---

## 趋势看板（`dashboard.html`）

自包含 HTML（内联 CSS/JS，无 CDN，**离线可用**），暗色主题：

| 视图 | 内容 |
|---|---|
| **持续霸榜 Top** | 连续轮数条形，绿≥3轮 / 黄=2轮 / 蓝=1轮 |
| 跨源共现榜 | 绿=多源共现 / 蓝=单源 |
| 排名走势 | Top 词条各轮最佳名次折线 |
| 各源采集量 | 堆叠柱，健康度监控 |

顶部工具栏：**词条搜索框**（实时过滤）+ 「只看跨源共现」/「只看连续上榜」勾选。

---

## 架构

```
调度层   scheduler.py    APScheduler；每源独立 job、max_instances=1 禁止并发
   ↓
采集层   collector.py    Fetcher 优先级：scrapling > curl_cffi > httpx > requests
                         指数退避重试；403/401 不重试
   ↓
解析层   parsers/        每源一个 Parser。只做 "原始 -> HotItem"，不发请求
   ↓
断言层   assertions.py   结构哨兵：条目数 / 必填字段 / 空词条
   ↓
分析层   analysis.py     跨源共现 + 持续上榜追踪（增量更新）
   ↓
存储层   storage/        snapshot + word + word_track + alert + 指纹（6 张表）
   ↓
展示层   render.py       趋势看板 HTML
   ↓
告警层   notify.py       解析失效 / 字段漂移 -> 日志 / alert 表
```

**Fetcher 档位**（auto 优先级）：
- `scrapling` — 完整框架，TLS 指纹 + 浏览器能力（依赖重）
- `curl_cffi` — **轻量 TLS 指纹，仅几 MB，无浏览器**（大多数场景够用）
- `httpx` / `requests` — 无指纹伪装，纯兜底

**关键解耦**：Parser 不碰网络，Collector 不碰解析。
→ 将来把 `static_json` 换成浏览器渲染，解析逻辑一行不用改。

---

## 源配置（`hotwords/config/sources.yaml`）

新增一个源 = **改配置 + 注册 Parser**，主流程零改动。

| 源 | 状态 | 实测条目 | 备注 |
|---|---|---|---|
| `baidu_hot` | ✓ | 101（4 榜） | realtime 无热度值 |
| `bilibili_hot` | ✓ | 30 | 最稳最快，0.2s |
| `weibo_hot` | ✓ | 50 | 需 Referer + 手机 UA |
| `douyin_hot` | ✓ | 50 | — |
| `toutiao_hot` | ✓ | 50 | 字段首字母大写 |
| `zhihu_hot` | ✗ | — | 401 需登录态 |

---

## 实测踩坑记录（2026-10-09）

这一节是最有产出的部分 —— 都是真机跑出来的，不是推测。

### 坑 1 · 百度接口多套一层 `content`
真实路径 `data.cards[0].content[0].content[]`，
**直接取 `data.cards[0].content` 会拿到 0 条**。

### 坑 2 · 同接口不同榜单，字段名不同 ⭐
| 榜单 | 字段 |
|---|---|
| `realtime` | `word` |
| `teleplay`/`movie`/`novel` | `title` |

同一 URL、同一层级，字段名居然不一样。**这是 adaptive 完全救不了的**
（它只处理 DOM 结构漂移，不处理 JSON 字段改名）。
解法：Parser 字段兼容 + 断言 `required_fields_any`。

### 坑 3 · 百度热度值是 dict 不是数字
`hotScore = {"type":0,"value":"95471","desc":"热搜指数"}`，直接 `int()` 丢数据。

### 坑 4 · 百度 `realtime` 榜压根没有热度值
只有 `word`/`url`/`isTop`。**这不是 bug，是数据事实**。

### 坑 5 · `normalize_hot_value` 对 dict 静默产错值
`str({'value':'1'})` 里的 `1` 被正则匹配 → 返回 1。
加类型防护 + 改 `fullmatch`。**靠自写测试才发现**。

### 坑 6 · 漂移检测误报
百度各榜字段天然不同，按"整个源"记指纹必然全报漂移。
改成按 `source/category` 维度记录。

### 坑 7 · 百度接口偶发超时返回空
手动测成功、批量跑却空 —— 网络抖动，**重试机制的价值所在**。

### 坑 8 · 头条字段名首字母大写，断言也要对上
`Title`/`HotValue`/`Url`。断言查的是**原始 raw 字典**，配置必须写
`required_fields: [Title]`。另有 `fixed_top_data` 置顶、`Label` 英文枚举需映射。

### 坑 9 ⭐ · UNIQUE 约束导致**静默数据丢失**（最严重）
原约束 `UNIQUE(source, category, rank, fetched_at)` ——
**同一秒内跑两轮，第二轮数据被 `INSERT OR IGNORE` 静默吞掉**，
表现为"词条 streak 永远涨不上去"。
→ 改为 `UNIQUE(run_id, source, category, rank, word)`。
**`run_id` 才是"哪一次采集"的权威标识，不能用时间戳代替。**

### 坑 10 · `ORDER BY started_at` 排序退化
同一秒内多轮时时间戳相同，`prev_run` 会选错轮次 → streak 断档判断失效。
→ 统一改用 `rowid`（插入顺序），不依赖时间戳。

### 坑 11 · `list - set` TypeError
`sorted(...)` 返回 list，却直接做 set 差集运算。必须先差集再排序。

---

## 选型说明（为什么只用 Scrapling 的 Fetcher）

| Scrapling 能力 | 本项目用不用 | 原因 |
|---|---|---|
| `Fetcher`（TLS 指纹） | ✅ 用 | 微博要的 Referer+UA 它天生支持 |
| `FetcherSession` | ✅ 可扩展 | 会话保持 |
| `DynamicFetcher` | ⚠️ 备用 | 仅知乎等 JS 站可能需要 |
| `StealthyFetcher` | ❌ 不用 | 现有源**没有一个**上 Cloudflare |
| **adaptive 自适应** | ❌ 不用 | 源是 JSON 接口，失效表现为"字段改名/接口换掉"，adaptive 无效 |

**真正防失效的是**：结构断言 + 字段漂移告警 + 配置化 Parser。
坑 2 是最直接的实证 —— adaptive 对"同接口不同榜字段不同"毫无办法。

> 本机现状：已装 `curl_cffi`（轻量 TLS 指纹），未装 scrapling。
> 当前实际使用 `curl_cffi`，微博实测 200 / 50 条。

---

## 合规留档

| 源 | robots 探测（2026-10-09） |
|---|---|
| 百度 | `top.baidu.com/robots.txt` **404**；`www.baidu.com/robots.txt` 只约束 `/baidu`、`/s?`，**未覆盖本接口** |
| B站 / 微博 / 抖音 / 头条 | 公开接口 |

红线：不绕技术措施、不高频并发、不商用替代原站。
频率设为单源 5–10 分钟、串行、全局 1–3 QPS。**不引入代理池。**

---

## 下一步（V4）

1. 告警外发到 Agent Mail（`notify.make_mail_notifier` 已留口子）
2. 词条分类打标（先评估用途，别急着上模型）
3. 持续上榜的**趋势预警**：新词突入 Top / 老词掉榜提醒


