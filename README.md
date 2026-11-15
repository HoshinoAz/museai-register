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
- 注册会话固定使用同一代理、Cookie jar 与 waterfall ID；支持 HTTP / HTTPS / SOCKS5 代理。
- 可选邀请码兑换：会话验证后，先唤醒 VM 并初始化 sharing，再提交兑换。
- 显示任务阶段、脱敏 HTTP 状态、候补状态及失败原因；支持停止、搜索和筛选。
- 账号凭据加密落盘，支持单账号和全部账号的 JSON 导出。

姓名字段明确区分姓 `last_name` 与名 `first_name`，生日必须有效且年满 18 周岁。勾选确认后，程序会提交服务端动态返回的条款版本与披露正文；不会用 HAR 中的旧值重放。

### 邮箱 API

每个邮箱提供一个专属接码 URL，GET 返回：

```json
[
  {"otp": "123456", "time": "2026-11-16T00:00:30Z"}
]
```

`time` 必须包含时区；程序只使用发送请求开始之后的最新 6 位验证码。同一时间出现不同验证码视为歧义，同一任务不会重复提交已使用的验证码。邮箱 API 走独立直连 Session，不附带 Muse Cookie，也不经过注册代理。

自动轮询间隔约 2 秒；默认总等待 180 秒，可调 30 至 600 秒。支持手动重发，至少间隔 45 秒，最多两次，重发不延长总等待时间。

批量清单每行支持两种格式：

```text
one@example.com----https://mail.example.com/otp/one
two@example.com
```

第二行将等待手动验证码。可另外粘贴代理池，每行一个完整 URL，按邮箱顺序循环分配。任何任务出现异常会停止本批尚未结束的任务，不补邮箱、不自动重复创建；已经发出的请求需等待返回。

## 完成条件与中断

流程为首页 → restart / CSRF → send-otp → confirm-otp → register-completion → Meta bootstrap → 动态 disclosure → accept-tos → Hatch 访问验证 → 可选邀请码。

| 显示 | 含义 |
| --- | --- |
| 已创建 | register-completion 返回有效 `auth_meta_session` |
| 会话已验证 | 动态披露已获 `success=true`；GET `/api/hatch/invite` 返回有效非失败 JSON，且存在 `hatch_sess` |
| 已创建 · 待确认 | 已有账号，但后续引导、地区、条款、访问或邀请码步骤未完成 |
| `region_waitlist` | 已创建但服务端判定地区候补，未证明已取得 Hatch 权限 |
| `creation_uncertain` | 创建请求已发出但响应未知，或创建时服务重启；该邮箱禁止再次提交 |
| 服务已中断 | 重启前的活动任务已中断，不会自动重新注册 |

邀请码返回 `invalid`、`used_up` 或 `already_redeemed` 会单独记录，已经验证的会话不会因此变成“未注册”。`accept-tos` 和账号创建均不自动重试。

“会话已验证”严格指上表的接口检查，不声称原生输入行为一致、所有功能可用，或获得了未检查的其他权限。导出中的 `frl_access_token` 是 **Meta FRL 凭据**，不是 Muse 原生 access token；字段与 `credential_type` 均明确区分。

## 数据

运行数据只写入本项目 `data/`：

```text
data/
  muse.sqlite3    任务、脱敏事件、加密的账号凭据
  secret.key     本地加密密钥
```

历史状态重启后保留；HTTP Session 本身不恢复。结果包含 `registered`、`hatch_access_verified` 等独立标记；有 token 或 Cookie 不会单独把任务标记为验证成功。

密钥与数据库需一起备份。该加密不抵御同时读取密钥和数据库的本机访问者。导出的 JSON 包含明文凭据，应单独保管。代理密码、OTP URL 和验证码不保存到任务历史；日志不记录 token、Cookie、完整错误响应或 URL 查询参数。`data/`、HAR、日志、环境文件与虚拟环境均已排除 Git。

## 开发与验证

```powershell
.\setup.ps1 -Dev
.\.venv\Scripts\python.exe -m pytest tests -q
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

已验证：24 项后端测试、3 项网页测试，以及桌面 / 手机布局。另做了一次只读真实首页 GET，得到 HTTP 200。**尚未使用新邮箱验证真实端到端注册成功**；本地测试不能替代服务端最终接受的证据。

开发检查点和 HAR 协议差异见 [docs/development.md](docs/development.md)。
