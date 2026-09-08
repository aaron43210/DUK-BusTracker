#!/bin/bash
# 
# DUK Bus Tracker — Server Setup Script
# Run this ONCE on a fresh Ubuntu 22.04 LTS server with a static IP.
#
# Usage:
#   chmod +x setup.sh
#   sudo ./setup.sh
#
# What it does:
#   1. Installs system packages (PostgreSQL, Redis, Nginx, Python, Node, Docker)
#   2. Creates the duk_user system account
#   3. Sets up the PostgreSQL database
#   4. Creates the virtual environment and installs Python dependencies
#   5. Sets up Nginx with a placeholder config (update domain later)
#   6. Installs and enables the systemd service
# 
set -e  # Exit immediately if any command fails

echo ""
echo "  DUK Bus Tracker — Production Server Setup"
echo ""

#  1. System packages 
echo ""
echo "[1/8] Installing system packages..."
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

#  2. System user 
echo ""
echo "[2/8] Creating duk system user..."
if ! id "duk" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false duk
    echo "   User 'duk' created"
else
    echo "   User 'duk' already exists"
fi

#  3. PostgreSQL database 
echo ""
echo "[3/8] Setting up PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

# Prompt for DB password
read -s -p "  Enter a strong password for the 'duk_user' database user: " DB_PASS
echo ""

sudo -u postgres psql -c "CREATE USER duk_user WITH PASSWORD '$DB_PASS';" 2>/dev/null || echo "  (user already exists)"
sudo -u postgres psql -c "CREATE DATABASE duk_bus OWNER duk_user;" 2>/dev/null || echo "  (database already exists)"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE duk_bus TO duk_user;"

# Allow local connections only
cat >> /etc/postgresql/16/main/pg_hba.conf << 'EOF'
# DUK App — local socket auth
local   duk_bus   duk_user   md5
EOF
systemctl reload postgresql
echo "   PostgreSQL configured"

#  4. Redis 
echo ""
echo "[4/8] Configuring Redis..."
# Bind only to localhost — never expose Redis to the internet
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
systemctl enable redis-server
systemctl restart redis-server
echo "   Redis bound to localhost only"

#  5. Python environment 
echo ""
echo "[5/8] Setting up Python virtual environment..."
mkdir -p /opt/duk_micro
python3.11 -m venv /opt/duk_venv
/opt/duk_venv/bin/pip install --upgrade pip -q
/opt/duk_venv/bin/pip install -r /opt/duk_micro/requirements.txt -q
echo "   Python venv ready at /opt/duk_venv"

#  6. Nginx 
echo ""
echo "[6/8] Configuring Nginx..."
mkdir -p /var/www/duk-pwa
cp /opt/duk_micro/deploy/nginx.conf /etc/nginx/sites-available/duk-bus
ln -sf /etc/nginx/sites-available/duk-bus /etc/nginx/sites-enabled/duk-bus
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl reload nginx
echo "   Nginx configured"

#  7. Firewall 
echo ""
echo "[7/8] Setting up firewall..."
ufw --force reset
ufw default deny incoming
ufw allow ssh          # Port 22
ufw allow 80/tcp       # HTTP
ufw allow 443/tcp      # HTTPS
ufw --force enable
echo "   Firewall enabled (22, 80, 443 open)"

#  8. ML Training Cron Job 
echo ""
echo "[8/8] Setting up automated ETA ML training..."
# Run train.py every Sunday at 2:00 AM
cat > /etc/cron.d/duk-ml-training << 'EOF'
0 2 * * 0 root set -a; source /etc/duk/production.env; set +a; /opt/duk_venv/bin/python /opt/duk_micro/services/eta-ml-service/train.py >> /var/log/duk_ml_train.log 2>&1
EOF
chmod 0644 /etc/cron.d/duk-ml-training
echo "   Weekly cron job configured for Sunday 2:00 AM"

#  Final: Systemd service 
echo ""
echo "Installing systemd service..."
cp /opt/duk_micro/deploy/duk-backend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable duk-backend
echo "   Service installed (not started — configure .env first)"

echo ""
echo ""
echo "  Setup complete!"
echo ""
echo "  NEXT STEPS:"
echo "  1. Copy your production .env to /etc/duk/production.env"
echo "  2. Copy the duk_micro/ folder to /opt/duk_micro/"
echo "  3. Copy the duk_pwa/dist/ folder to /var/www/duk-pwa/"
echo "  4. Start the backend: systemctl start duk-backend"
echo "  5. Get SSL cert:  certbot --nginx -d your-domain.com"
echo ""
