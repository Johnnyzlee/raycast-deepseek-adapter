# DeepSeek Raycast Adapter

**在同一个 Raycast AI Chat 中使用本地扩展、DeepSeek 原生联网和文件图文问答。**

简体中文（主文档） · [English](README.en.md)

这是一个运行在 macOS 本机的 Python 接入服务，连接 Raycast 自定义模型提供方与 DeepSeek 官方 API，合并工具清单、上传指定文件，并保存后续追问需要的原生上下文。它是独立项目，不是 Raycast 商店 Extension，不依赖 Exa 等第三方搜索服务。

## 为什么做这个项目

我原本已经在 Raycast 中配置了 DeepSeek，用它聊天、执行 AI Commands 和总结网页。DeepSeek 提供原生搜索、网页读取、文件解析、Vision 和 OCR 后，我希望实现这样的工作流：

> 在 AI Chat 中让 Finder 找到一份论文，让 DeepSeek 阅读原文和图表，再联网核实相关信息，最后继续围绕同一份文件追问。

直接修改 `providers.yaml` 遇到了两个实际问题：

1. **两套工具清单互相覆盖。** 在本机测试的 Raycast 1.104.36 中，启用客户端工具后，请求中的工具列表会替换配置注入的 DeepSeek 原生工具；部分请求还带有 `tool_choice: none`。关闭客户端工具虽能使用原生联网，却失去了 Finder 等本地工具。这是特定版本和配置下的观察，并非所有版本的固定行为。
2. **本地路径不是文件内容。** Finder 能返回路径或提取文字，但 DeepSeek 云端不能直接打开 Mac 上的文件；文字结果也无法代替论文里的图。需要读取文件字节、上传官方接口，再将 `file_id` 放入对话。

这个项目补上工具合并和文件接入流程，让一次对话能够完成“找到文件 → 看图文 → 联网 → 继续追问”。

## 功能

| 能力 | 实现方式 |
| --- | --- |
| Raycast 本地工具 | 保留请求中实际声明的 Finder、Notes 等函数，由 Raycast 执行 |
| 原生搜索与网页读取 | 合并 `deepseek_web_search`、`deepseek_read_url`，由 DeepSeek 服务端执行 |
| 文档图文问答 | 上传 PDF、Word、PowerPoint 等文档，使用 `rich_layout_recognition`，将原生文件引用加入对话 |
| 图片理解 | 保留 `image_url` 输入；本地 PNG、JPEG、WebP、GIF 可上传供 Vision 阅读 |
| 独立 OCR | 调用 `/v1/tools/ocr`，按页返回文字，可选择返回页码范围 |
| 连续追问 | 恢复 Raycast 未保留的文件引用、思考及服务端工具记录 |
| 缓存 | 按账号、文件内容和解析方式复用上传；缓存 OCR 结果 |
| 用户信息标记 | 动态补充日期、星期、位置字段、客户端类型和语言；位置默认留空 |

原生 Web Search 也支持 `open_page`，阅读网页不一定产生 `deepseek_read_url` 类型的记录。这里没有额外的 Research Web 对话；试用过的两个联网包装已在原生联网验证通过后移除。

## 工作原理

```text
Raycast AI Chat
  │ 问题、聊天记录、当前启用的扩展函数
  ▼
127.0.0.1:18766 — adapter.py
  │ 恢复上下文；合并工具；启用 thinking 和 tool_choice: auto
  ▼
https://api.deepseek.com
  ├─ 原生联网 → DeepSeek 服务端执行
  ├─ Finder/Notes → Adapter 返回调用 → Raycast 执行并再次请求
  ├─ 文件/图片/OCR → Adapter 调用官方接口 → 结果接回对话
  └─ 回答 → Adapter 以兼容的流式格式返回 Raycast
```

模型选择工具，Raycast 执行自己的扩展，Adapter 执行以下三个自定义接入函数：

| 函数 | 参数 | 底层处理 |
| --- | --- | --- |
| `deepseek_open_document` | `path` | 上传 `/files`；图文文档用 `rich_layout_recognition`，文本类用 `user_data` |
| `deepseek_view_image` | `path` | 上传本地图片，加入原生 `file` 内容块 |
| `deepseek_ocr` | `path`，可选 `start_page`、`end_page` | 独立 OCR，识别文字作为工具结果返回 |

