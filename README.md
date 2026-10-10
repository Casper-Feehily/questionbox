# Questionbox · 好奇心俱乐部

可以自己部署的网页版匿名提问箱。访客投递文字，主人在私人收件箱中保存草稿、回答并发布；**回答发布之前，问题不会公开。** 界面采用便签与贴纸风格，支持明亮、黑暗和跟随系统的主题。

这是独立的网页版源码，包含 Django 后端、网页界面、测试和通用部署配置，不包含微信小程序或其专用接口，也不包含运行数据、账号或真实密钥。使用 [GNU General Public License v3许可](LICENSE)。

## 功能

- 无需访客账号，支持匿名或自选署名；署名未经身份验证。
- 每条提问最多 500 个 Unicode 字符，全站最多保存 500 条问题，含私人问题；删除释放名额。
- 主人登录、私人收件箱、草稿、发布、编辑、删除与分页；旧编辑页面不能覆盖更新后的答案。
- 沉浸式回信阅读器，支持触屏滑动、鼠标拖动、横向滚动和方向键。
- IP 和签名 Cookie 限流：默认每位访客每分钟 1 条、每小时 10 条；每 IP 每小时 30 条、全站每小时 300 条。
- 可选 SMTP / Microsoft Graph 邮箱通知，填写邮箱后自动订阅，支持退订、队列、失败重试。
- 可选 Server 酱和 Bark 主人提醒；提醒仅提供需登录的查看链接，不含提问原文。
- Django 5.2、SQLite、WhiteNoise、后台 worker 和 Docker Compose，无 Redis 依赖。

用户输入按普通文字显示，不渲染 HTML、Markdown、图片或链接预览。公开内容由主人手动选择，没有接入自动审核。

## 本地运行

需要 Python 3.12。首次使用没有账号、问题或共享默认密码。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

访问 <http://127.0.0.1:8000>，主人入口为 `/owner/`。创建一个 superuser 即可；密码至少 12 个字符，用户名和公开显示名称是独立设置。默认开发模式会在 `data/` 生成本地签名密钥和数据库。

另开终端启动通知与清理任务：

```sh
.venv/bin/python manage.py mail_worker
```

开发时可在本地 `.env` 中设置 `OWNER_NAME`、`SITE_TITLE` 和 `DEBUG=1`。配置示例在 [.env.example](.env.example)。其中 `DATA_DIR=/app/data` 是 Docker 路径；本地请省略该项，或填写自己有权限的绝对路径。没有启用邮件或提醒时不会联系对应服务。

## Docker Compose + HTTPS 部署

安装 Docker Compose，准备域名，将 DNS 指向服务器并开放 80 / 443 端口。

```sh
python3 scripts/configure.py --domain ask.example.com --owner 站长
docker compose -f compose.yaml -f compose.https.yaml up -d --build
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py check --deploy
```

将 `ask.example.com` 替换为自己的域名。配置脚本生成随机 `SECRET_KEY`，不覆盖已有 `.env`，也不创建账号；Caddy 负责 HTTPS。应用与 worker 共用持久化数据卷。

已有 HTTPS 网关时，可使用 `compose.yaml` 并配置自己的代理。应用端口默认仅绑定服务器回环地址；生产 `DEBUG=0` 且 `SITE_URL` 必须使用 HTTPS，`TRUSTED_PROXY_CIDRS` 只填写真实可信代理。示例 Caddy 使用 `172.31.250.2/32`；若修改网络，需要同时调整 Compose 和可信代理配置。

`check --deploy` 可能提示 HSTS 子域名和 preload 未开启，这是留给部署者的选择。升级前先备份，再重新执行 `up -d --build`。停止使用 `down`；**不要加 `-v`，它会删除数据卷。**

本版本只保留通用 Compose 部署，没有绑定特定服务器的 SSH 发布脚本。

## 配置

| 配置 | 默认或用途 |
| --- | --- |
| `OWNER_NAME` | `站长`；公开显示名称和箱满联系人 |
| `SITE_TITLE` | `站长的匿名提问箱` |
| `DEBUG` | 本地默认 `1`，生产必须 `0` |
| `SECRET_KEY` | 生产至少 50 字符的随机值；配置脚本生成 |
| `SITE_URL` | 生产 HTTPS 源站，用于邮件和通知链接 |
| `ALLOWED_HOSTS` | 允许的域名，逗号分隔，不含协议或路径 |
| `DATA_DIR` | 本地默认 `data/`，Docker 使用 `/app/data` |
| `TRUSTED_PROXY_CIDRS` | 精确的可信代理地址或网段；直接访问时留空 |
| `QUESTION_RATE_LIMIT_ENABLED` | `1`；`0` 关闭提问时间配额，保留长度和总数上限 |
| `RATE_COOKIE_MINUTE` / `RATE_COOKIE_HOUR` | `1` / `10` |
| `RATE_IP_HOUR` / `RATE_SITE_HOUR` | `30` / `300` |
| `EMAIL_ENABLED` | `0`；配置邮件后启用 |
| `SERVERCHAN_SENDKEY` | 留空关闭 Server 酱 |
| `BARK_DEVICE_KEY` | 留空关闭 Bark |
| `BARK_SERVER_URL` | 默认 `https://api.day.app`，支持自建服务 |

