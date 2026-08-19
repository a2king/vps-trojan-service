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

1. 安装 nginx、uv、Node、Trojan、证书
2. `npm run build` 打包前端
3. 把 `/usr/share/nginx/html` 软连接到 `frontend/dist`
4. 写入 nginx：`/`、`/admin` 走前端，`/api/` 反代到后端
5. 用 systemd 后台启动 `trojan-admin` 和 `trojan`

装完后：

- 伪装首页：`https://域名/`
- 管理后台：`https://域名/admin`

在后台创建用户后，把详情弹窗里的 `trojan://` 链接导入客户端即可。

如果 GitHub 下载 Trojan 很慢，可先导出代理再安装：

```bash
export GITHUB_PROXY=https://ghfast.top/
sudo -E bash install.sh
```

脚本菜单还提供卸载和证书修复。

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

## 目录

```
install.sh          VPS 一键安装
deploy/             nginx / trojan / systemd / logrotate 模板
backend/            FastAPI + uv + SQLite
frontend/           React 伪装站 + 管理台
```
