# TourPlanOpt

几个人一起把行程排好——**同时编辑，自动算路**。

市面上的旅行规划工具要么功能堆砌上手复杂，要么把协作当附加功能。TourPlanOpt 反着来：MVP 只做两件事，并把它们做顺——

1. **实时协同编辑**：几个人打开同一个链接，彼此添加的地点、时间安排即时可见（谁在编辑哪张卡片也看得到）。
2. **距离计算与最优路径**：一键把一天的游览顺序排到最优，并生成整天的 timeline。

刻意不做（v1）：投票、评论、记账、AA 分摊、用户体系、公网部署。**小就是差异化。**

## 技术栈

| 层 | 选型 |
|---|---|
| 地图/路线 | 高德开放平台（前端 JS API + 后端 Web服务，两种不同的 Key） |
| 前端 | Vue 3 · Pinia · TypeScript · Vite · sortablejs |
| 后端 | Python FastAPI · 原生 WebSocket · Pydantic |
| 存储 | SQLite（WAL，纯 sqlite3 + Pydantic，无 ORM） |

## 快速开始

前置：Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node 18+。

```bash
# 1. 配置高德 Key（两种都要，见下节）
cp backend/.env.example backend/.env
#    然后编辑 backend/.env 填入 AMAP_WEB_KEY / AMAP_JS_KEY / AMAP_JS_SCODE

# 2. 装依赖（首次）
cd backend && uv sync
cd ../frontend && npm install

# 3. 起服务（两个终端，或 bash dev.sh 一键）
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

打开 http://localhost:5173 → 创建行程 → 把分享链接发给朋友（同一局域网内可直接打开）。

**只想跑一个端口**：`cd frontend && npm run build`，然后重启后端——FastAPI 会自动托管 `frontend/dist`，此后 http://localhost:8000 就是完整应用（单源、零 CORS）。

### 高德 Key 怎么填

到 https://console.amap.com → 应用管理 → 创建应用 → **分别**添加两种 Key：

| 变量 | Key 类型 | 用在哪 |
|---|---|---|
| `AMAP_JS_KEY` + `AMAP_JS_SCODE` | **Web端(JS API)** | 前端地图渲染（2.0 必须配安全密钥） |
| `AMAP_WEB_KEY` | **Web服务** | 后端 POI 搜索代理、距离矩阵 |

两个 Key **不能互换**。填反了的典型症状：后端报 `10009`、前端报 `INVALID_USER_SCODE`——应用的启动自检和页面顶部横幅会直接点名该改哪个变量。**域名白名单留空**（绑了 localhost 的话，手机/局域网访问会看到灰图）。

没有 Key 也能跑：默认优化走直线距离（haversine），零 API 调用。

## 功能一览

- 一键优化一天的游览顺序（3 个地点起），默认直线距离打分、可选真实路况
- 锁定某个地点或手改时间后，优化器绕着它排其它地点；「设为起点」把某张卡钉成出发地，「设为终点」决定当天收在哪
- 零配额自动排程：任何增删改之后服务端就地重算当天的到达/离开时间并广播，不点优化也有完整时间线，与固定时间冲突时告警
- 行程按天分成可折叠的区块，出去玩好几天也能一屏纵览；块内直接优化顺序、按天导航、撤销
- 首页是最近打开的行程仪表盘（记录只存在本机，无需账号）；新建抽屉只要求填标题，城市/日期/天数/出发时间/交通方式都能建完再补，一次就能建出连续 N 天
- 想去清单：搜索/推荐里先存着，想好了再排进某一天；地图右键/长按选点也能加
- 「发现」面板可按综合/热度/距离排序（默认综合）：距离不查上游，用这一天已加入地点的中心自己算直线距离，零配额；列表高度可拖，高度与排序按行程记住
- 地点自带照片（高德图床直链），列表缩略图与地图圆标共用同一张，加载失败自动回落成创建者色序号圆
- 优化结果一键撤销；卡片菜单支持跨天移动；空的天一键删除；底部一键复制文字版行程（贴群聊）；所有人的地图与列表实时收敛
- 距离矩阵带 SQLite 缓存（坐标取整到 ~1.1m）+ 不可达负缓存，配额消耗在 `/api/trips/{t}/days/{d}/matrix` 可观测

## 文档

开发文档在本地 `docs/` 目录（不入仓库，作者自用）：

- `docs/DEVELOPMENT.md` — 环境、常用命令、故障排查、代码约定
- `docs/ARCHITECTURE.md` — 协同模型、分层成本、数据不变量、依赖决策
- `docs/PROTOCOL.md` — WebSocket 协议规范

运行时的 REST 接口文档：启动后访问 `/docs`（FastAPI 自动生成）。

## 协同模型（给感兴趣的人）

服务端权威 + last-write-wins：客户端发字段级 patch → 服务端按到达顺序应用并 `rev+1` → **广播结果状态**（不是意图）给包括发起者在内的所有人。重排必须携带完整有序 id 数组并校验为排列，否则以 `op_reject` 附权威数组拒绝——并发拖拽因此不可能永久分叉。重连先 flush 离线队列（服务端按 `op_id` LRU 去重）再 resync 快照。

## 项目结构

```
backend/
  app/
    amap/      高德客户端（200 信封校验、限流重试）、距离缓存、自检
    api/       REST：行程、POI 代理、矩阵、优化
    ws/        协同 hub：TripHub / ClientConnection / handlers / ops
    routing/   矩阵构建、Held-Karp TSP（n≤14 精确）、排程
    db/        schema.sql + repositories（全部 SQL 在这一层）
  tests/       含 200 随机矩阵暴力 oracle、WS 全流程、优化闭环
frontend/
  src/
    stores/    trip（乐观应用+回声过滤）、socket（重连/心跳）
    components/ 地图、搜索、卡片、优化栏、presence 头像等
```

## 测试

```bash
cd backend && uv run pytest -q          # 74 个测试
cd frontend && npm run type-check       # vue-tsc
```

## 许可

见 LICENSE。
