[Unit]
Description=Trojan admin API
After=network.target

[Service]
Type=simple
WorkingDirectory=__INSTALL_DIR__/backend
EnvironmentFile=__INSTALL_DIR__/.env
Environment=TZ=Asia/Shanghai
ExecStart=__INSTALL_DIR__/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=2s
StandardOutput=append:/var/log/trojan-admin.log
StandardError=append:/var/log/trojan-admin.log

[Install]
WantedBy=multi-user.target
