# Paper→Post：AI Safety 论文内容工作流

Paper→Post 是一个在本机运行、带人工审核的论文内容生成工具。它可以从 arXiv 搜索和导入
论文，读取 PDF 并生成小红书文案、论文前 6 页配图和微信公众号文章，再由编辑审核、修改
和发布。

系统不会把 arXiv 收录自动写成“顶会录用”，也不会代替用户点击小红书最终发布按钮。

## 主要功能

- 搜索 arXiv，并按论文发布时间优先展示最新结果；
- 将论文加入本地 SQLite 论文库，自动去重和筛选相关主题；
- 下载并按页读取 PDF，生成带页码依据的事实底稿；
- 生成独立的小红书标题、结构化正文和微信公众号长文；
- 直接导出真实论文 PDF 的前 6 页作为小红书配图；
- 流式展示模型读取、事实抽取和正文生成过程；
- 在网页控制台预览并修改小红书标题、正文和公众号内容；
- 展示来源核验提醒，但不强制阻塞内容生成；
- 完成 6 项人工检查后生成带 SHA-256 清单的 ZIP 发布包；
- 将 6 张图片、标题和正文自动填入小红书创作者平台；
- 由用户手动点击发布，并在控制台确认最终发布结果。

工作流只保留四个主要步骤：

```text
论文入库 → 内容生成 → 内容审核 → 内容发布
```

页面对应四种业务状态：

```text
已入库 → 已生成内容 → 已通过审核 → 已发布
```

不符合选题范围的论文会直接删除，不保留“已拒绝”记录。已通过审核的内容如果再次修改，
旧审核会自动撤销，需要重新审核后才能发布。

## 环境要求

- Python 3.11 或更高版本；
- `uv` Python 包管理工具；
- 真实论文搜索、PDF 下载和模型调用需要互联网；
- 小红书自动填充需要 Chrome。

不需要 GPU、向量数据库、消息队列或云服务器。

## 快速安装

```bash
git clone https://github.com/Etheralo/xhs-agent.git
cd xhs-agent
uv sync --dev
cp .env.example .env
```

打开 `.env`，填写模型配置：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=你的模型名称
```

`OPENAI_*` 表示接口协议，不限定模型厂商。程序可以连接 OpenAI，也可以连接任何兼容
OpenAI `chat/completions` 接口的模型服务。`.env` 只保存在本机，不要提交真实密钥。

可选的模型参数：

```dotenv
LLM_STREAM=true
LLM_JSON_MODE=true
LLM_DISABLE_THINKING=true
LLM_READ_TIMEOUT_SECONDS=600
LLM_MAX_INPUT_CHARS=55000
LLM_MAX_COMPLETION_TOKENS=4096
```

## 启动网页控制台

```bash
uv run xhs-agent serve
```

浏览器访问 <http://127.0.0.1:8765>。

如果端口已被占用，可以指定其他端口：

```bash
uv run xhs-agent serve --port 9000
```

控制台默认只监听本机地址，不提供独立的用户账号系统。

## 使用流程

### 1. 论文入库

进入“内容工作台”，输入研究主题或关键词搜索 arXiv。选择目标论文并加入论文库，也可以
扫描近期相关论文。

### 2. 内容生成

打开论文详情并点击“生成内容”。系统会读取 PDF、抽取事实、生成小红书内容和公众号文章，
并导出 PDF 前 6 页。生成过程和模型输出会在页面中逐步展示。

### 3. 内容审核

检查以下内容：

- 小红书标题和正文；
- 6 张论文页面配图；
- 微信公众号文章；
- 事实底稿、核心数字和对应页码；
- 来源核验提醒及操作记录。

标题、正文和公众号文章可以直接在网页中修改。确认内容后点击“开始内容审核”，完成全部
6 项检查并填写审核人姓名，即可批准内容并生成发布包。

### 4. 内容发布

审核通过后可以：

- 下载 ZIP 发布包并手工发布；
- 把图片、标题和正文自动填充到小红书创作者平台；
- 通过自有 webhook 连接器接收发布包。

平台发布完成后，回到控制台确认结果，论文状态才会更新为“已发布”。

## 离线示例

仓库内置 3 篇构造论文，可用于检查本地工作流，不需要模型密钥：

```bash
uv run xhs-agent run --demo --select-count 1
uv run xhs-agent serve
```

构造论文只用于功能测试，不能作为真实研究内容发布。

生成文件位于 `output/<日期>/<paper>/`，主要包括：

```text
facts.json
validation.json
xhs-title.txt
xhs-caption.md
xhs-01.png ... xhs-06.png
wechat.md
wechat.html
publication-images.json
```

## 小红书自动填充

在 `.env` 中启用浏览器模式：

```dotenv
XHS_PUBLISH_MODE=browser
XHS_CREATOR_URL=https://creator.xiaohongshu.com/publish/publish?source=official
XHS_BROWSER_CHANNEL=chrome
XHS_BROWSER_HEADLESS=false
XHS_PUBLISH_TIMEOUT_SECONDS=180
```

首次使用需要安装浏览器组件并登录：

```bash
uv run playwright install chrome
uv run xhs-agent xhs-login
```

在项目专用 Chrome 中完成短信登录，进入发布页面后回到终端按 Enter。此后从控制台填充
内容时，程序会上传 6 张图片并填写标题和正文，然后停止操作并保留浏览器页面。

请人工检查内容、处理验证码或风险提示、点击“发布”，再回控制台点击“我已手动发布”。
程序不会绕过登录、验证码、风控或平台审核。

## 常用命令

```bash
# 初始化数据库
uv run xhs-agent init-db

# 扫描并处理真实论文
uv run xhs-agent run

# 查看论文和审核队列
uv run xhs-agent list
uv run xhs-agent review
uv run xhs-agent show 1

# 启动控制台
uv run xhs-agent serve

# 命令行审核、导出和确认发布
uv run xhs-agent approve 1 --reviewer "编辑姓名"
uv run xhs-agent export 1
uv run xhs-agent mark-published 1
```

## 数据和安全

- `data/agent.sqlite3` 保存论文、草稿和业务状态；
- `data/cache/` 保存下载的论文 PDF；
- `data/xhs-browser-profile/` 保存项目专用 Chrome 登录状态；
- `output/` 保存正文、配图和发布包；
- `.env` 保存 API 密钥和本机配置。

上述目录和 `.env` 默认不会提交到 Git。不要公开 API 密钥、浏览器登录目录或包含未发布
内容的发布包。

## 当前边界

- 小红书模式只填充内容，不自动点击发布；
- 微信公众号默认使用 HTML 或 ZIP 手工发布，也可连接自有 webhook；
- 来源未核验只做提醒，但真实发布前应人工核对正式来源；
- 模型输出和论文解读必须经过人工审核；
- 小红书页面改版后，自动填充定位规则可能需要更新。

## 使用文档

- [简易使用说明](docs/QUICK_START.md)
- [完整使用说明书](docs/USER_GUIDE.md)
