#!/bin/bash
# 
# DUK Bus Tracker — Ubuntu Production Deployment Script
# 
# Usage:
#   chmod +x deploy.sh
#   sudo ./deploy.sh
#
set -e

echo "==============================================="
echo "   DUK Bus Tracker — Production Deployment"
echo "==============================================="
echo ""

# Ensure run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo ./deploy.sh)"
  exit 1
fi

# ── 1. User Prompts ─────────────────────────────────────────────────────────
echo "--- Port Configuration ---"
read -p "Enter the port for the Passenger PWA [default: 80]: " PWA_PORT
PWA_PORT=${PWA_PORT:-80}

read -p "Enter the port for the Admin Dashboard [default: 8080]: " ADMIN_PORT
ADMIN_PORT=${ADMIN_PORT:-8080}

read -p "Enter the internal port for the Backend API [default: 8000]: " API_PORT
API_PORT=${API_PORT:-8000}

echo ""
read -s -p "Enter a strong password for the PostgreSQL 'duk_user': " DB_PASS
echo ""
echo ""

# ── 2. System Packages ──────────────────────────────────────────────────────
echo "[1/7] Installing System Dependencies (Nginx, Postgres, Redis, Python, Docker)..."
apt-get update -qq
apt-get install -y \
    postgresql-16 postgresql-contrib \
    redis-server \
    nginx \
    certbot python3-certbot-nginx \
    python3.11 python3.11-venv python3.11-dev \
    python3-pip \
    build-essential libpq-dev \
    curl git htop ufw \
    docker.io docker-compose-plugin

echo "Creating 'duk' system user..."
if ! id "duk" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false duk
fi

echo "Securing Redis (binding to localhost)..."
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf || true
systemctl enable redis-server
systemctl restart redis-server

# ── 3. PostgreSQL Database ──────────────────────────────────────────────────
echo "[2/7] Configuring PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

sudo -u postgres psql -c "CREATE USER duk_user WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE duk_bus OWNER duk_user;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE duk_bus TO duk_user;" 2>/dev/null || true

# Allow local connections only
if ! grep -q "duk_bus" /etc/postgresql/16/main/pg_hba.conf; then
  echo "local   duk_bus   duk_user   md5" >> /etc/postgresql/16/main/pg_hba.conf
  systemctl reload postgresql
fi

# ── 4. Setup Directories & Virtualenv ───────────────────────────────────────
echo "[3/7] Setting up Application Directories & Python..."
mkdir -p /opt/duk_micro
mkdir -p /opt/duk_pwa
mkdir -p /opt/admin_dashboard

# Copy from current directory to /opt
cp -r duk_micro/* /opt/duk_micro/ 2>/dev/null || true
cp -r duk_pwa1_dist/* /opt/duk_pwa/ 2>/dev/null || true
cp -r admin_dashboard_dist/* /opt/admin_dashboard/ 2>/dev/null || true

python3.11 -m venv /opt/duk_venv
/opt/duk_venv/bin/pip install --upgrade pip -q
/opt/duk_venv/bin/pip install -r /opt/duk_micro/requirements.txt -q

# Generate .env file
cat > /etc/duk_production.env << EOF
DATABASE_URL=postgresql+asyncpg://duk_user:${DB_PASS}@localhost/duk_bus
REDIS_URL=redis://localhost:6379/0
API_PORT=${API_PORT}
CORS_ORIGINS="*"
JWT_SECRET=$(head -c 32 /dev/urandom | base64)
EOF

chown duk:duk /etc/duk_production.env
chmod 600 /etc/duk_production.env

chown -R duk:duk /opt/duk_micro /opt/duk_venv

# ── 4a. Initialize Database Schema ────────────────────────────────────────────
echo "[3.5/7] Initializing Database Schema..."
export DATABASE_URL="postgresql+asyncpg://duk_user:${DB_PASS}@localhost/duk_bus"
/opt/duk_venv/bin/python /opt/duk_micro/init_db.py

# ── 5. Systemd Service ──────────────────────────────────────────────────────
echo "[4/7] Installing Systemd Services (Backend & Workers)..."
cat > /etc/systemd/system/duk-backend.service << EOF
[Unit]
Description=DUK Bus Tracker Backend
After=network.target postgresql.service redis-server.service

[Service]
User=duk
Group=duk
WorkingDirectory=/opt/duk_micro
EnvironmentFile=/etc/duk_production.env
ExecStart=/opt/duk_venv/bin/python /opt/duk_micro/mega_gateway.py --port ${API_PORT}
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/duk-workers.service << EOF
[Unit]
Description=DUK Bus Tracker Background Workers
After=duk-backend.service

[Service]
User=duk
Group=duk
WorkingDirectory=/opt/duk_micro
EnvironmentFile=/etc/duk_production.env
ExecStart=/bin/bash -c "source /opt/duk_venv/bin/activate && /opt/duk_micro/start_workers.sh"
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable duk-backend duk-workers
systemctl restart duk-backend duk-workers

# ── 5a. ML Training Cron Job ────────────────────────────────────────────────
echo "[4.5/7] Setting up automated ETA ML training..."
touch /var/log/duk_ml_train.log
chown duk:duk /var/log/duk_ml_train.log

# Run train.py every Sunday at 2:00 AM
cat > /etc/cron.d/duk-ml-training << 'EOF'
0 2 * * 0 duk set -a; source /etc/duk_production.env; set +a; /opt/duk_venv/bin/python /opt/duk_micro/services/eta-ml-service/train.py >> /var/log/duk_ml_train.log 2>&1
EOF
chmod 0644 /etc/cron.d/duk-ml-training
echo "   Weekly ML cron job configured for Sunday 2:00 AM"

# ── 6. Nginx ────────────────────────────────────────────────────────────────
echo "[5/7] Configuring Nginx..."
sed -e "s/\${PWA_PORT}/${PWA_PORT}/g" \
    -e "s/\${ADMIN_PORT}/${ADMIN_PORT}/g" \
    -e "s/\${API_PORT}/${API_PORT}/g" \
    /opt/duk_micro/deploy/nginx.conf.template > /etc/nginx/sites-available/duk-bus

ln -sf /etc/nginx/sites-available/duk-bus /etc/nginx/sites-enabled/duk-bus
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl restart nginx

# ── 7. OSRM Setup ───────────────────────────────────────────────────────────
echo "[6/7] Setting up OSRM (Docker)..."
chmod +x /opt/duk_micro/deploy/osrm-setup.sh
/opt/duk_micro/deploy/osrm-setup.sh

# ── 8. Firewall ─────────────────────────────────────────────────────────────
echo "[7/7] Setting up Firewall..."
ufw --force reset
ufw default deny incoming
ufw allow ssh
ufw allow ${PWA_PORT}/tcp
ufw allow ${ADMIN_PORT}/tcp
ufw --force enable

echo "==============================================="
echo "   Deployment Complete!"
echo "==============================================="
echo "PWA (Passenger App) is running on port: ${PWA_PORT}"
echo "Admin Dashboard is running on port: ${ADMIN_PORT}"
echo "Backend API is running internally on port: ${API_PORT}"
echo "Check backend logs with: journalctl -fu duk-backend"
echo "==============================================="
