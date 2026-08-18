#!/bin/bash
set -e

echo "Starting Mega-Container initialization..."

# 1. Start Postgres in the background
echo "Starting PostgreSQL..."
service postgresql start

# Wait for Postgres to be ready
until su - postgres -c "psql -c '\q'"; do
  >&2 echo "Postgres is unavailable - sleeping"
  sleep 1
done

# Initialize database and user if they don't exist
echo "Setting up Postgres Database..."
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='duk'\" | grep -q 1 || psql -c \"CREATE USER duk WITH PASSWORD 'dukpassword';\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='duk_bus'\" | grep -q 1 || psql -c \"CREATE DATABASE duk_bus OWNER duk;\""
su - postgres -c "psql -c \"ALTER USER duk WITH SUPERUSER;\""

# 2. Start Redis in the background
echo "Starting Redis..."
service redis-server start

# 3. Start Supervisord to manage all Python processes
echo "Starting Supervisord (Python Microservices)..."
/usr/bin/supervisord -c /app/supervisord.conf
