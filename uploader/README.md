# 日百行业情报台 · 在线上传 + AI 产出 + 审核上线（Vercel 后端）

这是给看板配套的「同事在线传 Excel → AI 自动产出分析 → 管理员审核 → 自动上线」后端。

```
同事打开上传页 → 传赛道 Excel + 填赛道名
   → 后端解析 Excel → 调 AI 产出 track JSON → 存入待审核区（GitHub pending/ 目录）
你打开审核页 → 预览 AI 产出 → 点【通过】
   → 后端把该赛道合并进 site/data/creative.js 并 push
   → GitHub Pages 几十秒后自动刷新看板
```

看板本体（GitHub Pages）不变，本后端只负责「收数据 + 跑 AI + 审核 + 写回」。

---

## 一、目录结构

```
uploader/
├── api/
│   ├── _lib.js        # 共享库：GitHub读写 / creative.js解析合并 / AI调用 / 鉴权
│   ├── _prompt.js     # AI 提示词（固化了 schema 规范）
│   ├── upload.js      # POST 收Excel→调AI→存待审核区
│   ├── pending.js     # GET  列待审核记录
│   ├── approve.js     # POST 通过→合并进creative.js并推送
│   └── reject.js      # POST 驳回→删待审记录
├── public/
│   ├── upload.html    # 同事用：上传页
│   └── review.html    # 你用：审核页
├── package.json
└── vercel.json
```

---

## 二、部署步骤（一次性，约 5 分钟）

### 1. 把本目录推到一个 GitHub 仓库
可以单独建个仓库，也可以就用现在这个 `riri-baihuo-board`（本 uploader 目录已在其中）。

### 2. 在 Vercel 导入项目
1. 打开 https://vercel.com → 用 GitHub 登录
2. Add New → Project → 选中仓库
3. **Root Directory 选 `uploader`**（重要：因为后端在这个子目录）
4. 先别急着 Deploy，去配环境变量 ↓

### 3. 配置环境变量（Vercel → Settings → Environment Variables）

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `GH_TOKEN` | GitHub Token（能读写看板仓库） | `ghp_xxx` |
| `GH_OWNER` | 看板仓库所有者 | `cccccccoingjiang77` |
| `GH_REPO` | 看板仓库名 | `riri-baihuo-board` |
| `GH_BRANCH` | 分支 | `main` |
| `CREATIVE_PATH` | creative.js 路径 | `site/data/creative.js` |
| `AI_BASE_URL` | AI 接口地址（OpenAI 兼容） | 见下 |
| `AI_API_KEY` | AI 密钥 | `sk-xxx` |
| `AI_MODEL` | 模型名 | 见下 |
| `UPLOAD_TOKEN` | 上传口令（发给同事） | 自定义，如 `ricao2026` |
| `ADMIN_TOKEN` | 审核口令（你自己留着） | 自定义，如 `admin888` |

**AI 供应商怎么填（三选一，都是 OpenAI 兼容格式）：**

- OpenAI：`AI_BASE_URL=https://api.openai.com/v1`，`AI_MODEL=gpt-4o-mini`
- DeepSeek：`AI_BASE_URL=https://api.deepseek.com/v1`，`AI_MODEL=deepseek-chat`
- 阿里通义：`AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，`AI_MODEL=qwen-plus`

> 换供应商只需改这 3 个变量，代码不用动。

### 4. Deploy
部署完成后会得到一个网址：`https://riri-baihuo-board.vercel.app`（当前生产地址，Vercel 项目名 riri-baihuo-board，push 到 main 后自动重新部署）

- **上传页（发给同事）**：`https://<你的域名>/` 或 `/upload.html`
- **审核页（你自己用）**：`https://<你的域名>/review.html`

---

## 三、日常使用

两套协同看板上传，不同榜单、不同赛道各自随时更新，互不干扰，无需你手动组装：

### 1. 🎬 卖点 & 创意看板 (AI分析)
- **用途**：收集分客户/分链路/分创意性能报表，自动解析、计算消耗加权指标并让 AI 提炼词云和爆款话术。
- **模板下载**：在上传页面点 `下载：原始数据报表样本.xlsx` 获取。
- **更新策略**：输入同名赛道则自动覆盖卖点分析数据，但**深度保留 Top 素材、视频及关键帧等网页端内容，互不干扰**。

### 2. 🛒 商品选品看板 (数值同步)
- **用途**：按选品台的两大模块更新数据：`分链路榜单` 与 `热点周期 / 节气选品`。
- **归属选择**：分链路榜单包含 `CID / 小程序（非闭环）`、`闭环 - 全域通`、`闭环 - ADQ`；热点周期包含梅雨季、夏至 / 三伏天等年度节点。
- **更新策略**：上传时明确选择具体链路或热点节点，仅覆盖所选归属，不再按 Sheet 名猜测全域通 / ADQ，也不会影响其他链路或节点。
- **模板下载**：在页面上点 `下载：商品选品表格模板.xlsx` 获取。该模板与创意卖点填报表相互独立。
- **标准字段**：`开户行业`、`商品类目`、`商品名称`、`商品图`、`参考单价`、`推荐版位`、`消耗(元)`、`ROI`、`CTR`、`CVR`、`素材链接`、`落地页链接（复制到微信点击查看）`、`创建日期`。节点榜单按 `消耗(元)` 降序展示 Top 25。
- **Sheet 命名支持**：单 Sheet `商品选品台` 可直接导入；原有榜单多 Sheet 也可读取，实际归属以上传页面选择为准。
- **表头核心字段自适应匹配**：
  - `商品名称` *(必填，以此为主进行聚合去重)*
  - `客单价` / `建议客单` / `参考单价` / `单价`
  - `消耗(元)` / `总消耗` / `消耗` *(节点榜单主排序字段)*
  - `ROI` *(消耗相同时作为次排序字段)*
  - `点击率%` / `转化率%`
  - `推荐版位`、`素材链接`
- **无图智能配图**：如果 Excel 中没有提供 `商品图` 列（或数据为空），系统在导入时会**全自动根据商品名称和分类调用 AI（Pollinations）联网配出高清白底电商参考图**，同事上传完全无需找图贴图！

---

## 四、安全说明

- `topMaterials`（Top素材拆解）、`videoUrl`、关键帧等网页侧字段：合并时**只覆盖 6 个分析模块，其余原样保留**，同事上传不会冲掉已有的 Top 素材。
- 上传口令 / 审核口令分离：同事只能传（进审核区），只有你能让数据真正上线。
- AI 的 Key、GitHub Token 都只存在 Vercel 后端环境变量里，**不出现在前端页面**。
