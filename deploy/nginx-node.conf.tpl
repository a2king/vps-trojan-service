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

    # Trojan 把普通 HTTPS 回落到这里。备机同时提供 /admin 与订阅。
    server {
        listen 127.0.0.1:80;
        server_name __DOMAIN__;
        root /var/www/node-html;
        index index.html;

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
