# Shadow Platform 首次生产部署手册

本手册对应本地/NAS 媒体存储、PostgreSQL、Redis、Nginx 和 systemd。执行任何线上变更前先备份现有 Nginx、PostgreSQL 和 Authelia 配置。

## 1. 安装与目录

```bash
cd /opt/shadow-platform
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[postgres]'

groupadd --system shadow-platform
useradd --system --gid shadow-platform --home-dir /nonexistent --shell /usr/sbin/nologin shadow-platform
useradd --system --gid shadow-platform --home-dir /nonexistent --shell /usr/sbin/nologin shadow-media
install -d -o root -g shadow-platform -m 0750 /etc/shadow-platform/secrets
install -d -o shadow-media -g shadow-platform -m 0750 /var/lib/shadow-media/objects
install -d -o shadow-platform -g shadow-platform -m 0750 /var/lib/shadow-telemetry
```

若用户或组已经存在，跳过对应的 `groupadd` / `useradd`。应用自己的 outbox flush 模板以 systemd 实例名作为 Linux 用户名，例如 `shadow-travel`，部署前也必须创建该最小权限用户。

创建独立 PostgreSQL 数据库和最小权限用户：`shadow_media`、`shadow_telemetry`。把完整 SQLAlchemy URL 分别写入 `root:shadow-platform 0640` 的：

```text
/etc/shadow-platform/secrets/media-database-url
/etc/shadow-platform/secrets/telemetry-database-url
```

上述平台服务秘密文件设为 `root:shadow-platform 0640`。应用原始服务 Token 目录只授权给对应应用用户，不能让其他业务项目读取。

## 2. 服务凭据和签名密钥

首次为每个接入项目生成服务 Token。中央只保存摘要，明文只写到对应应用能读取的文件：

```bash
.venv/bin/python scripts/generate_service_token.py \
  --registry /etc/shadow-platform/secrets/service-token-hashes.json \
  --app travel \
  --token-output /etc/shadow-platform/secrets/apps/shadow-travel/service-token

chown root:shadow-platform /etc/shadow-platform/secrets/service-token-hashes.json
chmod 0640 /etc/shadow-platform/secrets/service-token-hashes.json
chown -R root:shadow-travel /etc/shadow-platform/secrets/apps/shadow-travel
chmod 0750 /etc/shadow-platform/secrets/apps/shadow-travel
chmod 0640 /etc/shadow-platform/secrets/apps/shadow-travel/service-token
```

轮换时再次执行同一命令；registry 会同时保留新旧两份摘要。所有实例切换后移除旧摘要：

```bash
.venv/bin/python scripts/generate_service_token.py \
  --registry /etc/shadow-platform/secrets/service-token-hashes.json \
  --app travel --retire-previous
```

另生成至少 32 字节的媒体访问签名密钥并写入 `/etc/shadow-platform/secrets/media-access-signing-key`。平台服务秘密使用 `root:shadow-platform 0640`；每个应用的原始 Token 只对该应用组可读。

## 3. 配置和自检

从 `deploy/env/` 复制 `media.env.example`、`telemetry.env.example` 到 `/etc/shadow-platform/`，确认路径、域名和数据库文件正确。再把四个 example registry 复制为实际文件：

```text
auth/configuration.yml
auth/users_database.yml
llm/registry.yml
agents/registry.yml
```

执行严格自检：

```bash
.venv/bin/python scripts/platform_doctor.py --strict
```

严格模式会拒绝缺失配置、`REPLACE_WITH` 占位符、Catalog 的无效引用以及缺少的 OIDC 客户端。

Authelia 固定使用 `4.39.20`。裸机部署复制
`deploy/systemd/authelia.service.example` 为 `/etc/systemd/system/authelia.service`，并在
`/etc/authelia/authelia.env` 中仅配置以下文件型密钥：

```text
AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE
AUTHELIA_SESSION_SECRET_FILE
AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE
AUTHELIA_STORAGE_POSTGRES_PASSWORD_FILE
```

合并 OIDC 客户端、JWKS 私钥和密钥文件后，必须使用同版本二进制或容器验证：

```bash
set -a && . /etc/authelia/authelia.env && set +a
runuser -u authelia -- authelia config validate --config /etc/authelia/configuration.yml

# 没有安装裸机二进制时可用同版本容器：
docker run --rm -v /etc/authelia:/config authelia/authelia:4.39.20 \
  authelia config validate --config /config/configuration.yml
```

首次账号不要直接授予全平台管理员组。按已接入项目从最小组开始，例如 Health 首个账号
只加入 `health-users`；需要管理能力时再单独增加 `shadow-admins`。一次性初始密码应只保存
在服务器 root 可读文件（当前约定为 `/root/shadow-identity-initial-password`，权限
`0600`），首次登录后立即修改，不写入仓库或运维记录。

## 4. systemd 和 Nginx

复制并调整 `deploy/systemd/` 下的 Media、Telemetry、清理和 outbox flush 单元，然后：

```bash
systemctl daemon-reload
systemctl enable --now shadow-media shadow-telemetry
systemctl enable --now shadow-media-cleanup.timer shadow-telemetry-cleanup.timer
systemctl enable --now shadow-llm-usage-flush@shadow-travel.timer
```

应用的 LLM 客户端写 `/var/lib/<service>/llm-usage.jsonl`。对应 timer 使用 `deploy/env/llm-telemetry-app.env.example`，每分钟把元数据发送到 `https://media.cylunex.top/platform/telemetry/`。

将 `deploy/nginx/media.cylunex.top.conf.example` 合并到线上配置，执行：

```bash
nginx -t
systemctl reload nginx
```

Identity 与业务域名的 80 端口必须为 `/.well-known/acme-challenge/` 保留 Webroot，其他
路径再跳转 HTTPS。首次签发后执行一次无随机等待的续期演练：

```bash
certbot renew --cert-name shadow-services --dry-run --no-random-sleep-on-renew
```

## 5. 验收

```bash
curl -fsS http://127.0.0.1:8400/healthz
curl -fsS http://127.0.0.1:8400/readyz
curl -fsS http://127.0.0.1:8410/healthz
curl -fsS http://127.0.0.1:8410/readyz
systemctl list-timers 'shadow-*'
```

再用每个项目的服务 Token 完成一次图片上传、私密访问和一条 LLM metadata 上报，确认跨项目访问返回 404/403。检查 Nginx 日志没有 query token，collector 数据库没有 prompt/response 字段。

## 6. 备份与升级

- 每日备份 Authelia、Media、Telemetry 三个 PostgreSQL 数据库；
- 备份 Authelia OIDC 私钥、storage encryption key 和媒体签名密钥；
- 本地/NAS 对象目录做快照或异机增量备份；
- 升级前运行全量测试和 `platform_doctor.py --strict`；
- Authelia、PostgreSQL 和 Python 依赖锁定版本后再升级，不使用浮动 `latest`。
