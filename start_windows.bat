@echo off
setlocal enabledelayedexpansion
title DUK Bus Tracker - Windows 1-Click Launcher
color 0B

echo ================================================================
echo           DUK BUS TRACKER - 1-CLICK WINDOWS LAUNCHER
echo ================================================================
echo.

:: 1. Check Docker
echo [1/4] Checking Docker installation...
docker --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Docker is not installed or not in PATH.
    echo [*] Attempting to install Docker Desktop via winget...
    winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
    if %ERRORLEVEL% NEQ 0 (
        echo [!] Automatic install failed. Opening Docker download page...
        start https://www.docker.com/products/docker-desktop/
        echo Please install Docker Desktop, start it, and re-run this script.
        pause
        exit /b 1
    )
    echo [*] Docker installed! Please start Docker Desktop from Start Menu, then press any key.
    pause
)

:: Ensure Docker Daemon is running
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Docker Desktop is not running.
    echo [*] Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker daemon to initialize...
    :wait_docker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [*] Still waiting for Docker engine...
        goto wait_docker
    )
    echo [OK] Docker daemon is running!
) else (
    echo [OK] Docker is running.
)
echo.

:: 2. Check Node.js
echo [2/4] Checking Node.js installation...
node -v >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Node.js is not installed.
    echo [*] Installing Node.js LTS via winget...
    winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    if %ERRORLEVEL% NEQ 0 (
        echo [!] Opening Node.js download page...
        start https://nodejs.org/
        echo Please install Node.js and re-run this script.
        pause
        exit /b 1
    )
    echo [*] Node.js installed! Please restart your terminal/cmd to refresh PATH.
    pause
    exit /b 0
) else (
    echo [OK] Node.js is installed.
)
echo.

:: 3. Start Backend Services (Postgres, Redis, OSRM, Traefik, FastAPI)
echo [3/4] Building and starting backend microservices in Docker...
docker compose up -d --build
if %ERRORLEVEL% NEQ 0 (
    echo [!] Failed to start Docker containers. Check docker logs.
    pause
    exit /b 1
)
echo [OK] All backend services are healthy and running!
echo.

:: 4. Install & Launch Frontends
echo [4/4] Starting Admin Dashboard and Passenger PWA...
cd admin-dashboard
if not exist node_modules (
    echo [*] Installing Admin Dashboard dependencies...
    call npm install
)
start "DUK Admin Dashboard" cmd /k "title Admin Dashboard && npm run dev"
cd ..

cd duk_pwa
if not exist node_modules (
    echo [*] Installing Passenger PWA dependencies...
    call npm install
)
start "DUK Passenger PWA" cmd /k "title Passenger PWA && npm run dev"
cd ..

echo.
echo ================================================================
echo           SYSTEM LAUNCHED SUCCESSFULLY!
echo ================================================================
echo.
echo  - Admin Dashboard:    http://localhost:5173
echo  - Passenger PWA:      http://localhost:5174
echo  - Backend API:        http://localhost/api/v1/stops
echo  - Default Admin User: admin / admin
echo.
echo Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start http://localhost:5173
start http://localhost:5174

pause
