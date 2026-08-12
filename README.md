# AI Safety 论文内容 Agent

这是一个本机运行、人工审核的小红书与微信公众号内容流水线。它从 arXiv
发现候选论文，严格区分“预印本”与“已核验会议论文”，从同一份可追溯事实底稿
生成独立的小红书标题、结构化正文、论文 PDF 前六页配图和公众号长文。小红书可选用专用浏览器
自动填充图片、标题和正文，最终发布按钮始终由用户手动点击。

完整设计依据与取舍见 [agent-proposal.md](agent-proposal.md)。
面向编辑和运营人员的完整操作步骤见 [Paper→Post 使用说明书](docs/USER_GUIDE.md)。

## 已实现的闭环

```text
arXiv / 本地样例
  → SQLite 去重
  → AI Safety 主题筛选
  → 来源状态提醒（不阻塞生成）
  → 结构化评分与选题
  → PDF 按页抽取 / 事实底稿
  → 小红书 + 公众号草稿
  → 自动一致性检查
  → ready_for_review
  → 人工 approve
  → 可导出的发布包 / 小红书浏览器自动填充
```

页面只展示四个业务状态：

```text
已入库 → 已生成内容 → 已通过审核 → 已发布
```

不符合选题范围的论文会直接删除，不保留“已拒绝”记录。系统内部仍保留细分处理节点，
用于断点续跑和任务诊断，但不会作为页面状态展示。

`approve` 之前不能导出。浏览器自动填充也必须经过同一组人工审核，不能跳过审核门。

## 1. 安装

需要 Python 3.11 或更高版本。推荐使用 `uv`：

```bash
uv sync --dev
cp .env.example .env
```

真实运行请填写 OpenAI-compatible 模型配置。可使用 OpenAI，也可使用任何提供兼容
`chat/completions` 接口的模型服务。缺少模型密钥时，程序仍能做规则分类和
保守底稿，但不会猜测 PDF 中没有明确提取出的结果。OpenAlex key 用于辅助会议
匹配；OpenAlex 只能产生 `matched_secondary`，不能单独证明论文已被会议录用。

## 2. 先跑离线验收

仓库内置 3 篇明确标注为 constructed demo 的构造论文，只用来证明工程闭环，
其中的题目、作者、会议和数字都不能作为真实研究内容发布。

```bash
uv run xhs-agent run --demo --select-count 3
uv run xhs-agent review
```

每篇会在 `output/<日期>/<paper>/` 生成：

```text
source.json
facts.json
validation.json
xhs-slides.json
xhs-title.txt
xhs-caption.md
publication-images.json
xhs-01.png ... xhs-06.png
wechat.md
wechat.html
```

重复运行相同样例时，SQLite 会记录 `duplicate_skipped`，不会生成第二份候选。

## 3. 启动可视化控制台

```bash
uv run xhs-agent serve
```

浏览器打开 `http://127.0.0.1:8765`。控制台直接使用同一套 SQLite、流水线和
人工审核状态机，支持：

- 搜索 arXiv，选择并加入候选论文；
- 扫描最近论文或运行离线示例；
- 为单篇真实论文直接导出 PDF 前 6 页作为小红书配图，并生成公众号长文；
- 预览发布配图、文案、事实底稿、校验结果及操作记录；
- 在人工审核前直接修改小红书标题、正文和公众号全文，保存后的版本会进入发布包；
- 按需补充会议官网证据；未核验时仅提醒，不阻塞内容生成；
- 完成 6 项人工审核后，一键批准并下载发布包，或自动填充到小红书发布页；
- 人工发到平台后确认发布状态。

控制台默认只监听本机地址，不包含自己的账号系统。默认发布模式仍是 `manual`；没有
连接器时由编辑下载后手工发布。要启用本机浏览器自动填充，在 `.env` 配置：

```bash
XHS_PUBLISH_MODE=browser
XHS_CREATOR_URL=https://creator.xiaohongshu.com/publish/publish?source=official
XHS_BROWSER_CHANNEL=chrome
XHS_BROWSER_HEADLESS=false
XHS_PUBLISH_TIMEOUT_SECONDS=180
```

首次使用或登录失效后，运行：

```bash
uv sync --dev
uv run playwright install chrome
uv run xhs-agent xhs-login
```

程序会打开只供本项目使用的 Chrome 配置目录。完成短信登录并进入发布页后，回到终端
按 Enter 保存登录态。此后在控制台完成内容审核并选择“小红书”，后台任务会上传 6 张
图片、填写标题和完整正文，然后停止自动化操作并保留浏览器页面。程序不会寻找或点击
“发布”按钮。编辑核对内容后在小红书页面手动点击发布，再回控制台点击“我已手动发布”；
此时论文才会被标记为“已发布”。

