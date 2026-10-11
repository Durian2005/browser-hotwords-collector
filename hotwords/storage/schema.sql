-- ============================================================
-- 高热度词条采集 —— SQLite Schema (V1)
-- ------------------------------------------------------------
-- 设计要点：
--  1) snapshot 全量追加，绝不 UPDATE 覆盖 —— 趋势分析的基础
--  2) word 表做"词条唯一性"聚合，不删 snapshot
--  3) hot_value 同时存原始字符串与归一化数值，归一化规则版本化
--  4) raw 字段保留原始条目 JSON，用于字段漂移排查 / 数据救援
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- 抓取批次：每次调度执行一条，便于定位"某次抓取整体失败"
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    run_id      TEXT PRIMARY KEY,          -- uuid4
    started_at  TEXT NOT NULL,             -- ISO8601
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',  -- running|ok|partial|failed
    stats_json  TEXT                       -- {"baidu_hot": 31, "bilibili_hot": 30}
);

-- ------------------------------------------------------------
-- 榜单快照：每条词条每个榜单一期一行
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS snapshot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    source        TEXT NOT NULL,           -- baidu_hot | bilibili_hot | ...
    category      TEXT NOT NULL DEFAULT 'main',  -- realtime|teleplay|movie|novel|main
    rank          INTEGER NOT NULL,        -- 榜内排名（1 起）
    word          TEXT NOT NULL,           -- 词条原文
    hot_value     TEXT,                    -- 热度原始值，如 "1.2亿" / "100909"
    hot_value_num INTEGER,                 -- 归一化数值，可为 NULL
    norm_version  TEXT,                    -- 归一化规则版本，如 "v1"
    label         TEXT,                    -- 平台展示标签：热/沸/新/爆/辟谣…
    url           TEXT,                    -- 跳转链接
    -- 位置与性质标记（独立成列，便于直接 SQL 过滤/统计，不必解析 raw）
    is_ad         INTEGER NOT NULL DEFAULT 0,  -- 广告/推广位（微博 is_ad / topic_ad）
    is_top        INTEGER NOT NULL DEFAULT 0,  -- 置顶（百度 isTop、头条 fixed_top_data）
    topic_category TEXT,                   -- 内容分类（头条 InterestCategory / 微博 flag_desc）
    raw           TEXT,                    -- 原始条目 JSON（rescue 用）
    fetched_at    TEXT NOT NULL,           -- ISO8601
    -- ⚠️ 唯一约束必须含 run_id：仅用 fetched_at 做去重时，
    --    同一秒内的两轮采集会被 INSERT OR IGNORE 静默吞掉（实测踩到）。
    --    run_id 才是"哪一次采集"的权威标识。
    UNIQUE(run_id, source, category, rank, word)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_source_time
    ON snapshot(source, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_word
    ON snapshot(word, fetched_at DESC);

-- ------------------------------------------------------------
-- 词条聚合：一个词条一行，跨源/跨期累加（去重在此层）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word (
    word_hash         TEXT PRIMARY KEY,    -- sha1(normalize(word))
    canonical_word    TEXT NOT NULL,       -- 首次出现时的原文
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    appear_count      INTEGER NOT NULL DEFAULT 1,
    sources           TEXT,                -- 逗号分隔来源
    cross_source_count INTEGER NOT NULL DEFAULT 1  -- 跨源共现数（高热度信号）
);

CREATE INDEX IF NOT EXISTS idx_word_last_seen ON word(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_word_cross ON word(cross_source_count DESC);

-- ------------------------------------------------------------
-- 告警：解析失效 / 结构漂移 / 沉默检测
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    source      TEXT,
    kind        TEXT NOT NULL,             -- parse_fail|schema_drift|silent|http_error
    detail      TEXT,
    created_at  TEXT NOT NULL,
    notified    INTEGER NOT NULL DEFAULT 0 -- 是否已外发（Agent Mail）
);

CREATE INDEX IF NOT EXISTS idx_alert_created ON alert(created_at DESC);

-- ------------------------------------------------------------
-- 解析器指纹：记录每个源上一次成功解析的字段集合，用于漂移检测
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parser_fingerprint (
    source       TEXT PRIMARY KEY,
    field_names  TEXT NOT NULL,            -- 归一化字段名，逗号分隔（排序后）
    item_count   INTEGER,
    updated_at   TEXT NOT NULL
);

-- ------------------------------------------------------------
-- 持续上榜追踪：区分"持续霸榜"与"偶发冒泡"
-- ------------------------------------------------------------
-- 单轮共现有偶发噪声（某词刚好两源同时出现）。跨轮统计才能识别真热点：
--   连续 N 轮都在榜 = 真爆点；只出现 1 轮 = 一时冒泡
-- 由 analysis.track_persistence() 在每轮采集后增量更新。
CREATE TABLE IF NOT EXISTS word_track (
    word_hash         TEXT PRIMARY KEY,
    canonical_word    TEXT NOT NULL,
    first_seen_run    TEXT NOT NULL,       -- 首次出现的 run_id
    last_seen_run     TEXT NOT NULL,       -- 最近出现的 run_id
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    total_rounds      INTEGER NOT NULL DEFAULT 1,   -- 累计上榜轮数
    current_streak    INTEGER NOT NULL DEFAULT 1,   -- 当前连续上榜轮数
    max_streak        INTEGER NOT NULL DEFAULT 1,   -- 历史最长连续轮数
    best_rank         INTEGER NOT NULL DEFAULT 999, -- 历史最好名次
    last_rank         INTEGER,                      -- 最近一轮名次（判升/降）
    source_count      INTEGER NOT NULL DEFAULT 1,   -- 出现过的源数
    sources           TEXT,                          -- 逗号分隔
    persistence_score REAL NOT NULL DEFAULT 0,      -- 持续度综合分
    -- 标记位：只要该词条在任意一轮被标为广告位/置顶，即置 1（累积 OR，不回退）
    is_ad             INTEGER NOT NULL DEFAULT 0,
    is_top            INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_track_streak ON word_track(current_streak DESC, max_streak DESC);
CREATE INDEX IF NOT EXISTS idx_track_score ON word_track(persistence_score DESC);
CREATE INDEX IF NOT EXISTS idx_track_last ON word_track(last_seen_run);
