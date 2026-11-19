<div align="center">

<h1>DeepSeek Raycast Adapter</h1>
<p>Client tools, native web access, and visual document Q&A in one conversation.</p>
<p><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>
<p><a href="#installation">Installation</a> · <a href="#usage">Usage</a> · <a href="#architecture">Architecture</a> · <a href="#operations">Operations</a> · <a href="#data-handling">Data handling</a></p>
<sub>macOS · Python 3.11+ · Local service · MIT License</sub>

</div>

---

A local Python service for macOS connecting Raycast Custom Providers to the official DeepSeek API. It merges tool declarations, uploads requested files, and restores native context for follow-up questions. This is a standalone project, not a Raycast Store extension. Native search is executed by DeepSeek.

## Why this project

Raycast client tools and DeepSeek’s native APIs provide complementary capabilities. A document workflow should be able to combine them in one conversation:

> Ask Finder to locate a paper, let DeepSeek read its text and figures, verify information online, and keep discussing the same document in AI Chat.

Editing `providers.yaml` alone exposed two problems:

1. **Competing tool lists.** In testing with Raycast 1.104.36, enabling client tools replaced the native tool list injected through provider settings. Some requests also used `tool_choice: none`. Disabling client tools allowed native web access but removed Finder and other local tools. This is an observation about the tested version/configuration, not a claim about every Raycast version.
2. **A local path is not file content.** Finder can return a path or extracted text, but DeepSeek cannot open a file on a Mac remotely. Text extraction also loses visual information. The client must upload the bytes and include the resulting `file_id` in the conversation.

The adapter fills these gaps so one conversation can locate a file, inspect figures, search the web, and continue with the original document.

## Features

| Capability | Implementation |
| --- | --- |
| Raycast client tools | Preserve the Finder, Notes, and other functions actually supplied by Raycast; Raycast executes them |
| Native web search and reading | Merge `deepseek_web_search` and `deepseek_read_url`; DeepSeek executes them server-side |
| Visual document Q&A | Upload PDFs, Word documents, PowerPoint files, and other accepted documents using `rich_layout_recognition` |
| Vision | Preserve `image_url` inputs and upload local PNG, JPEG, WebP, or GIF images |
| Standalone OCR | Call `/v1/tools/ocr`, returning text by page with an optional output page range |
| Follow-up context | Restore file references, reasoning, and server-tool records Raycast may omit |
| Caching | Reuse uploads by account, file content, and parsing purpose; cache OCR results |
| User information markers | Add date, weekday, location field, client type, and language; location is empty by default |

Native Web Search also supports `open_page`, so reading a URL does not always produce a `deepseek_read_url` record. Web tools run within the main model conversation; no separate search conversation is created.

## Architecture

```text
Raycast AI Chat
  | messages + enabled client function declarations
  v
127.0.0.1:18766 — adapter.py
  | restore context + merge tools + enable thinking and automatic tool choice
  v
https://api.deepseek.com
  +-- native web tools --> executed by DeepSeek
  +-- Finder/Notes --> returned to Raycast --> Raycast executes and sends results
  +-- document/image/OCR --> adapter calls official APIs --> results rejoin chat
  +-- answer --> compatible streaming response back to Raycast
```

The model chooses tools. Raycast executes its own extensions. The adapter implements three functions:

| Function | Arguments | Processing |
| --- | --- | --- |
| `deepseek_open_document` | `path` | Upload through `/files`; use `rich_layout_recognition` for visual documents and `user_data` for text-like formats |
| `deepseek_view_image` | `path` | Upload an image and insert a native `file` content block |
| `deepseek_ocr` | `path`, optional `start_page`, `end_page` | Call standalone OCR and return recognized text as a tool result |

These names belong to this project; the parsing, vision, and OCR capabilities come from official APIs. Only client functions declared by Raycast in the current request are returned to Raycast. Obsolete functions in old histories receive a tool error.

After an upload, the adapter adds `{"type":"file","file_id":"file-api-..."}` and continues the model request. It stores the most recent 200 conversation checkpoints to restore references on later turns. This is not a user-profile or general long-term memory system.

## Installation

### 1. Prepare the project

Requirements: macOS, Python **3.11+**, Git, a DeepSeek API key, and a Raycast version/account with access to Custom Providers and the AI Extensions you want to use. This project does not bypass subscriptions, extension permissions, or macOS file-access restrictions.

```sh
git clone https://github.com/Johnnyzlee/raycast-deepseek-adapter.git
cd raycast-deepseek-adapter
python3 --version  # Must be 3.11+; use python3.11 or another suitable executable if needed
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp settings.example.json settings.json
```

Keep the project in a permanent location: the LaunchAgent uses absolute paths. Do not move or delete the installed directory without reinstalling appropriately.

### 2. Configure Raycast and local settings

In Raycast versions exposing this option, enable Settings → AI → Experiments → Custom Providers.

Merge the DeepSeek entry from [examples/providers.example.yaml](examples/providers.example.yaml) into `~/.config/raycast/ai/providers.yaml`. Preserve other providers and replace `YOUR_DEEPSEEK_API_KEY` locally. This is the **pre-installation** example: its URL is still `https://api.deepseek.com`. The installer switches it to localhost.

The supported provider ID is `deepseek`, containing one model with ID `deepseek-flash`. Display names can change. The installer requires the indentation layout shown in the example and stops on unsupported layouts instead of reserializing the whole file. Put other models in a separate provider before installation.

Edit local `settings.json`:

