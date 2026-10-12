# Skein

> 一绞线。把散落的灵感，编成一期讲得清的节目。

[![License: MIT](https://img.shields.io/badge/License-MIT-6c7cff.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-2f3446.svg)](#快速开始)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)](#快速开始)
[![Release](https://img.shields.io/github/v/release/llwand1/skein?color=c9a227)](https://github.com/llwand1/skein/releases)

**Skein**（读作 /skeɪn/，意为"一绞线"）是一款桌面端**内容创作副驾**。你在桌面上随手丢进零散的想法、截图、录屏、长文，它帮你把这些线头**编成一期节目的完整骨架**——每段要回答什么、口播稿怎么写、画面要什么、缺什么料——最后交出一份能直接给剪映 / 即梦 / 可灵去出片的**编排包**。

它**不做视频渲染**，只做"编排"这一件最费脑子的事。剩下的交给专业的剪辑和生成工具。

---

## 为什么是它

写脚本最难的从来不是"敲字"，是**想清楚**：这一期到底要讲什么、先讲哪段、这段凭什么让人信、手上这点料够不够。

Skein 把这件事变成一颗**常驻桌面角落的球**：

- 🟡 **一颗球，随时待命** —— 不打断你手头的事（不抢焦点），想起来就点开，想不起来它就静静待着
- 🫧 **灵感池** —— 想法先丢进去，不用现在就决定它属于哪一段；池子是"液态玻璃"，能看出攒到什么程度
- 🧠 **AI 帮你长出结构** —— 立项只给 3 段"初始理解"，之后结构随素材自己长；该反对你就反对你
- ✍️ **一段一段啃** —— 每段都能"详细论证 / 就这点发散"，撑不住的地方它直说缺，不编
- 🎬 **交出的是施工单** —— 分镜表、生成画面的提示词、镜头时间轴、字幕、口播稿、剪映草稿，一包带走
- 🔌 **没模型也能走到头** —— 有 key 走 AI，没 key 走本地规则，功能不缺失

**设计红线**（写死在代码里）：不存在通往"发送"的路径，只有"打开页面"；不接入任何平台私有协议；完成度永远只到 **92%**，最后一格由你点——这是设计，不是 bug。

---

## 快速开始

### 环境

- Windows 10 / 11
- Python **3.11+**

### 安装

```bat
git clone https://github.com/llwand1/skein.git
cd skein
pip install -r app/requirements.txt
```

### 运行

| 方式 | 命令 | 用途 |
|---|---|---|
| 日常 | 双击 `run-silent.vbs` | 无控制台，挂在托盘 + 角落的球 |
| 排错 | 双击 `run.bat` | 带控制台，能看到报错 |
| 开发 | 双击 `dev.bat` | **改代码自动重启**，边改边看 |

> 下载即用的打包版（无需装 Python）见 [Releases](https://github.com/llwand1/skein/releases)。

---

## 怎么用（5 步）

1. **丢想法** —— 点开球 → 「喂」那一格写下你看到的 → 目标留「灵感池」→ 喂进去
2. **立项** —— 想清楚了给这一期起个名（如「MCP 到底创新在哪」）→ AI 给出初始理解（3 段骨架）
3. **喂素材** —— 下拉选某一节拍 → 继续喂，或直接把文件拖到面板上（图片 / 视频 / 录音 / 长文都行）
4. **聊思路** —— 在「聊」那格说人话（"我想从成本切"、"第 2 段太学术了"），它接话；要动结构会先列出来，你点「照做」才动
5. **交出去** —— 点「导出编排包」，拿到分镜表 + 提示词 + 时间轴 + 剪映草稿

完整说明见 [app/README.md](app/README.md) 与 [怎么用Skein做一期.md](怎么用Skein做一期.md)。

---

## 配置

一切在**标题栏那枚齿轮**里，四组都不预设——留空就是留空，不兜"抖音 / 图文 / 7 张"这种写死的默认。

| 组 | 说明 |
|---|---|
| 模型 | OpenAI 兼容网关的 `base_url` / `model` / `key`，带「测试连接」 |
| 内容默认 | 平台 / 形态 / 目标张数（**只影响之后新立的项**） |
| 系统提示词 | 自定义人设，改完即时生效 |
| 搜索 MCP | AI 联网搜索（见下） |
| 跨期记忆 | 只读的 `user.md`，写下你是谁、你的口味，每一期都会参考 |
| 更新 | 检查更新 / 一键更新并重启 |

配置文件都在 `%LOCALAPPDATA%\Skein\`，**不进代码仓**。

---

## 联网搜索

AI 聊天时可以**真的上网查**——通过 [tinyfish](https://agent.tinyfish.ai) 的 MCP 服务。

- 走 `mcp-remote` 讲 stdio JSON-RPC，**只放行只读工具**（`search` / `fetch_content`），其余 29 个有副作用的工具一律不暴露
- 默认开启；没装 node / mcp-remote 时**静默降级**，绝不卡主流程
- OAuth 由 `mcp-remote` 自行管理，token 存在 `~/.mcp-auth`，Skein **不碰凭据**

没有联网需求时可以关掉，AI 照样按本地素材聊。

---

## 自动更新

- 应用内：设置 → 「检查更新」→ 有新版可**一键下载并自动重启**
- 发布端：往仓库推一个 `v*` tag，[GitHub Actions](.github/workflows/release.yml) 会自动打包 `Skein.exe` 并建 Release

---

## 数据与隐私

**全部在本机，不联网上传。**

```
%LOCALAPPDATA%\Skein\
  skein.db       全部数据（备份＝拷这一个文件）
  assets\        收进来的素材（原件不动，这里存副本）
  settings.json  球的坐标 / 当前期 / 各类设置
  llm.json       模型配置（含 key，绝不进代码仓）
  user.md        跨期用户记忆（只读，你自己写）
  packs\         导出的编排包
```

模型请求只发往**你自己填的**网关；搜索只发往你启用的 MCP 服务。

---

## 技术栈

- **PyQt6** —— 逐像素 Alpha、真抗锯齿、柔和投影；悬浮球与灵感池都是自绘
- **SQLite** —— 本地单文件库，零依赖
- **OpenAI 兼容协议** —— 换网关只改 `base_url`
- **MCP** —— 通过 `mcp-remote` 接入外部工具（stdio JSON-RPC）

> 早期 Tk 版因 Canvas 无抗锯齿、透明靠颜色键而废弃，全面转向 PyQt6。

---

## 项目结构

```
app/
  main.py        入口：悬浮球、热键、状态聚合、后台任务编排
  orb.py         悬浮球（置顶 / 不激活 / 三态）
  panel.py       主面板（灵感池 / 立项 / 喂 / 确认 / 估算 / 分镜表 / 设置）
  pool.py        灵感池（液态玻璃池子：水位 + 泡着的素材 + 落下动效）
  basin.py       结构池
  brain.py       大脑：本地规则 + LLM（结构生长 / 深挖 / 对话 / 一期记忆 / 推荐搜法）
  store.py       SQLite 数据层
  llm.py         OpenAI 兼容网关客户端（chat / 原生 tool_calls / JSON 模式）
  mcp.py         MCP 客户端（tinyfish 只读搜索）
  websearch.py   Bing 免 key 搜索（RSS + HTML 双通道）
  updater.py     GitHub Releases 自动更新
  arrange.py     编排引擎（镜头规划 / 时长 / 提示词 / 时间轴 / SRT）
  export_pack.py 导出编排包（施工单 + 剪映草稿）
  icons.py       QPainter 自绘图标（全项目不用 emoji 当图标）
  theme.py       配色与 QSS
  winutil.py     Win32：不抢焦点、前台 / 光标探针
  uitest.py      自动点击走查（找 bug 用）
  llmtest.py     接线自检（桩掉模型，验链路）
dev-watch.py     开发期自动重启
docs/
  requirements.md 唯一需求真相源（含变更记录）
  mockups/        版式设计稿
```

---

## 开发

```bat
python dev-watch.py     REM 改 app/ 下 .py 自动重启
python app/uitest.py    REM 自动点击走查
python app/llmtest.py   REM 接线自检（不花 token）
```

改动请**先改文档、再改代码、改完回填文档**，并在 `docs/requirements.md` 的变更记录里登记。

---

## 路线图

- [x] 悬浮球 + 灵感池 + 结构化立项
- [x] 收素材（文件 / 拖拽）+ 缩略图
- [x] 全量接 LLM：初识 / 深挖 / 发散 / 对话 / 改结构
- [x] AI 原生工具调用改任意一颗球
- [x] 一期一份的独立记忆 + 跨期 `user.md`
- [x] AI 按当前期推荐搜索词 + 联网搜索（MCP）
- [x] 开源 + 自动更新流水线
- [ ] 划选喂素材（UIA 读取浏览器选区）
- [ ] 更聪明的结构建议（会自己找矛盾点）

---

## 许可证

[MIT](LICENSE) © 2026 llwan
