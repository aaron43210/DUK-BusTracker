#!/bin/bash
# DUK Bus Tracker - Complete Ubuntu Server Setup Script

set -e

echo "============================================="
echo " Complete Server Setup for DUK Bus Tracker"
echo "============================================="

# 1. Ask for IP Address (defaults to localhost)
read -p "Enter your server's IP Address (Press Enter for localhost / local network): " SERVER_IP
SERVER_IP=${SERVER_IP:-localhost}
echo "Using Server Host: $SERVER_IP"

echo "--> Updating Frontend .env files with IP: $SERVER_IP"
# Update PWA .env
cat <<EOF > duk_pwa/.env
VITE_API_URL=http://$SERVER_IP
VITE_WS_URL=ws://$SERVER_IP
VITE_MAP_STYLE_URL=https://basemaps.cartocdn.com/gl/positron-gl-style/style.json
EOF

# Update Admin .env
cat <<EOF > admin-dashboard/.env
VITE_API_URL=http://$SERVER_IP
VITE_WS_URL=ws://$SERVER_IP
EOF
echo "Environment files updated!"

# 2. Install Docker and Docker Compose
echo "--> Checking for Docker..."
if ! command -v docker &> /dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "ERROR: Docker is not installed. You are on macOS."
        echo "Please install Docker Desktop for Mac manually from: https://www.docker.com/products/docker-desktop"
        echo "Or install OrbStack (recommended for Mac): https://orbstack.dev/"
        echo "After installing and starting Docker, run this script again."
        exit 1
    else
        echo "--> Installing Docker and Docker Compose (if not installed)..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker $USER
        rm get-docker.sh
        echo "Docker installed successfully."
    fi
else
    echo "Docker is already installed."
fi

# 3. Firewall Configuration (UFW)
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "--> Skipping Firewall Configuration on macOS."
else
    echo "--> Configuring Firewall (UFW)..."
    if command -v ufw &> /dev/null; then
        sudo ufw default deny incoming
        sudo ufw default allow outgoing
        sudo ufw allow 22/tcp     # SSH
        sudo ufw allow 80/tcp     # HTTP
        sudo ufw allow 443/tcp    # HTTPS
        # Enable UFW non-interactively
        sudo ufw --force enable
        echo "Firewall configured successfully."
    else
        echo "--> ufw command not found, skipping firewall."
    fi
fi

# 4. Swap File Creation (OOM Prevention)
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "--> Skipping Swap File Creation on macOS."
else
    echo "--> Creating 2GB Swap File..."
    if grep -q "swap" /etc/fstab; then
        echo "Swap already exists in /etc/fstab. Skipping."
    else
        # Create 2GB swap file
        sudo fallocate -l 2G /swapfile
        sudo chmod 600 /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        # Make it permanent
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
        # Optimize swappiness for an SSD-backed VM
        sudo sysctl vm.swappiness=10
        echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
        echo "2GB Swap created successfully."
    fi
fi

# 6. Start the Application
echo "--> Building and Starting Docker Containers..."
# Run docker compose using sudo since the user was just added to the docker group
sudo docker compose up -d --build

echo "============================================="
echo " Setup Complete!"
echo " The application is now starting in the background."
echo " You can view the API at: http://$SERVER_IP/api/v1/stops"
echo " Note: You may need to log out and log back in for docker permissions to apply to your user without sudo."
echo "============================================="
