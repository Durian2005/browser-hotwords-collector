# 使用指南

命令行参考、典型场景与排查手册。安装见 [README](../README.md#快速开始)。

---

## 0. 一分钟上手

```bash
python -m hotwords once       # 采集一轮（约 5 秒）
python -m hotwords track      # 看持续上榜榜（"长期"）
python -m hotwords emergence  # 看什么词正在爆发（"此刻"）
python -m hotwords trend      # 生成看板并打开
```

Windows 用户可直接双击 `run.bat`，会出现交互菜单（选 1~7）。

---

## 1. 环境准备

```bash
# 建议使用虚拟环境，避免污染系统 Python
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

pip install -r requirements.txt
```

依赖说明：

| 包 | 作用 | 必需 |
|---|---|---|
| `curl_cffi` | 轻量 TLS 指纹（几 MB，无浏览器） | 推荐 |
| `scrapling` | 完整框架（TLS 指纹 + 浏览器能力） | 可选 |
| `httpx` | 无指纹伪装，纯兜底 | 兜底 |
| `PyYAML` | 读取源配置 | **必需** |
| `APScheduler` | 常驻调度 | 常驻模式必需 |

采集层会自动按 `scrapling > curl_cffi > httpx > requests` 的优先级选择可用的 Fetcher，
装哪个都能跑。

> 若 `python` 不在 PATH 上，或想指定某个解释器：
> Windows 下 `set HOTWORDS_PY=C:\path\to\python.exe`（`run.bat` 会读取它）。

---

## 2. 常用命令

| 命令 | 作用 |
|---|---|
| `python -m hotwords sources` | 列出所有源的状态 |
| `python -m hotwords once` | **跑一轮采集** |
| `python -m hotwords track` | 持续上榜榜（长期，靠跨轮累加） |
| `python -m hotwords emergence` | 突现 / 涨跌榜（此刻，只看最新一轮相对上次） |
| `python -m hotwords cross` | 跨源共现榜 |
| `python -m hotwords trend` | 生成看板 `dashboard.html` |
| `python -m hotwords report` | 数据概览 |
| `python -m hotwords alerts` | 查看告警 |
| `python -m hotwords run` | 常驻调度 |
| `python -m hotwords retrack` | 从快照重建持续上榜数据（改排除清单后必跑） |
| `python tests/test_basic.py` | 跑测试（27 项） |

全局参数：`--db <路径>`（默认 `hotwords.db`）、`--config <路径>`、`-v`（详细日志）。

### run.bat 快捷方式（Windows）

```bat
run.bat            :: 跑一轮
run.bat 2          :: 持续上榜榜
run.bat 3          :: 突现 / 涨跌榜
run.bat 4          :: 跨源共现榜
run.bat 5          :: 生成并打开看板
run.bat 6          :: 概览 + 告警
run.bat 7          :: 常驻调度
run.bat test       :: 跑测试
```

也支持关键字写法：`run.bat once / track / emergence / cross / trend / report / retrack / run / test`。

---

## 3. 典型场景

### 3.1 采集一轮

```bash
python -m hotwords once
```

预期输出：

```
run_id=3b6c8656... status=ok
  baidu_hot: 101 条
  bilibili_hot: 30 条
  weibo_hot: 51 条
  douyin_hot: 50 条
  toutiao_hot: 50 条
```

`status=ok` 即成功。跑完会**自动更新**持续上榜追踪，无需额外操作。

### 3.2 看什么词最热

```bash
python -m hotwords track --limit 20 --min-streak 2
```

```
共 4 轮数据。持续上榜榜（按连续轮数排序）:

词条                连续  累计  最长  最佳  源数  持续度    来源
宋佳飞天奖视后        4     4     4     1     2    89.2    toutiao+weibo
```

| 列 | 含义 |
|---|---|
| **连续** | 当前连续几轮在榜 ← 最强信号 |
| 累计 | 一共上榜几轮（中间断过也算） |
| 最长 | 历史最长连续（抗短期中断） |
| 最佳 | 历史最好名次（越小越好） |
| 源数 | 几个平台出现过，≥2 即跨源 |
| **持续度** | 0~150 综合分 |

### 3.3 看什么词正在爆发

```bash
python -m hotwords emergence --limit 20
```

只看**最新一轮**相对各词**上次出现**的变化，专门找刚冒头的：

```
词条                        名次    上轮    断档    跃升    源   分       构成
------------------------------------------------------------------------------------------------
张雪机车葡萄牙站第二回合              3     —     新     —     1   62.0    新词+35 + 第3名+27
            ……（中间 17 行同为全新词，略）……
2026美好奇妙夜节目单              10    22     0    +12    2   41.6    第10名+20 + 升12位+9.6 + 2源+12
王曼昱横扫佐藤瞳夺冠                2     2     0      0    2   40.0    第2名+28 + 2源+12
```

| 列 | 含义 |
|---|---|
| **名次** | 本轮最好名次（置顶 rank=0 不参与比较） |
| 上轮 | 上次出现时的名次；`—` = 全新词 |
| 断档 | 中间缺席几轮；`新` = 从未出现过，`0` = 连续在榜 |
| 跃升 | 名次变化（`+` 为上升） |
| **分** | 突现分（新词 35 / 回归 20 / 位置 `30−名次` / 跃升 `0.8×位数` / 跨源 `12×(源数−1)`） |
| 构成 | 得分的逐项拆解，直接说明"为什么它排在前面" |

**和 `track` 的分工**：`track` 找"长期稳定的真热度"，`emergence` 找"此刻的爆发"。
持续度对新词天然不利，所以**不要**用 `track` 的下半部分去找新出现的词。

`--min-score X` 可过滤掉低分噪声（例如 `--min-score 30` 只看强信号）。

### 3.4 看哪些词全网都在聊

```bash
python -m hotwords cross
```

单个平台的榜单可能被运营干预，**多平台同时顶上去**说明是真实公共事件。
热度指数单源满分 100，多源按 `1 + log2(源数)` 加成，所以 **>100 即跨平台**。

### 3.5 看趋势图

```bash
python -m hotwords trend
```

生成 `dashboard.html`（自包含、离线可用），含 7 个视图：
持续霸榜 Top、新词突现 / 涨跌、跨源共现榜、位置与性质标记、
排名走势折线、突现词轨迹、各源采集量堆叠柱。
顶部有搜索框与筛选勾选。

### 3.6 挂机采集

```bash
python -m hotwords run
```

按源配置的间隔调度（默认：百度/B站 5 分钟，微博/抖音/头条 10 分钟）。
每个源独立 job，同一源禁止并发。`Ctrl+C` 退出。

> **想得到有意义的"持续上榜"数据，需要跨天运行。**
> 短时间密集采样会让大量词条呈现"全勤"，此时分数缺乏区分度。

---

## 4. 排查

### 某个源 0 条

```bash
python -m hotwords alerts
```

- `http_error` — 网络/接口问题，偶发超时，重试 3 次仍失败才会记
- `parse_fail` — 结构断言未过，多半是**字段改名**
- `schema_drift` — 字段集合变化，**不一定影响解析**

### 看到 `schema_drift: weibo_hot`

**正常现象，不是 bug。** 微博部分条目（带广告/话题标记）字段集合不同，
只要断言通过、条数正常即可忽略。

### 源突然 0 条 / 条数腰斩（最常见）

大概率字段改名。三步定位：

1. **看告警**：`python -m hotwords alerts --limit 5`
2. **手动打接口对比字段**：
   ```bash
   curl -H "Referer: https://weibo.com/" https://weibo.com/ajax/side/hotSearch \
     | python -c "import sys,json;print(list(json.load(sys.stdin)['data']['realtime'][0].keys()))"
   ```
3. **改 Parser**：编辑 `hotwords/parsers/__init__.py` 里对应的 `parse_xxx`。

> 关键设计：**Parser 只做"原始 JSON → HotItem"，不碰网络**。
> 改字段不需要动采集逻辑，改完直接 `once` 验证。

---

## 5. 添加一个新源

两步，**主流程零改动**。

**第一步：改配置** `hotwords/config/sources.yaml`

```yaml
  - id: newsite_hot          # 唯一标识，入库用，定了别改
    name: XX热榜
    enabled: true
    type: static_json
    interval_min: 10
    parser: newsite          # 对应下面的 parser 名
    request:
      url: "https://example.com/api/hot"
      headers:
        Referer: "https://example.com/"
    assertions:
      min_items: 10
      required_fields: [word]
```

**第二步：写 Parser**，在 `hotwords/parsers/__init__.py` 里加：

```python
def parse_newsite(payload: dict, source: str, category: str) -> ParseResult:
    items = []
    for i, e in enumerate(payload.get("data", []), start=1):
        items.append(HotItem(source=source, category=category,
                             rank=i, word=e["title"]))
    return ParseResult(source=source, category=category,
                       items=items, raw_items=payload.get("data", []))
```

然后在文件底部注册表里登记 `"newsite": parse_newsite`。

完成后 `python -m hotwords sources` 应能看到新源。

---

## 6. 四个"热度"视角

| 视角 | 回答什么 | 抗噪性 |
|---|---|---|
| 单源排名 | 某平台排第几 | ❌ 各平台各自为政 |
| 跨源共现 | 多平台是否同时上榜 | ⚠️ 单轮可能是巧合 |
| **持续上榜** | 是否连续多轮在榜 | ✅ 最强信号 |
| **突现 / 涨跌** | 此刻是否正在爆发 | ✅ 抢新（看长尾用持续度） |

一个词偶尔上榜可能是巧合，**连续多轮在榜**几乎不可能是噪声。
反过来，一个词**刚出现就冲到前几名**，也几乎不可能是巧合 ——
这是两个方向的问题，用两套判据。

### `persistence_score`（0~150）

```
连续轮数 35% + 历史最长连续 20% + 上榜占比 20% + 最佳名次 15% + 跨源广度 10%
```

每轮采集后**增量更新**：本轮上榜则 `total_rounds+1`；若上轮也在榜则 `streak+1`；
本轮未上榜则 `streak` 归零（断档检测）。

### `emergence_score`（0~约 135）

```
新词 +35 | 断档后回归 +20 | 位置 30−名次（名次<30 才计）
名次跃升 0.8×位数（上限 30 位） | 跨源 12×(源数−1)
```

只看**最后一轮**，不做跨轮累加。排除清单与持续榜共用，
`failed` 轮不参与判定（否则会把采集失败误算成"断档好几天"）。

---

## 7. 数据库

所有数据在 `hotwords.db`（SQLite，WAL 模式）。6 张表：

| 表 | 作用 |
|---|---|
| `run` | 每轮采集记录 |
| `snapshot` | 全量快照 |
| `word` | 词条聚合（去重） |
| `word_track` | 持续上榜追踪 |
| `alert` | 告警 |
| `parser_fingerprint` | 字段指纹（漂移检测） |

备份即复制该文件；删除后程序会自动重建。

---

## 8. 合规

- 频率：单源 5~10 分钟，串行，全局 1~3 QPS
- 不绕技术措施、不破解验证码
- 不商用替代原站
- 不引入代理池

robots 探测结论留档于 `sources.yaml` 各源的 `robots_note`。