小红书标题和正文分开保存。标题按“会议简写：核心总结”生成，核心总结最多 15 字，
完整标题最多 20 字；正文固定包含“论文题目、会议来源、研究摘要、研究背景、核心创新、
实验结果”六部分，生成目标控制在 900 字内，网页编辑保存时硬性限制为 1000 字。

如已有自己的发布服务，也可以改用 webhook 模式：

```bash
XHS_PUBLISH_MODE=webhook
XHS_PUBLISH_WEBHOOK_URL=https://your-publisher.example/xhs
WECHAT_PUBLISH_WEBHOOK_URL=https://your-publisher.example/wechat
PUBLISH_WEBHOOK_TOKEN=your-private-token
```

连接器接收 `multipart/form-data`：`metadata` 是 JSON，`package` 是经过人工审核并带
SHA-256 清单的 ZIP。连接器返回 JSON `status` 可取 `delivered`、`submitted` 或
`published`；所选渠道全部返回 `published` 时，控制台会自动把论文记为已发布。
密钥仅在本机后端读取，不会传给浏览器。浏览器模式依赖创作者平台当前页面结构，平台
改版后可能需要更新定位规则；短信验证、验证码和风险提示不会被程序绕过。可修改控制台端口：

```bash
uv run xhs-agent serve --port 9000
```

## 4. 真实运行

```bash
uv run xhs-agent run
```

来源核验不再是内容生成的硬门槛。未核验论文会继续生成，但页面和发布审核弹窗会提醒
编辑核对。自动从 OpenAlex 匹配到的来源不会被当成已核验录用；如需补充证据，可从会议
官方 accepted papers / proceedings 页面核对后，在
`config/venue_overrides.yaml` 添加覆盖：

```yaml
"2608.01234":
  venue: USENIX Security Symposium
  venue_code: usenix_security
  status: verified
  evidence_url: https://www.usenix.org/conference/usenixsecurity26/presentation/example
```

再运行时，新论文可通过门槛。已经被拒绝的历史候选不会被程序暗中改回；这保留了
审计记录。如需重新处理，应使用新的数据库或由编辑明确迁移记录，而不是删除审核历史。

## 5. 人工审核与导出

先阅读 `validation.json`，然后逐项核对：

1. 论文是否值得发布；
2. 会议证据是否来自可信官方页面；
3. 问题和方法是否讲反；
4. 每个结果能否在标注 PDF 页码找到；
5. 通俗例子是否误导；
6. “作者展望”和“编辑推演”是否明确分开；
7. 6 张发布配图是否清晰可读（真实论文为 PDF 前六页）。

审核通过后使用数据库 ID 或 arXiv ID：

```bash
uv run xhs-agent approve demo.00001 --reviewer "编辑姓名"
uv run xhs-agent export demo.00001
```

`approve` 会生成带 SHA-256 文件摘要的 `FINAL-APPROVED.json`；`export` 仅接受
已有该清单且数据库状态为 `approved` 的内容。手工发到两个平台后可记录：

```bash
uv run xhs-agent mark-published demo.00001
```

## 6. 常用命令

```bash
uv run xhs-agent init-db
uv run xhs-agent list
uv run xhs-agent list --status ready_for_review
uv run xhs-agent review
uv run xhs-agent show 1
uv run xhs-agent serve
uv run pytest
```

可用环境变量：

- `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`；
- `LLM_STREAM`、`LLM_JSON_MODE`、`LLM_DISABLE_THINKING`；
- `LLM_READ_TIMEOUT_SECONDS`、`LLM_MAX_INPUT_CHARS`、`LLM_MAX_COMPLETION_TOKENS`；
- `OPENALEX_API_KEY`；
- `XHS_PUBLISH_MODE`、`XHS_CREATOR_URL`、`XHS_BROWSER_PROFILE_DIR`；
- `XHS_BROWSER_CHANNEL`、`XHS_BROWSER_HEADLESS`；
- `XHS_PUBLISH_TIMEOUT_SECONDS`；
- `XHS_AGENT_DATA_DIR`、`XHS_AGENT_OUTPUT_DIR`，用于隔离测试或部署数据。

所有密钥只从环境变量读取，`data/`、`output/` 和 `.env` 默认被 Git 忽略。

## 7. 当前边界

- 小红书浏览器模式只填充内容，不点击发布，也不绕过首次登录、验证码、风险控制或平台审核；公众号仍需手工发布或 webhook；
- 不把 arXiv 分类、作者备注或 OpenAlex 二手匹配写成“顶会录用”；
- 不自动把失败草稿推进到待审状态；
- 不需要 GPU、向量数据库、消息队列、多 Agent 或云服务器；
- 真实内容仍需人工逐项核对原文；会议证据是建议核对项，不阻塞审核。

当连续人工发布 2–4 周并确认内容价值后，再考虑定时服务器、飞书通知、公众号草稿
接口或审核网页。第一阶段的目标是来源可信、事实可追溯、表达清楚，而不是无人值守。
