# TourPlanOpt

**多人实时协作的行程规划工具：同行的几个人打开同一个链接，即可同时编辑同一份行程；一天内的多个地点可以自动排成更省时的路线。**

[![在线体验](https://img.shields.io/badge/%E5%9C%A8%E7%BA%BF%E4%BD%93%E9%AA%8C-chengrm.online-blue?style=flat-square)](https://chengrm.online/tourplanopt)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square)](LICENSE)

它面向多人出行中的两个问题：同行者的意见难以对齐，一天内多个地点的先后顺序难以权衡。它最初是作者自己出游时使用的工具。

**在线使用：<https://chengrm.online/tourplanopt>**（支持手机浏览器「添加到主屏幕」后作为应用使用）

![行程页：封面、当天时间线与地图上的路线](assets/trip-desktop.png)

## 功能

### 实时协作

- 打开同一链接即可同时编辑同一份行程，改动实时同步给所有参与者。
- 同伴正在编辑哪张卡片、各自停在地图上的哪一站，界面上直接可见。
- 断网或切到后台期间的改动会先入队，恢复连接后自动补齐。
- 留言附着在具体的「某天某站」上，讨论与上下文保持一体。

### 路线优化

- 一键将当天的多个地点排成更省时的顺序。
- 支持先固定部分地点（如从酒店出发、在指定地点收尾），再排列其余地点。
- 每次增删改后，当天的到达与离开时间就地重算并同步给所有人；与固定时间的安排冲突时给出提示。
- 默认按直线距离估算；需要真实路况时可切换驾车或步行模式。

### 行前与收尾

- 出行清单、预算与 AA 结算、每日天气、想去清单集中在同一份行程中。
- 每笔支出记明分摊方式，按人数折算后给出转账建议与明细，明细可直接复制为文本。
- 首页汇总每趟行程的待办：距出发天数、清单完成度、预算使用情况。已结束但未结算的行程优先展示，可一次性收尾。

手机上同样可用，页签与抽屉按触屏重排：

![触屏档：封面、当天时间线与底部页签](assets/trip-phone.png)

## 技术栈

Vue 3 · Pinia · TypeScript · Vite · sortablejs ｜ Python FastAPI · WebSocket · Pydantic · SQLite（WAL）｜ 高德开放平台 ｜ Docker Compose + Caddy

## 许可

本项目基于 [Apache License 2.0](LICENSE) 开源。