这三个名字由本项目定义，实际解析、识图和 OCR 来自官方接口。只有本轮 Raycast 声明的客户端函数才会交回 Raycast；旧历史中的失效函数会得到工具错误。

载入文件后，Adapter 加入 `{"type":"file","file_id":"file-api-..."}`，再让模型继续回答。本地保存最近 200 个对话检查点，以便下一轮恢复引用；这不是用户画像或通用长期记忆。

## 安装

### 1. 准备项目

需要 macOS、Python **3.11+**、Git、DeepSeek API Key，以及可使用 Custom Providers 和所需 AI Extensions 的 Raycast 版本/账号。本程序不解除订阅、扩展权限或 macOS 文件访问限制。

```sh
git clone https://github.com/Johnnyzlee/raycast-deepseek-adapter.git
cd raycast-deepseek-adapter
python3 --version  # 至少 3.11；必要时使用 python3.11 等命令
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp settings.example.json settings.json
```

把项目放在长期保留的位置；登录自启会引用它的绝对路径，安装后不要直接移动或删除目录。

### 2. 配置 Raycast 和文档目录

在支持该入口的 Raycast 版本中，开启 Settings → AI → Experiments → Custom Providers。

将 [examples/providers.example.yaml](examples/providers.example.yaml) 的 DeepSeek 条目合并到 `~/.config/raycast/ai/providers.yaml`，保留其他 provider，在本地替换 `YOUR_DEEPSEEK_API_KEY`。这是**安装前**示例，地址仍为 `https://api.deepseek.com`，安装程序负责改成本地地址。

当前支持 provider ID `deepseek`，其中只配置一个模型 ID `deepseek-flash`；显示名称可以修改。安装程序要求与示例相同的缩进布局，不支持的结构会报错停止，避免重新序列化整个文件。不要把其他模型放在同一个待切换的 provider 中。

编辑本地 `settings.json`：

| 设置 | 默认值 / 含义 |
| --- | --- |
| `port` | `18766`，只监听 `127.0.0.1` |
| `timezone` | `Asia/Hong_Kong`，生成日期和星期 |
| `language` | `中文`，写入信息标记；可改为 `English` |
| `location` | 留空；可手动填写，不自动定位 |
| `allowed_roots` | Desktop、Documents、Downloads；按需增加文档目录 |
| `state_dir` | `~/Library/Application Support/DeepSeek Raycast Adapter` |

`settings.json` 被 Git 忽略。公开默认值放在 `settings.example.json`；不要提交个人路径或凭据。安装后改端口需同步 provider 地址；改状态目录会影响缓存和安装记录，建议先回退再重新安装。

### 3. 安装登录自启

```sh
./.venv/bin/python manage.py install --dry-run
./.venv/bin/python manage.py install
./.venv/bin/python manage.py status
```

`--dry-run` 只检查配置，不写文件、不启动服务、不调用 API。安装会备份原配置，创建 LaunchAgent，确认服务健康，再把 DeepSeek 地址改为 `http://127.0.0.1:18766`，设置 `abilities.tools.supported: true`。Key 保持原样，Vision 应按示例开启。

在 Raycast 选择该自定义模型，启用所需 AI Extensions；模型列表未刷新时再重启 Raycast。服务在**登录 macOS 后**自启并等待请求，意外退出由 launchd 重启。空闲时不调用 API；注销、关机或休眠期间无法正常处理请求。

## 使用

- “用 Finder 找到 Downloads 里的报告，用图文解析解释 Figure 3。”
- “读取 `/Users/YOUR_USER/Downloads/report.pdf`，说明图中的主要趋势。”
- “读取这张截图，解释文字和图标。”（附图或提供本地图片路径。）
- “对这份文件做独立 OCR，只返回第 2–3 页的文字。”
- “联网查找这篇论文的后续工作，给出来源，再比较它们的方法。”
- “回到刚才的 PDF，第二张图说明了什么？”

OCR 页码只限制返回内容，首次识别仍处理整份文件。网页 PDF 需要先下载；URL 或本地路径本身不等于已上传的文件。

