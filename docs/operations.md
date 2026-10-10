# 运行维护

以下命令在源码目录执行。HTTPS 部署的启动命令始终使用两个 Compose 文件：

```sh
docker compose -f compose.yaml -f compose.https.yaml up -d --build
```

## 恢复 SQLite 备份

1. 先执行 `docker compose exec web python manage.py backup_database`，把备份复制到服务器之外，并保留当前 `.env`。
2. 将要恢复的备份放在当前目录，命名为 `restore.sqlite3`。确认这是正确站点的备份，恢复会替换当前数据。
3. 停止写入，复制备份，验证，再启动：

```sh
docker compose stop web worker
docker compose run --rm --no-deps -v "$PWD/restore.sqlite3:/tmp/restore.sqlite3:ro" web python -c 'import sqlite3, shutil; source=sqlite3.connect("file:/tmp/restore.sqlite3?mode=ro", uri=True); assert source.execute("PRAGMA integrity_check").fetchone()[0] == "ok"; source.close(); shutil.copyfile("/tmp/restore.sqlite3", "/app/data/questionbox.sqlite3")'
docker compose -f compose.yaml -f compose.https.yaml up -d
docker compose exec web python manage.py check
```

不要在 web 或 worker 仍运行时直接复制并覆盖数据库文件。默认使用 SQLite 的回滚日志模式，没有启用 WAL；如以后自行切换 WAL，恢复操作还要正确处理相应日志文件。

## 密码与邮件

忘记主人密码时，在自己的服务器终端运行：

```sh
docker compose exec web python manage.py changepassword 你的用户名
```

不要把密码、数据库、备份或 `.env` 上传到 GitHub。邮件错误仅保存异常类型，不记录 SMTP 异常原文中的邮箱或秘密。可以检查 `worker` 日志和主人收件箱的失败计数，修复后执行 `retry_failed_mail`。后台进程即使关闭邮件功能，也负责定期清理会话和限流计数。

## Server 酱提醒

在生产 `.env` 中设置 `SERVERCHAN_SENDKEY`，重新创建 web / worker 服务；清空此项关闭新提问提醒。后台 worker 与邮件共用，但不依赖 `EMAIL_ENABLED`。使用 `python manage.py test_serverchan` 发送一条配置测试消息，不创建提问。密钥不能提交到 Git 或出现在日志中。

每条成功提交的新提问创建一个提醒任务；失败会延迟重试，最多尝试 5 次。最终失败在主人收件箱显示。检查密钥、接收通道、每日额度后，运行 `python manage.py retry_failed_notifications`，或加 `--job 123` 只重试一个任务。发送异常仅保存异常类型或数字 API 错误码；不会记录带 SendKey 的请求 URL 或供应商原始响应。成功任务保留七天，删除问题会删除相关提醒任务；响应丢失时重试仍可能造成重复通知。

## 存储边界

500 条上限限制应用中的问题数量，SQL 备份、代理日志和数据库空闲页仍会占用空间。定期检查磁盘使用情况、删除不需要的旧备份。需要收缩数据库文件时，先备份并停 web / worker，再执行：

```sh
docker compose run --rm --no-deps web python -c 'import sqlite3; db=sqlite3.connect("/app/data/questionbox.sqlite3"); db.execute("VACUUM"); db.close()'
docker compose -f compose.yaml -f compose.https.yaml up -d
```

清理或恢复都应先留一份可用的异地备份。不会提供批量删除访客问题的自动任务，以免误删你准备回答的来信。
