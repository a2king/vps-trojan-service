user __NGINX_USER__;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile on;
    keepalive_timeout 120;
    client_max_body_size 20m;
    server_tokens off;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;

    # 公网 80：ACME 校验 + 跳转到 HTTPS（443 由 Trojan 接管）
    server {
        listen 80;
        listen [::]:80;
        server_name __DOMAIN__;

        location /.well-known/acme-challenge/ {
            root /var/www/acme;
        }

        location / {
            return 301 https://$host$request_uri;
        }
    }

    # Trojan 把普通 HTTPS 回落到这里。
    # 公开首页用伪装站；/admin 与 /assets 仍走管理前端。
    server {
        listen 127.0.0.1:80;
        server_name __DOMAIN__;
        root /var/www/camouflage;
        index index.html;

        # 后端 FastAPI：管理接口 + Clash 订阅
        location /api/ {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
        }

        location /config/ {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
        }

        location /admin {
            root /usr/share/nginx/html;
            try_files /index.html =404;
        }

        location /assets/ {
            root /usr/share/nginx/html;
            try_files $uri =404;
        }

        location / {
            try_files $uri $uri/ /index.html;
        }
    }
}