公开页面只展示已回答并发布的内容。主人登录限制每 IP 每 15 分钟 5 次尝试。签名 Cookie 为随机标识，生产使用 Secure、HttpOnly、SameSite；共享网络可能共享 IP 配额。

500 条限制不是磁盘配额，日志、队列、会话、数据库空闲页和备份仍会占空间。后台清理过期会话、防刷计数及已完成通知任务；答案和草稿各最多 10,000 字。

## 邮箱通知

SMTP 示例，填写自己的配置到私有 `.env`：

```dotenv
EMAIL_ENABLED=1
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-account
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=Questionbox <box@example.com>
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
```

隐式 TLS 按供应商要求改成 `EMAIL_USE_TLS=0`、`EMAIL_USE_SSL=1`，并设置对应端口。

Outlook / Hotmail 或 Microsoft 365 可使用 Microsoft Graph：

```dotenv
EMAIL_ENABLED=1
EMAIL_BACKEND=box.graph_backend.EmailBackend
MS_GRAPH_CLIENT_ID=your-client-id
MS_GRAPH_TENANT_ID=your-tenant-id
MS_GRAPH_CLIENT_SECRET=your-client-secret
MS_GRAPH_ACCOUNT_TYPE=personal
```

个人邮箱用 `personal`，组织邮箱用 `organization`。应用需支持实际发件账号类型，配置 Graph 的 `Mail.Send` 委托权限和 `offline_access`，并添加 Web 回调 `https://你的域名/owner/mail/callback/`。重新创建 web / worker 后，在主人后台“邮件通知”中连接邮箱并测试。未连接或授权失效时，Graph 模式的访客邮箱入口关闭。

客户端密码可以通过 `python3 scripts/set_outlook_secret.py /path/to/.env` 在交互式终端隐藏输入。该工具会创建敏感配置备份，应保持私有。OAuth 令牌加密保存在 `DATA_DIR/outlook-tokens.enc`；SQLite 备份不包含它。更换签名密钥、应用 ID 或账号类型后需重新授权。

提交时填写邮箱即自动订阅，收到订阅成功邮件；首次发布和后续更新发送通知，草稿不发邮件。邮件含退订链接，不含提问原文。失败不阻止提交或发布，队列最多自动尝试 5 次，可运行 `retry_failed_mail` 重排失败任务。供应商已接收但响应丢失时，重试可能导致重复通知。

## 主人提醒

在 `.env` 配置 `SERVERCHAN_SENDKEY` 或 `BARK_DEVICE_KEY`，重新创建 web / worker。Bark 自建地址只填服务源站，不包含设备 Key 或 `/push`。这些提醒不依赖访客邮件开关，但需要运行 worker。

```sh
docker compose -f compose.yaml -f compose.https.yaml up -d --force-recreate
# 以下命令会发送真实配置测试通知：
docker compose exec web python manage.py test_serverchan
docker compose exec web python manage.py test_bark
```

留空密钥即可关闭对应提醒。失败任务可以用 `retry_failed_notifications` 或 `retry_failed_bark` 重排；供应商配额以其说明为准。

## 备份、维护与开发

```sh
docker compose exec web python manage.py backup_database
```

备份包含私人问题、邮箱和账号信息，必须保持私有。详见 [运行维护](docs/operations.md)。

```sh
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test box --noinput
.venv/bin/pip check
```

测试使用独立数据库，外部通知服务在测试中模拟；测试密码和 token 是模拟值。本版采用网页版迁移链，适合全新安装，不能直接用于升级包含其他客户端功能的既有数据库。

主要入口：`box/views.py` 负责网页与主人操作，`box/submission.py` 负责投递事务，`box/rate_limit.py` 负责限流，`box/mail.py` 负责邮件队列，`templates/` 和 `static/` 负责界面。

发布前请阅读 [开源文件边界](docs/open-source.md)。`.gitignore` 不能阻止手工上传被忽略的文件，也不能删除已提交历史中的秘密。
