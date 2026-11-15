# Muse Registration Studio

独立的 Muse 纯 HTTP 注册网页，包含 Python 后端与免构建的静态前端。注册链路无需浏览器、Playwright、Camoufox 或 RegFlow；浏览器仅用于显示本地控制台。

协议实现引用 [duwqijlk/muse-protocol](https://github.com/duwqijlk/muse-protocol)，使用 Git submodule 固定在 `be34c5e4893e5d109a15ffe46b14b416f60e0abf`。本仓库提供独立页面、任务调度、状态验证和本地结果管理；不把上游代码复制成另一个版本。

## Windows 启动

需要 Python 3.12+ 和 Git。运行网页本身不需要 Node.js。

```powershell
git clone --recurse-submodules https://github.com/HoshinoAz/museai-register.git D:\gpt\muse
cd D:\gpt\muse
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

打开 **http://127.0.0.1:8787**。也可双击 `setup.bat` 安装，再双击 `start.bat` 启动。

本机目录已安装依赖时，直接运行 `start.bat` 即可。

安装依赖时需要网络代理：

```powershell
.\setup.ps1 -NetworkProxy http://127.0.0.1:7897
```

该参数只用于 Git / pip 下载；**注册代理在网页中另外填写**，不会擅自复用系统下载代理。

修改端口与账号任务并发数：

```powershell
.\start.ps1 -Port 8787 -Workers 2
```

默认并发 1，上限 5。后端始终只监听本机 `127.0.0.1`，请通过该地址访问。

## Linux / macOS

```bash
git clone --recurse-submodules https://github.com/HoshinoAz/museai-register.git
cd museai-register
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

## 网页功能

- 单账号注册、最多 50 个邮箱的批量导入；批量共用表单中的姓名和生日。
- 手动填写 OTP，或轮询独立邮箱 API；两种方式可在等待阶段并用。
- 注册会话复用同一代理配置、Cookie jar 与 waterfall ID；支持 HTTP / HTTPS / SOCKS5、动态 IP 与独立前置代理。
- 默认 Chrome 152，按邮箱加密保存独立协议身份；UA、Client Hints、TLS 配置与语言保持一致，重试及重启后复用。
- 可选邀请码兑换：会话验证后，先唤醒 VM 并初始化 sharing，再提交兑换。
- 显示任务阶段、脱敏 HTTP 状态、候补状态及失败原因；支持停止、搜索和筛选。
- 账号凭据加密落盘，支持单账号和全部账号的 JSON 导出。

姓名字段明确区分姓 `last_name` 与名 `first_name`，生日必须有效且年满 18 周岁。勾选确认后，程序会提交服务端动态返回的条款版本与披露正文；不会用 HAR 中的旧值重放。

### 邮箱 API

每个邮箱提供一个专属接码 URL，GET 返回：

```json
[{ "otp": "123456", "time": "2026-11-16T00:00:30Z" }]
```

`time` 必须包含时区；程序只使用发送请求开始之后的最新 6 位验证码。同一时间出现不同验证码视为歧义，同一任务不会重复提交已使用的验证码。邮箱 API 走独立直连 Session，不附带 Muse Cookie，也不经过注册代理。

自动轮询间隔约 2 秒；默认总等待 180 秒，可调 30 至 600 秒。支持手动重发，至少间隔 45 秒，最多两次，重发不延长总等待时间。

批量清单每行支持两种格式：

```text
one@example.com----https://mail.example.com/otp/one
two@example.com
```

第二行将等待手动验证码。可另外粘贴代理池，每行一个 URL 或四段格式，按邮箱顺序循环分配，并统一应用代理模式与前置设置。任何任务出现异常会停止本批尚未结束的任务，不补邮箱、不自动重复创建；已经发出的请求需等待返回。

### 动态 IP 与前置代理

展开“连接与更多选项”，支持以下代理格式。凭据中的特殊字符使用 URL 格式时应 percent-encode：

```text
http://user:password@host:8000
https://user:password@host:443
socks5h://user:password@host:1080
host:8000:username:password
```

**固定代理**保留原凭据；**动态 IP**为每个账号任务生成独立 session，同一任务从首页到会话激活复用同一份凭据。自动识别用户名或密码中的 `lsid` / `sessid` / `session` / `sid`，保留原 token 长度及纯数字格式，不修改国家和 TTL。也可以使用模板：

```text
host:8000:customer-country-us-session-{session}-lifetime-30:password
```

供应商必须支持 sticky session；程序不能把逐请求随机换 IP 的入口凭空固定。未识别 session 参数时会提示。与 RegFlow 一致，`gate.kookeey.info:1000`、`geo.iprocket.io:59999`、`prem.country.iprocket.io:9595` 归一为 SOCKS5。

启用前置代理，例如 `http://127.0.0.1:7897`，连接顺序为：**本机 → 前置代理 → 注册代理 → Muse / Meta**。注册代理留空时只经过前置代理。前置支持 HTTP / HTTPS / SOCKS5；HTTPS 节点保留证书验证。邮箱 API 仍使用独立直连 Session；环境变量中的系统代理不会隐式参与这些请求。

首次使用前置时，点击“安装代理链内核”。安装器从 [Mihomo 官方发行页](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.29) 下载固定 v1.19.29，并验证内置 SHA256；二进制仅保存在本项目 `data/bin/`。下载可以经过填写的前置代理，不经过注册代理。每任务启动独立内核，使用 [dialer-proxy](https://wiki.metacubex.one/en/config/proxies/dialer-proxy/) 连接两跳，只监听 localhost，随机端口和认证，结束时清理进程及临时配置。内核缺失、认证或连接失败均停止，不回退直连。

“测试当前代理”只查询出口 IP 和 GET Muse 首页，不发送验证码或创建账号。批量池不会逐条测试。正式任务会重新生成自己的动态 session，因此测试出口可能与正式任务不同。默认在代理模式下，注册前与提交创建前通过 `api.ipify.org` 采样出口 IP；采样失败或不一致时不提交创建，可取消勾选此检查。两次相同只代表采样一致，不能保证全流程从未换 IP。

### 一号一协议指纹

在“连接与更多选项”的“账号协议指纹”中，默认 **Chrome 152 / 自动分配与复用**。首次为邮箱生成独立随机种子和身份 ID，选择当前运行库支持的 Chrome 152 Windows 或 macOS 原生 TLS / HTTP2 配置；也可以明确选择其中一种系统。已保存的邮箱固定使用原身份与语言，切换系统或语言会在发送前报错，选择自动复用可沿用原配置。批量邮箱各有独立身份，历史任务不会被改写或自动重提。

种子和配置加密存入本项目数据库，恢复时检查配置摘要；损坏、密钥不匹配或运行库不支持时停止，不更换种子或降级到 Chrome 142。页面、日志和凭据导出包含 ID、浏览器、系统和 SHA256 摘要，公开 API 不包含种子。身份 ID 是本地关联标识，不是网站签发或接受的设备凭证；不同邮箱可以使用相同浏览器型号，不能据此宣称每个账号具有独一无二的 JA3。

每次请求固定匹配的 `User-Agent`、`Sec-CH-UA`、系统、移动标记及 `Accept-Language`，禁止单个请求覆盖。按 [curl_cffi 官方说明](https://curl-cffi.readthedocs.io/en/latest/quick_start.html#headers) 关闭默认头并显式区分导航与 API：`Sec-Fetch-User` 和 `Upgrade-Insecure-Requests` 只出现在导航中，JSON 请求不再混入它们。运行库仍负责对应的 TLS / HTTP2 模拟，而非仅改 UA 字符串。

本功能覆盖协议层可发送的身份字段。纯 HTTP 不执行 Canvas、WebGL、AudioContext 或浏览器 JS；当前证据未确定 Muse 用于这些字段的提交入口，因此未伪造额外 Cookie 或指纹参数。`registrationBlocked` 只说明创建接口拒绝请求，不能单凭错误码确定是指纹、IP、邮箱或其他服务端条件，也不保证升级浏览器后解除拒绝。

## 完成条件与中断

流程为首页 → restart / CSRF → send-otp → confirm-otp → register-completion → Meta bootstrap → 动态 disclosure → accept-tos → Hatch 访问验证 → 可选邀请码。

| 显示                 | 含义                                                                                          |
| -------------------- | --------------------------------------------------------------------------------------------- |
| 已创建               | register-completion 返回有效 `auth_meta_session`                                              |
| 会话已验证           | 动态披露已获 `success=true`；GET `/api/hatch/invite` 返回有效非失败 JSON，且存在 `hatch_sess` |
| 已创建 · 待确认      | 已有账号，但后续引导、地区、条款、访问或邀请码步骤未完成                                      |
| `region_waitlist`    | 已创建但服务端判定地区候补，未证明已取得 Hatch 权限                                           |
| `creation_uncertain` | 创建请求已发出但响应未知，或创建时服务重启；该邮箱禁止再次提交                                |
| 服务已中断           | 重启前的活动任务已中断，不会自动重新注册                                                      |

邀请码返回 `invalid`、`used_up` 或 `already_redeemed` 会单独记录，已经验证的会话不会因此变成“未注册”。`accept-tos` 和账号创建均不自动重试。

“会话已验证”严格指上表的接口检查，不声称原生输入行为一致、所有功能可用，或获得了未检查的其他权限。导出中的 `frl_access_token` 是 **Meta FRL 凭据**，不是 Muse 原生 access token；字段与 `credential_type` 均明确区分。

## 数据

运行数据只写入本项目 `data/`：

```text
data/
  muse.sqlite3    任务、脱敏事件、加密的账号凭据
  secret.key     本地加密密钥
  bin/           本项目安装的代理链内核
  proxy-sessions/  活动任务的临时代理配置，任务结束清理
```

历史状态重启后保留；HTTP Session 本身不恢复。结果包含 `registered`、`hatch_access_verified` 等独立标记；有 token 或 Cookie 不会单独把任务标记为验证成功。

密钥与数据库需一起备份。该加密不抵御同时读取密钥和数据库的本机访问者。导出的 JSON 包含明文凭据，应单独保管。代理密码、OTP URL 和验证码不保存到任务历史；日志不记录 token、Cookie、完整错误响应或 URL 查询参数。`data/`、HAR、日志、环境文件与虚拟环境均已排除 Git。

## 开发与验证

```powershell
.\setup.ps1 -Dev
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\ruff.exe check app tests run.py
node --check static/app.js
```

可选网页测试，需要 Node.js 20+：

```powershell
npm ci
npx playwright install chromium
npm run test:ui
```

也可指定本机 Chromium / Edge：

```powershell
$env:MUSE_TEST_BROWSER = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
npm run test:ui
```

网页测试启动独立 fixture 服务 `127.0.0.1:8788`，使用临时数据库。所有注册和邮箱请求均为脚本化本地响应，不会实际发送邮件、创建账号或兑换邀请码。Playwright 只用于测试控制台，不参与注册引擎。

真实代理链测试需要先安装内核；没有内核时三项本地两跳测试会跳过，其他 fixture 测试照常运行。GitHub Actions 安装内核后验证 loopback 链路，不连接实际注册目标。

已验证：102 项后端测试、5 项网页测试，以及展开代理配置后的桌面 / 手机布局。使用真实 Mihomo 和本地认证代理验证 HTTP → SOCKS5、SOCKS5 → HTTP、认证失败不直连及资源清理。Chrome 152 Windows / macOS 经本地受信证书的 HTTPS 服务验证实际请求头；身份持久化、加密、覆盖保护和单次创建行为均有回归检查。另通过本机前置代理完成两次出口采样与只读 Muse 首页 HTTP 200。**尚未使用新邮箱验证真实端到端注册成功**；本地测试不能替代服务端最终接受的证据。

任务结束后可点击“导出脱敏诊断”，查看原生注册请求的字段名、关键 Cookie 名是否存在、响应类型与服务端子码。诊断不包含邮箱、姓名、生日、验证码、Cookie 值或代理密码；Cookie 缓存中的名称不代表实际发送或已获服务端认可。旧任务无法补取当时未记录的信息。

开发检查点和 HAR 协议差异见 [docs/development.md](docs/development.md)。`registrationBlocked` 排查证据及尚未确定的原因见 [docs/registration-blocked-investigation.md](docs/registration-blocked-investigation.md)。