| Setting | Default / meaning |
| --- | --- |
| `port` | `18766`, bound only to `127.0.0.1` |
| `timezone` | `Asia/Hong_Kong`, used for date and weekday |
| `language` | `中文`; change to `English` for the information marker |
| `location` | Empty; optionally entered manually, never automatically located |
| `allowed_roots` | Desktop, Documents, Downloads; add document directories as needed |
| `state_dir` | `~/Library/Application Support/DeepSeek Raycast Adapter` |

`settings.json` is ignored by Git; shared defaults live in `settings.example.json`. Do not commit personal paths or credentials. Changing the installed port also requires updating the provider URL. Changing the state directory changes cache and installation-record locations; roll back before reinstalling with a different one.

### 3. Install the login service

```sh
./.venv/bin/python manage.py install --dry-run
./.venv/bin/python manage.py install
./.venv/bin/python manage.py status
```

The dry run validates configuration without writing files, starting a service, or calling an API. Installation backs up the original provider file, creates a LaunchAgent, checks service health, and then sets the DeepSeek URL to `http://127.0.0.1:18766` and `abilities.tools.supported` to `true`. The key stays unchanged; enable Vision as in the example.

Select the custom model in Raycast and enable the required AI Extensions. Restart Raycast if its model list has not refreshed. The service starts **when you log into macOS**, waits for requests, and is restarted by launchd if it exits. It makes no API requests while idle; logout, shutdown, and sleep interrupt availability.

## Usage

- “Use Finder to locate the report in Downloads, then use visual document parsing to explain Figure 3.”
- “Read `/Users/YOUR_USER/Downloads/report.pdf` and explain the chart's main trend.”
- “Read this screenshot and explain its text and icons.” Attach it or provide its local path.
- “Run standalone OCR on this file and return only pages 2–3.”
- “Search for follow-up work on this paper, cite sources, and compare the methods.”
- “Back to the PDF: what does the second figure show?”

OCR page arguments limit returned output, not the first recognition pass, which processes the whole file. A web-hosted PDF must be downloaded first; a URL or local path alone is not an uploaded file.

## Operations

```sh
./.venv/bin/python manage.py status
./.venv/bin/python manage.py restart

# Update an existing installation; do not run install again
git pull --ff-only
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m unittest -v
./.venv/bin/python manage.py restart

# Restore the pre-install provider file and remove login startup
./.venv/bin/python manage.py rollback
```

Rollback refuses to overwrite provider edits made after installation. The backup path is recorded in `installation.json` in the state directory. Rollback does not remove caches or remote files. If necessary, clean up first:

```sh
# Delete this adapter's uploads for the current account and its conversation checkpoints
./.venv/bin/python manage.py cleanup-files
```

Old file conversations may then require reloading the original. Local OCR caches remain. Stopping the service while the provider still points to localhost makes chat requests fail.

## Data handling

- File functions upload specified files to the **official DeepSeek API**. There is no additional per-upload confirmation dialog.
- Access is restricted by allowed roots, resolved paths, extensions, and size limits. Common credential directories and `.env` files are blocked, but this is not a complete OS-level sandbox. Allow only appropriate document directories.
- The key is read from the existing Raycast provider file, not copied into source code or ordinary logs. The upstream origin is fixed to `api.deepseek.com`; redirects are refused.
- State-directory permissions are `700`, database permissions `600`. The database **contains conversation context, tool results, file references, and OCR text**. Treat it as private data. Provider backups contain the key and must also stay private.
- Logs contain basic service/request status, not normal message bodies or authentication headers.
- Cached uploads are reused; remote files are not deleted after every answer. Parsing, storage, OCR, web tools, and chat are billed under official API rules. Follow-ups can still incur file-context input costs.

## Scope and limitations

- Supports `deepseek-flash` through Chat Completions, not arbitrary models/providers or the Responses API.
- Sets `tool_choice: auto` and enables thinking; unsuitable for a strict no-tools mode.
- Visual parsing can miss images. OCR returns text and is not a substitute for visual understanding.
- Local files are limited to 100 MiB, `user_data` to 32 MiB, and request bodies to 64 MiB, also subject to official API constraints. Inline base64 increases body size.
- Text, spreadsheet, and some ebook extensions use `user_data`, but not every accepted format has been individually verified. Speech, audio generation, and automatic web-PDF downloading are outside scope.
- Up to 10 internal tool rounds per request. Changed histories, expired checkpoints, or deleted remote files may require loading the file again.
- Tool selection is model-driven. Limited tests cannot guarantee every website, extension combination, or history. See [validation notes](VALIDATION.md) (Chinese).

## Development

| File | Purpose |
| --- | --- |
| `adapter.py` | HTTP/SSE, dispatch, official API calls, file and conversation caches |
| `manage.py` | Install, status, restart, rollback, remote-file cleanup |
| `settings.example.json` | Public default settings |
| `examples/providers.example.yaml` | Pre-install provider example with a placeholder key |
| `test_adapter.py` / `test_manage.py` | Protocol, file, installation, and rollback checks |
| `README.md` / `README.zh-CN.md` | English / Chinese documentation |

```sh
./.venv/bin/python -m unittest -v
./.venv/bin/python -m py_compile adapter.py manage.py
```

Automated tests need no real key, upload nothing, and make no paid API calls. For live validation, use synthetic files and check search → client tool path → visual reading → web verification → file follow-up. Inspect actual completed server-tool records rather than trusting the model's claim that it searched.

## References and license

[Web tools](https://api-docs.deepseek.com/guides/web_tools/) · [File parsing](https://api-docs.deepseek.com/guides/document_parsing/) · [Vision](https://api-docs.deepseek.com/guides/vision/) · [OCR](https://api-docs.deepseek.com/guides/ocr/) · [Latest reminder](https://api-docs.deepseek.com/guides/latest_reminder/)

[MIT License](LICENSE). A personal project, not affiliated with Raycast or DeepSeek.
