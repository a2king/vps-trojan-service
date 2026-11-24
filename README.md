# Trojan 个性化服务

自用的 Trojan 代理节点：保留 `nginx + acme.sh + trojan` 的伪装架构，并加上多用户管理后台（React + FastAPI）。数据库只用 **SQLite**，不再安装 MariaDB。

## 和原来 `trojan_mult.sh` 的关系

原脚本会做这些事：

1. 安装 nginx，先在 80 端口放一个伪装站
2. 用 acme.sh 给绑定域名申请证书
3. 把 nginx 改成：公网 80 跳 HTTPS；`127.0.0.1:80` 继续提供网页
4. Trojan 监听 443，识别出 Trojan 协议就代理，否则把普通 HTTPS 流量转给本地 nginx
5. 只支持配置文件里的**一个密码**，没有用户管理和流量统计

这套项目沿用同一条链路，额外做了：

- 一键 `install.sh`：装环境、打前端包、把 `frontend/dist` 软链到 nginx html 目录、写前后端反向代理、以后台服务拉起管理 API
- 管理后台：`https://你的域名/admin`
- 公开伪装首页仍在 `/`，不需要登录
- 多用户的创建 / 禁用 / 删除
- 按天、按月统计每个用户的进流量和出流量，以及最近使用时间
- 日志与日流量最多保留约 30 天

## 架构

```
客户端 / 浏览器
    │
    ├─ Trojan 协议 ──► :443 trojan-gfw ──► 出站代理
    │                      │
    │                      └─ MySQL 协议 127.0.0.1:13306
    │                         （管理后端进程内兼容层，数据在 SQLite）
    │
    └─ 普通 HTTPS ─────────┴──► 127.0.0.1:80 nginx
                                 html → /usr/share/nginx/html
                                        └── 软链到 frontend/dist
                                 ├─ /          伪装首页
                                 ├─ /admin     管理后台
                                 └─ /api       FastAPI 127.0.0.1:8000
```

Trojan 原生只认 MySQL 用户表。这里不跑数据库服务，而是让管理后端在 `127.0.0.1:13306` 模拟 MySQL 协议，读写同一个 SQLite 文件。

禁用用户：把 `quota` 设为 `0`。删除用户：同时删管理记录和 Trojan 用户。

## VPS 安装

系统：Ubuntu 20.04+ / Debian 11+ / CentOS 7+（x86_64）。需要一个已经解析到 VPS 的域名，并且 **80、443 未被占用**。

```bash
git clone <本仓库> /opt/vps-trojan-service
cd /opt/vps-trojan-service
sudo bash install.sh
```

安装过程会：

1. 安装 nginx、uv、Node、Trojan（使用仓库 `vendor/trojan` 预置包）、证书
2. `npm run build` 打包前端
3. 把 `/usr/share/nginx/html` 软连接到 `frontend/dist`
4. 写入 nginx：`/`、`/admin` 走前端，`/api/` 反代到后端
5. 用 systemd 后台启动 `trojan-admin` 和 `trojan`

装完后：

- 伪装首页：`https://域名/`
- 管理后台：`https://域名/admin`

在后台创建用户后，把详情弹窗里的 `trojan://` 链接导入客户端即可。

Trojan 二进制已预置在 `vendor/trojan/`，安装时不再访问 GitHub。脚本菜单还提供卸载和证书修复。

## 本地开发

```bash
cd backend
copy ..\.env.example .env
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

cd ../frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173/` 看伪装首页，`/admin` 进管理台。Vite 会把 `/api` 代理到后端。

## 主控主备（文件同步 HA）

不引入网络数据库。后台「添加节点」会把那台机器装成**完整备机**（本机 Trojan + 管理后台），不再只靠主节点反向隧道做认证。

- 装完后打开 `https://子节点域名/admin`，账号与主节点相同
- 子节点代理查自己的 `127.0.0.1:13306`，用户、订阅、隧道配置来自主节点定期推送的库（默认 6 小时，安装结束会先推一次）
- 页面添加或安装备机成功后，会把该节点补进每个账号里已经有 VPS 绑定的策略组（没绑过节点的组不改）
- 改用户节点 / 隧道绑定后会立刻再推一次在线备机；主库提交成功不受推送失败影响，失败写入该节点 `last_error`
- 主节点挂了之后，子节点代理和 `/admin` 仍可用，数据停在最后一次同步
- 主节点在线时，同步以主节点库为准，会覆盖这段时间在备机后台里做的修改

已经用旧方式装过的子节点，在节点页对该机器再点一次重新安装，才会变成备机。旧节点在重装前仍走反向隧道。

另外仍可用管控域名 DNS 在两台**都跑过完整 install.sh** 的机器之间切换主备（`control-dns-watch`）。页面添加的备机不走这条隧道。

约束：

