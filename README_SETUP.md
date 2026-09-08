# 🚌 DUK Bus Tracker — Setup & Quickstart Guide

This project is fully self-contained. You do **NOT** need to pre-install PostgreSQL, Redis, or OSRM on your computer — Docker Compose manages all backend microservices, database, caching, and map routing automatically.

---

## 💻 Quickstart for Windows (1-Click)

1. **Extract the ZIP file** to any folder on your computer.
2. **Double-click `start_windows.bat`**.
   - If you do not have **Docker Desktop** or **Node.js**, the script will automatically install them for you.
   - It will automatically launch all backend containers, install frontend packages, and open the apps in your browser.
3. **Access the Apps**:
   - **Admin Dashboard**: [http://localhost:5173](http://localhost:5173) *(Login: `admin` / `admin`)*
   - **Passenger / Student PWA**: [http://localhost:5174](http://localhost:5174)
   - **Backend API**: [http://localhost/api/v1/stops](http://localhost/api/v1/stops)

---

## 🍏 Quickstart for Mac / Linux / WSL

1. Open your terminal in the extracted project folder.
2. Run the 1-click startup script:
   ```bash
   chmod +x start.sh deploy_server.sh
   ./start.sh
   ```
3. Open [http://localhost:5173](http://localhost:5173) (Admin) and [http://localhost:5174](http://localhost:5174) (PWA).

---

## ✉️ Setting up Email / SMTP (Optional)

If you want the system to send real password reset or verification emails:
1. Open `duk_micro/.env` in Notepad or your code editor.
2. Add your Gmail and App Password:
   ```ini
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your_email@gmail.com
   SMTP_PASSWORD=your_16_character_app_password
   SMTP_FROM=your_email@gmail.com
   ```
3. Restart the backend:
   ```bash
   docker compose up -d --build
   ```

---

## 🛑 How to Stop the Project

- To stop the frontend servers: Press `Ctrl + C` in the command window.
- To stop the backend Docker containers:
  ```bash
  docker compose down
  ```
