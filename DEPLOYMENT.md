# Admin Service Deployment Guide

This guide outlines the production-ready deployment strategy for the Admin Service on a DigitalOcean Droplet using Nginx, HTTPS via Certbot, and `ufw` firewall rules.

---

## Security Requirements & Firewalls (UFW)

> [!WARNING]
> **Strict Port Isolation:** The Uvicorn app processes execute on port `8000` bound to the local loopback interface `127.0.0.1`.
> **You must configure UFW to drop external incoming traffic to port 8000.** Let external clients access port 8000 directly bypasses the reverse proxy, leaving endpoints exposed. Only ports 80 (HTTP) and 443 (HTTPS) must be allowed externally.

---

## Environment Variables

Create a `.env` file containing the following environment variables. Ensure that `ADMIN_JWT_SECRET` is a secure 32-byte hexadecimal string shared with the Admin Dashboard.

```env
# Your MongoDB Atlas Connection String
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority

# JWT HS256 Shared Secret for secure Server-to-Server Admin Dashboard communication (minimum 32 characters)
ADMIN_JWT_SECRET=your_32_byte_hex_secret_here

# Optional: JWT Secret for signing local tokens (minimum 32 characters)
JWT_SECRET=your_fallback_32_byte_jwt_secret_here

# Comma-separated list of allowed CORS origins (e.g., frontend dashboard URL)
CORS_ORIGINS=https://admin.yourdomain.com
```

---

## Production Server Setup

Execute these commands as `root` before switching to the service user. This will install system dependencies, create a dedicated service user, and configure SSH access:

```bash
# Install fish and neovim
apt update && apt install -y fish neovim

# Create dedicated service user and configure fish shell
useradd -m -s /usr/bin/fish admin-service
chsh -s /usr/bin/fish admin-service
passwd admin-service
usermod -aG sudo admin-service

# Configure SSH key by copying from root
mkdir -p /home/admin-service/.ssh
cp /root/.ssh/authorized_keys /home/admin-service/.ssh/authorized_keys
chown -R admin-service:admin-service /home/admin-service/.ssh
chmod 700 /home/admin-service/.ssh
chmod 600 /home/admin-service/.ssh/authorized_keys
```

---

## Swap Memory Allocation

To ensure system stability during heavy processes:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

---

## Service Installation

SSH into the newly created `admin-service` user:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git nginx python3-certbot-nginx ufw python3.12-venv
python3 -m venv venv
source venv/bin/activate.fish

# Clone repository and install dependencies
pip install -r requirements.txt
chmod 600 .env
```

---

## Fish Shell Configuration

To streamline the environment, add the following to `~/.config/fish/config.fish`:

```fish
set -g fish_greeting
set -gx ENV_PATH "/home/admin-service/Admin_Service/.env"
set -gx TERM xterm-256color
```

---

## Nginx & HTTPS Configuration

First, ensure your Hostinger DNS points the desired subdomain (e.g., `admin.yourdomain.com`) to your DigitalOcean IP address.

Define the custom log format in `/etc/nginx/nginx.conf` (inside the `http { ... }` block):

```nginx
    log_format domain_access '$remote_addr - $remote_user [$time_local] '
                             '"$request" $status $body_bytes_sent '
                             '"$http_referer" "$http_user_agent" '
                             'host="$host"';
```

Configure Nginx (`sudo nvim /etc/nginx/sites-available/Admin-Service`):

```nginx
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

server {
    server_name admin.yourdomain.com;

    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    client_max_body_size 50M;

    location / {
        limit_req zone=api_limit burst=20 nodelay;

        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # Certbot will inject SSL configurations here automatically
    access_log /var/log/nginx/Admin-Service.access.log domain_access;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_reject_handshake on;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444; # Instantly drop unencrypted traffic
}
```

Enable the site, configure the firewall, and obtain the SSL certificate:

```bash
sudo ln -sf /etc/nginx/sites-available/Admin-Service /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

# Allow OpenSSH and deny external port 8000 access
sudo ufw allow OpenSSH
sudo ufw deny 8000/tcp

# Temporarily allow 'Nginx Full' for Certbot validation
sudo ufw allow 'Nginx Full'

# Enable firewall
sudo ufw --force enable

# Obtain SSL Certificate
sudo certbot --nginx -d admin.yourdomain.com

# Revert firewall to HTTPS only by allowing 'Nginx HTTPS' and deleting 'Nginx Full'
sudo ufw allow 'Nginx HTTPS'
sudo ufw delete allow 'Nginx Full'
sudo ufw status verbose

# Restart Nginx
sudo systemctl restart nginx
sudo systemctl enable nginx.service
```

---

## Firewall Setup

The permanent firewall rules ensure that only SSH and HTTPS are allowed externally, while port `8000` is blocked.

Verify the configuration:

```bash
sudo ufw status verbose
```

To run the application persistently, refer to the provided `systemd/Admin_Service.service` template.