- 同一时刻只有一台 `CONTROL_ROLE=primary`（会拉子节点反向隧道）
- 备控 `CONTROL_ROLE=standby` 负责记录本机流量，由主控定时拉取流量和认证日志。
- 最多丢失一个同步周期内的写入（默认每 6 小时）
- 主备 `.env` 里的 `DOMAIN` 建议与管控/订阅域名一致
- DNS 切换后最多约 5 分钟（再加解析 TTL）才会角色对齐

### 一次性配置

1. 备机同样执行 `install.sh`，装完后把 `.env` 改为 `CONTROL_ROLE=standby`，并 `systemctl restart trojan-admin`（或先停掉，只留同步目录待命）
2. 保证主控能 SSH 到备机。后台为每个节点登记独立公钥，主机指纹会在首次连接时留存。手工同步可以在 `ha.env` 中配置 `STANDBY_SSH_KEY` 和 `STANDBY_KNOWN_HOSTS`。
3. **主备都配置** `/etc/trojan-admin/ha.env`：至少填 `CONTROL_LOCAL_IP=本机公网IP`；主控再填 `STANDBY_HOST`
4. 主控启用同步 timer；**主备都启用** DNS 监听 timer：

```bash
sudo mkdir -p /etc/trojan-admin
sudo cp deploy/ha.env.example /etc/trojan-admin/ha.env
sudo chmod 600 /etc/trojan-admin/ha.env
# 编辑 CONTROL_LOCAL_IP / STANDBY_HOST / INSTALL_DIR 等
sudo chmod +x deploy/control-*.sh deploy/control-common.sh
sudo cp deploy/trojan-control-sync.service deploy/trojan-control-sync.timer \
  deploy/trojan-control-dns-watch.service deploy/trojan-control-dns-watch.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
# 仅主控需要 sync timer；DNS watch 主备都开
sudo systemctl enable --now trojan-control-sync.timer          # 主控
sudo systemctl enable --now trojan-control-dns-watch.timer     # 主备
sudo systemctl start trojan-control-sync.service               # 立刻试同步
sudo systemctl start trojan-control-dns-watch.service          # 立刻试 DNS 对齐
```

同步快照默认落在备机的 `/var/lib/trojan-admin/ha-incoming/trojan.db`。管理 SSH 私钥留在持有方的本机目录。

完整备控接收独立冷备快照。主控在写库或配置停止变更约 10 分钟后广播一次，每天北京时间 03:00 再广播一次。备控的 `/opt/data/trojan-backup/` 保留最近 14 份，包含 SQLite 备份、安装根 `.env`、可选 `ha.env` 和恢复说明。恢复管理访问时重新登记节点公钥，或从独立保管的凭据恢复。可以执行 `deploy/control-cold-backup.sh --local-only` 生成本地冷备，或传 `--host` 推送到指定备控。

### 故障切换（推荐：只改 DNS）

计划使用 DNS 接管时，先在节点页给目标备机执行「准备接管密钥」，让它在本机生成密钥并把公钥登记到其他节点。「设为主节点」会在切换前执行同样的准备。SSH 地址或节点清单变更后可再次执行准备。

1. 在域名网站把管控域名 A 记录改成备机公网 IP（TTL 建议 60–300s）
2. 等待最多约 5 分钟：备机发现解析指向自己 → 自动 `promote`；旧主发现解析已离开 → 自动 `demote`
3. 抽查：`/api/health`（应含 `"control_role":"primary"`）、订阅、任选子节点新连 Trojan

仍可手动：`control-promote.sh` / `control-promote.sh --demote`。

### 回切

把域名 A 记录改回原主控 IP 即可，双方 DNS watch 会自动交换角色。若原主长时间离线，先确认它已收到较新 sync（或接受数据回退），再改 DNS。

相关脚本：`control-backup.sh`、`control-sync.sh`、`control-promote.sh`、`control-dns-watch.sh`、`ha.env.example`。

## 升级采集与节点观测

1. 先升级主控，再在节点页重新部署各完整备机。旧节点升级前，节点页可能提示新采集接口不可用。
2. 各节点在本机 `backend/data/node-traffic.db` 按天累计流量。主控定期拉取已计数数据，重复拉取同一天的累计值只入账一次。已有流量汇总继续计入全网总量，节点筛选从启用独立采集开始。
3. 节点页展示最近成功读取流量、认证日志和应用权限的时间。日志可在安装、Trojan 和管理后台之间切换，管理审计记录账号、规则、节点和隧道的写操作。
4. 已有 SSH 授权通过旧凭据升级。成功验证专用密钥后，后台移除目标上的旧共用授权。首次连接需核对远端主机身份，重装导致主机指纹变更时先核对再更新本机 `known_hosts`。重新部署完整备机时会轮换该节点的管理会话签名密钥，已有会话需要重新登录。

## 目录

```
install.sh          VPS 一键安装
deploy/             nginx / trojan / systemd / HA 同步与 logrotate 模板
backend/            FastAPI + uv + SQLite
frontend/           React 伪装站 + 管理台
```
