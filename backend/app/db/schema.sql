-- TourPlanOpt schema. Executed idempotently at startup (see app/db/database.py).
--
-- COORDINATE INVARIANT: every lng/lat stored here is GCJ-02, the native system of
-- both the Amap JS API and the Amap Web服务 API. Values read from Amap are stored
-- verbatim and NEVER converted. The only conversion boundary in the whole project is
-- navigator.geolocation (WGS-84) -> GCJ-02, in app/util/coords.py. If you ever render
-- on a non-Amap basemap you must apply the inverse transform at that point.
--
-- TIME INVARIANT: every *_min column is an INTEGER count of minutes since midnight,
-- never an "HH:MM" string. The schedule recurrence
--   next_start = prev_start + prev_duration + travel_min_before
-- is pure integer arithmetic; parsing strings at each step is a bug farm.
-- app/util/timefmt.py is display-only.
--
-- No migration tool. This is a fresh-database project; schema.sql is re-run on every
-- boot with IF NOT EXISTS. If a column ever needs to change, write a numbered
-- ALTER guarded by a schema_meta version bump.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS trips (
    id            TEXT PRIMARY KEY,           -- 8-char base32, URL-safe
    title         TEXT NOT NULL DEFAULT '',
    city          TEXT NOT NULL DEFAULT '',
    travel_mode   TEXT NOT NULL DEFAULT 'driving'
                  CHECK (travel_mode IN ('driving', 'walking', 'straight')),
    cost_model    TEXT NOT NULL DEFAULT 'haversine'
                  CHECK (cost_model IN ('haversine', 'amap')),
    day_start_min INTEGER NOT NULL DEFAULT 540,   -- 09:00
    seq           INTEGER NOT NULL DEFAULT 0,     -- monotonic broadcast counter; persisted
                                                  -- so it survives a restart and clients can
                                                  -- detect gaps across reconnects
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS days (
    id              TEXT PRIMARY KEY,
    trip_id         TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    day_index       INTEGER NOT NULL,             -- 0-based
    date            TEXT,                         -- 'YYYY-MM-DD' or NULL
    title           TEXT NOT NULL DEFAULT '',
    start_place_id  TEXT,                         -- optional hotel/anchor, not an FK: the
    end_place_id    TEXT,                         -- place may be deleted independently
    start_min       INTEGER,                      -- overrides trips.day_start_min when set
    travel_mode     TEXT CHECK (travel_mode IN ('driving', 'walking', 'straight')),
    rev             INTEGER NOT NULL DEFAULT 1,
    UNIQUE (trip_id, day_index)
);

CREATE TABLE IF NOT EXISTS places (
    id                TEXT PRIMARY KEY,
    day_id            TEXT NOT NULL REFERENCES days(id) ON DELETE CASCADE,
    trip_id           TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,  -- redundant on
                                                  -- purpose: a full snapshot is one WHERE
    sort_index        INTEGER NOT NULL,           -- dense 0..N-1, rewritten wholesale on reorder
    name              TEXT NOT NULL,
    amap_poi_id       TEXT NOT NULL DEFAULT '',
    address           TEXT NOT NULL DEFAULT '',
    lng               REAL NOT NULL,              -- GCJ-02
    lat               REAL NOT NULL,              -- GCJ-02
    duration_min      INTEGER NOT NULL DEFAULT 60,
    user_start_min    INTEGER,                    -- 用户自己填的时间：排程唯一当作"固定"的
                                                  -- 时间，NULL = 从没设过
    start_min         INTEGER,                    -- 全部由排程推导，只为了重启后不必重算
    arrive_min        INTEGER,                    -- （重算会花高德配额）而落库
    travel_min_before INTEGER,
    locked            INTEGER NOT NULL DEFAULT 0, -- 1 = 位置锚点；用户手填时间时一并置 1
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('confirmed', 'pending')),
    note              TEXT NOT NULL DEFAULT '',
    added_by          TEXT NOT NULL DEFAULT '',   -- client_id of whoever added it
    photo_url         TEXT NOT NULL DEFAULT '',   -- 首张实拍图（高德 CDN 直链，加地点时随
                                                  -- 搜索/详情拿，落库后零配额复用）
    rev               INTEGER NOT NULL DEFAULT 1, -- convergence stamp, NOT a rejecting CAS
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (day_id, sort_index)
);

CREATE INDEX IF NOT EXISTS idx_places_trip ON places(trip_id, sort_index);

-- Persistent roster: name and colour stay stable across reconnects.
-- Presence ("who is online right now") is deliberately NOT a table -- it lives in
-- TripHub's memory. Persisting presence means zombie rows after a crash.
CREATE TABLE IF NOT EXISTS participants (
    trip_id   TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    name      TEXT NOT NULL,
    color     TEXT NOT NULL DEFAULT '',
    joined_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (trip_id, client_id)
);

-- Keyed on round(coord * 1e5) integers (~1.1 m), NOT raw floats: with float keys a
-- marker nudged 30 cm misses the cache and burns quota. The rounded coordinates are
-- also what we send to Amap -- 1.1 m is far below routing noise.
-- ok=0 is a NEGATIVE cache entry: it stops us re-requesting a permanently unreachable
-- pair (walking beyond 5 km, no road network) on every optimize.
CREATE TABLE IF NOT EXISTS amap_distance_cache (
    o_lng_r    INTEGER NOT NULL,
    o_lat_r    INTEGER NOT NULL,
    d_lng_r    INTEGER NOT NULL,
    d_lat_r    INTEGER NOT NULL,
    mode       INTEGER NOT NULL,   -- 0 straight | 1 driving | 3 walking (Amap /v3/distance type)
    distance_m INTEGER,
    duration_s INTEGER,
    ok         INTEGER NOT NULL DEFAULT 1,
    infocode   TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (o_lng_r, o_lat_r, d_lng_r, d_lat_r, mode)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- -- M10: accounts, sessions, trip history, city recommendation cache ----------------

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,           -- 8-char base32
    name          TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,              -- pbkdf2_hmac: salt$hash (hex)
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,              -- 32-byte hex, cookie value
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,                 -- ISO timestamp
    created_at TEXT NOT NULL
);

-- 历史足迹：按"最近打开过"排序我的行程。created_by 是所有者，visit 是访客。
CREATE TABLE IF NOT EXISTS trip_visits (
    trip_id   TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (trip_id, user_id)
);

-- 推荐缓存：按 (city, category, sort) 存整页结果，24h TTL——同一城市一天最多烧一次配额。
CREATE TABLE IF NOT EXISTS city_poi_cache (
    city       TEXT NOT NULL,
    category   TEXT NOT NULL,
    sort       TEXT NOT NULL,                  -- 'composite' | 'hot'
    payload    TEXT NOT NULL,                 -- JSON array of PoiOut
    fetched_at TEXT NOT NULL,
    -- 'distance' 不在这一列里：它不查上游，拿 composite 那份自己算直线距离重排。
    PRIMARY KEY (city, category, sort)
);

-- 暂存区（想去清单）：先丢想法，再择日排进行程。与 places 分表——
-- 暂存项不参与优化/排程，混进 places 会污染排序与时间线。
CREATE TABLE IF NOT EXISTS stash (
    id          TEXT PRIMARY KEY,
    trip_id     TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    address     TEXT NOT NULL DEFAULT '',
    lng         REAL NOT NULL,
    lat         REAL NOT NULL,
    amap_poi_id TEXT NOT NULL DEFAULT '',
    added_by    TEXT NOT NULL DEFAULT '',
    photo_url   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stash_trip ON stash(trip_id, created_at);
