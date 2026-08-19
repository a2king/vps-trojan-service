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

    server {
        listen 80;
        listen [::]:80;
        server_name __DOMAIN__;
        root /var/www/acme;

        location /.well-known/acme-challenge/ {
            root /var/www/acme;
        }

        location / {
            return 200 'waiting for certificate\n';
            add_header Content-Type text/plain;
        }
    }
}
