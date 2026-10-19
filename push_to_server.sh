#!/bin/bash
# Server deployment script via SSH (rsync)
# Run this from the /Users/aaronr/Desktop/DUK_MICRO directory

# Abort on the first failed step (ssh returns the remote command's exit code),
# so a failed build or migration is never reported as a successful deploy.
set -eo pipefail

SERVER="can@10.10.35.205"
SERVER_PATH="~/duk-bus-tracker-final"

echo "==============================================="
echo "   DUK Bus Tracker — SSH Direct Deployment"
echo "==============================================="
echo ""

echo "[0/4] Cleaning up stray files in server home directory..."
ssh -t $SERVER "rm -rf ~/audit_logger.py ~/eta_main.py ~/firebase_helper.py ~/firebase_singleton.py ~/gps_main.py ~/tracking_main.py ~/duk_pwa1_dist ~/duk_micro ~/pwa_dist ~/website_dist ~/lib_updates ~/service_updates || true; \
  if [ -d $SERVER_PATH/duk_micro/alembic/versions ]; then rm -rf $SERVER_PATH/alembic_versions_backup && cp -a $SERVER_PATH/duk_micro/alembic/versions $SERVER_PATH/alembic_versions_backup; fi"
echo ""

echo "[1/4] Syncing duk_micro Backend to Server..."
# Sync the microservices source code, ignoring Python cache and venv
rsync -avz --exclude="venv" --exclude="__pycache__" --exclude=".git" ./duk_micro $SERVER:$SERVER_PATH/
# Mirror migrations exactly. A stale file left on the server (e.g. an old copy of
# a revision under another filename) makes Alembic see duplicate revisions and
# multiple heads. The pre-sync copy is kept in $SERVER_PATH/alembic_versions_backup
# (step 0). No -b/--backup-dir here: macOS openrsync skips deletions when -b is set.
rsync -avz --delete --exclude="__pycache__" \
  ./duk_micro/alembic/versions/ $SERVER:$SERVER_PATH/duk_micro/alembic/versions/
# Sync docker-compose.prod.yml for orchestration
rsync -avz ./docker-compose.prod.yml $SERVER:$SERVER_PATH/docker-compose.yml

echo ""
echo "[2/4] Deploying duk_pwa1 (Progressive Web App)..."
cd duk_pwa1
npm run build
# Sync dist folder to NGINX path. Requires sudo privileges on the server side for /opt
# We sync to a temp directory first, then move it
rsync -avz ./dist/ $SERVER:$SERVER_PATH/duk_pwa1_dist_temp/
ssh -t $SERVER "sudo rsync -a $SERVER_PATH/duk_pwa1_dist_temp/ /opt/duk_pwa1/ && sudo rm -rf $SERVER_PATH/duk_pwa1_dist_temp"
cd ..

echo ""
echo "[3/5] Deploying duk_bus_website (Public Site)..."
cd duk_bus_website
npm run build
rsync -avz ./dist/ $SERVER:$SERVER_PATH/duk_bus_website_dist_temp/
ssh -t $SERVER "sudo rsync -a $SERVER_PATH/duk_bus_website_dist_temp/ /var/www/duk_bus_website/ && sudo rm -rf $SERVER_PATH/duk_bus_website_dist_temp"
cd ..

echo ""
echo "[4/5] Deploying admin-dashboard..."
cd admin-dashboard
npm run build
rsync -avz ./dist/ $SERVER:$SERVER_PATH/admin_dashboard_dist_temp/
ssh -t $SERVER "sudo rsync -a $SERVER_PATH/admin_dashboard_dist_temp/ /opt/admin_dashboard/ && sudo rm -rf $SERVER_PATH/admin_dashboard_dist_temp"
cd ..

echo ""
echo "[5/5] Running Backend Updates on Server (Alembic & Docker)..."
ssh -t $SERVER "cd $SERVER_PATH && \
  echo 'Rebuilding Docker Containers...' && \
  sudo docker-compose down && \
  sudo docker-compose build && \
  sudo docker-compose up -d && \
  echo 'Fixing Postgres Table Ownership...' && \
  (cd /tmp && sudo -u postgres psql -d duk_bus -c \"ALTER TABLE scheduled_notifications OWNER TO duk_user; ALTER TABLE users OWNER TO duk_user; ALTER TABLE bus_stops OWNER TO duk_user;\" || true) && \
  echo 'Running Alembic Migrations...' && \
  sudo docker-compose exec core-api alembic upgrade head"

echo ""
echo "✅ Deployment Complete!"
echo ""
echo "📌 REMINDER: Ensure /opt/duk_micro/.env on the server contains:"
echo "   GOOGLE_CLIENT_ID=<your_google_oauth_client_id>"
echo "   (Get from Google Cloud Console → APIs & Services → Credentials)"