配合 [PDF Summary 扩展](https://github.com/Johnnyzlee/raycast-deepseek-pdf-summary)：扩展生成浏览器 PDF 摘要，保存原文至 `Downloads/Raycast PDF Library` 后打开 AI Chat。随后用 Adapter 载入本地原文，继续问图文问题。扩展自己的摘要请求仍直连 DeepSeek，两者可以独立使用。

## 管理、更新与回退

```sh
./.venv/bin/python manage.py status
./.venv/bin/python manage.py restart

# 更新已有安装，不要重复运行 install
git pull --ff-only
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m unittest -v
./.venv/bin/python manage.py restart

# 恢复安装前配置，移除登录自启
./.venv/bin/python manage.py rollback
```

回退前会检查 provider 文件是否有安装后的修改，若有便停止，避免覆盖新配置。原备份位置见状态目录 `installation.json`。回退不会删除缓存或远端文件；如需清理，先执行：

```sh
# 删除本 Adapter 为当前账号上传的远端文件，清除该账号的对话检查点
./.venv/bin/python manage.py cleanup-files
```

清理后旧文件可能需要重新载入；本地 OCR 缓存保留。服务停止但 provider 仍指向本地端口时，聊天会连接失败。

## 数据、权限和费用

- 文件函数会将指定文件上传到 **DeepSeek 官方 API**，没有为每次上传额外增加确认弹窗。
- 路径受 `allowed_roots`、真实路径检查、扩展名和大小限制约束；阻止常见凭据目录和 `.env`，但这不是完整的系统级沙箱。只开放适合此工作流的文档目录。
- API Key 从现有 Raycast 配置读取，不另存到源码或普通日志。上游固定为 `api.deepseek.com`，不跟随重定向。
- 状态目录权限 `700`，数据库 `600`。数据库**包含聊天上下文、工具结果、文件引用和 OCR 文字**，应按私人资料管理，不要公开。provider 备份含 Key，同样需要保密。
- 日志只记录必要的服务和请求状态，不记录正常请求正文或认证头。
- 上传可复用缓存，远端文件不会在每次回答后删除。解析、存储、OCR、联网和聊天按官方规则计费；后续问答仍可能产生文件上下文输入费用。

## 当前范围与限制

- 仅针对 `deepseek-flash` 的 Chat Completions 接口；不是任意模型、provider 或 Responses API 的通用代理。
- 会设置 `tool_choice: auto` 并启用 thinking，不适用于严格禁止工具调用的模式。
- 图文解析可能漏检图片；OCR 只有识别文字，不能代替完整的图像理解。
- 本地文件限 100 MiB，`user_data` 路径另限 32 MiB，请求体限 64 MiB；仍受官方接口限制，内联 base64 会增大请求体。
- 文本、表格、部分电子书按 `user_data` 上传，但未逐一验证全部格式。语音识别、语音合成和网页 PDF 自动下载不在范围内。
- 单请求最多 10 轮内部工具处理。历史被改写、超过检查点保留范围或远端文件被删除后，可能要重新载入文件。
- 工具由模型选择；有限实测不保证所有网页、扩展组合和历史对话都成功。见 [验证记录](VALIDATION.md)。

## 开发

| 文件 | 用途 |
| --- | --- |
| `adapter.py` | HTTP/SSE、工具分发、官方 API、文件及会话缓存 |
| `manage.py` | 安装、状态、重启、回退和远端文件清理 |
| `settings.example.json` | 公开默认设置 |
| `examples/providers.example.yaml` | 安装前 provider 示例，只含 Key 占位符 |
| `test_adapter.py` / `test_manage.py` | 协议、文件、安装与回退保护检查 |
| `README.md` / `README.en.md` | 中文 / 英文说明 |

```sh
./.venv/bin/python -m unittest -v
./.venv/bin/python -m py_compile adapter.py manage.py
```

自动检查无需真实 Key，不上传文件、不调用收费 API。端到端测试请使用自行创建的文件，检查“搜索 → 本地工具返回路径 → 图文读取 → 网页核实 → 文件追问”，以实际 `completed` 服务端记录为依据，不能只相信模型说“已联网”。

## 官方依据与许可

[Web tools](https://api-docs.deepseek.com/guides/web_tools/) · [File parsing](https://api-docs.deepseek.com/guides/document_parsing/) · [Vision](https://api-docs.deepseek.com/guides/vision/) · [OCR](https://api-docs.deepseek.com/guides/ocr/) · [Latest reminder](https://api-docs.deepseek.com/guides/latest_reminder/)

[MIT License](LICENSE)。个人项目，与 Raycast、DeepSeek 官方无隶属关系。
