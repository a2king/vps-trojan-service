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
    client_max_body_size 8m;
    server_tokens off;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;

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

    # Trojan 把普通 HTTPS 回落到这里。节点机不跑管理后台。
    server {
        listen 127.0.0.1:80;
        server_name __DOMAIN__;
        root /var/www/node-html;
        index index.html;

        location /api/ { return 404; }
        location /admin { return 404; }

        location / {
            try_files $uri $uri/ /index.html;
        }
    }
}
