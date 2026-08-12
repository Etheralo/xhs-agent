# Paper→Post 简易使用说明

Paper→Post 可以搜索 arXiv 论文，生成小红书文案、论文 PDF 前 6 页配图和公众号文章，
再经过人工审核后导出或填充到小红书创作者平台。最终发布按钮由用户手动点击。

## 1. 安装

需要 Python 3.11 及以上版本，并安装 `uv`。

```bash
git clone https://github.com/Etheralo/xhs-agent.git
cd xhs-agent
uv sync --dev
cp .env.example .env
```

打开 `.env`，至少填写以下三项：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=你的模型名称
```

模型服务需要兼容 OpenAI `chat/completions` 接口。不要把包含真实密钥的 `.env`
提交到 Git。

## 2. 启动控制台

```bash
uv run xhs-agent serve
```

浏览器访问 <http://127.0.0.1:8765>。

如果端口被占用，可以更换端口：

```bash
uv run xhs-agent serve --port 9000
```

## 3. 基本工作流程

系统只保留四个主要步骤：

```text
论文入库 → 内容生成 → 内容审核 → 内容发布
```

1. 在“内容工作台”搜索论文，选择论文并加入论文库。
2. 打开论文详情，点击“生成内容”，等待正文和配图生成完成。
3. 查看小红书正文、6 张论文配图、公众号文章和事实底稿；需要时可直接修改并保存文本。
4. 点击“开始内容审核”，完成检查并填写审核人姓名。
5. 审核通过后下载发布包，或者把内容填充到小红书创作者平台。
6. 在平台完成发布后，回到控制台确认发布结果。

来源未核验只会显示提醒，不会阻止内容生成。真实发布前仍应人工核对论文原文、实验结果
和来源信息。已审核内容再次修改后会撤销原审核，需要重新审核。

## 4. 先运行离线示例

如果只想检查安装是否正常，不需要配置真实论文：

```bash
uv run xhs-agent run --demo --select-count 1
uv run xhs-agent serve
```

离线示例是构造数据，只能用于测试流程，不能作为真实论文内容发布。

## 5. 小红书自动填充

自动填充会上传 6 张图片并填写标题和正文，但不会点击“发布”。

在 `.env` 中配置：

```dotenv
XHS_PUBLISH_MODE=browser
XHS_CREATOR_URL=https://creator.xiaohongshu.com/publish/publish?source=official
XHS_BROWSER_CHANNEL=chrome
XHS_BROWSER_HEADLESS=false
XHS_PUBLISH_TIMEOUT_SECONDS=180
```

首次使用需要安装浏览器组件并登录自己的小红书账号：

```bash
uv run playwright install chrome
uv run xhs-agent xhs-login
```

登录完成后，在控制台审核内容并选择填充到小红书。程序填充完成后会保留浏览器页面；
请检查图片、标题和正文，手动点击发布，再回到控制台点击“我已手动发布”。

## 6. 常见问题

### 模型无法生成内容

检查 `.env` 中的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`，修改后重启控制台。

### 提示端口已被占用

关闭旧的服务进程，或者使用 `--port 9000` 启动。

### 小红书要求重新登录

关闭项目专用的 Chrome 窗口，然后重新运行：

```bash
uv run xhs-agent xhs-login
```

### 自动填充后没有自动发布

这是正常行为。系统只负责填充，发布按钮必须由用户手动点击。

更完整的配置、命令和故障排查见 [完整使用说明书](USER_GUIDE.md)。
