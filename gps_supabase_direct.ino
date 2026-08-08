/**
 * =============================================================================
 *  DUK BUS TRACKER — Turbo GPS Firmware (Sub-2s Direct Supabase Table Insert)
 * =============================================================================
 *  Hardware : ESP32 + Quectel EC200U Cellular + GPS Module
 *  Architecture: 100% Standalone (Zero Laptop / Zero Server needed)
 *
 *  Optimizations:
 *    - Instant early-break UART parser (no waiting for timeouts).
 *    - Signal strength check throttled to once every 15 seconds.
 *    - HTTP Keep-Alive persistent SSL connection to Supabase.
 *    - Ultra-fast cycle time (~1.2 to 1.8 seconds per update).
 * =============================================================================
 */

#include <FS.h>
#include <FastLED.h>
#include <HardwareSerial.h>
#include <SPIFFS.h>
#include <Update.h>

// ================= UART PINS =================
#define MODEM_RX 16
#define MODEM_TX 17

// ================= LED =================
#define LED_PIN 21
#define NUM_LEDS 3

// ================= POWER SENSE PINS =================
#define POWER_SENSE_PIN 35
#define BatVoltage_PIN 39

HardwareSerial MODEM(1);
CRGB leds[NUM_LEDS];

// ================= SUPABASE CONFIGURATION =================
const String SUPABASE_HOST = "mtkdzcdzxtfjwpgnpujc.supabase.co";
const int SUPABASE_PORT = 443;
const String SUPABASE_TABLE = "gps_realtime";
const String SUPABASE_SERVICE_KEY =
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "REDACTED"
    "REDACTED"
    "REDACTED";

// ================= OTA CONFIGURATION =================
const char *VERSION_URL = "https://raw.githubusercontent.com/milma-can-lab/"
                          "GPS_tracker/refs/heads/main/GPS_OTA_version.txt";
const char *FW_BASE_URL =
    "https://raw.githubusercontent.com/milma-can-lab/GPS_tracker/main/GPS_G";
String current_version = "1_0_2";
String new_version = "0_0";

// ================= GLOBAL STATE =================
float lat = 0.0;
float lon = 0.0;
float spd = 0.0;
bool gpsOk = false;

int failCount = 0;
bool pendingPowerOn = false;
bool pendingPowerOff = false;
bool sslConnected = false;

int nflag = 2; // 0=good signal, 1=weak, 2=no signal
int pflag = 1; // 0=power ON (ignition), 1=power OFF

int resetHour = 0;
int resetMinute = 0;
bool resetDone = false;
unsigned long lastResetCheck = 0;
unsigned long lastCsqCheck = 0; // Throttled CSQ timer

// =====================================================================
//  FAST AT COMMAND SENDER (Breaks immediately when response finishes)
// =====================================================================
String sendAT(String cmd, int timeout = 1000, String expected = "") {
  MODEM.println(cmd);
  String resp = "";
  unsigned long start = millis();
  unsigned long lastCharTime = millis();

  while (millis() - start < timeout) {
    while (MODEM.available()) {
      resp += (char)MODEM.read();
      lastCharTime = millis();
    }

    // If we received an explicit expected token, return instantly
    if (expected.length() > 0 && resp.indexOf(expected) != -1) {
      delay(5);
      while (MODEM.available()) resp += (char)MODEM.read();
      break;
    }

    // If response contains OK, ERROR, or prompt >, break immediately
    if (resp.indexOf("OK\r\n") != -1 || resp.indexOf("ERROR\r\n") != -1 || resp.indexOf(">") != -1) {
      delay(5);
      while (MODEM.available()) resp += (char)MODEM.read();
      break;
    }

    // Small yield if no data
    if (resp.length() > 0 && (millis() - lastCharTime > 40)) {
      break;
    }
  }

  // Watchdog check
  if (resp.length() == 0) {
    failCount++;
    if (failCount > 5) {
      Serial.println("[WATCHDOG] Modem not responding. Restarting ESP32...");
      ESP.restart();
    }
  } else {
    failCount = 0;
  }
  return resp;
}

void showResponse(unsigned long timeout = 5000) {
  unsigned long start = millis();
  while (millis() - start < timeout) {
    while (MODEM.available()) {
      Serial.write(MODEM.read());
      start = millis();
    }
  }
  Serial.println();
}

// =====================================================================
//  OTA UPDATE
// =====================================================================
String parseVersionResponse(const String &response) {
  String clean = "", line = "";
  for (int i = 0; i < response.length(); i++) {
    char c = response[i];
    if (c == '\r' || c == '\n') {
      if (line.indexOf('_') > 0 && isDigit(line[0])) {
        clean = line;
        break;
      }
      line = "";
    } else
      line += c;
  }
  return clean;
}

String httpGET(const char *url) {
  MODEM.println("AT+QHTTPCFG=\"contextid\",1");
  delay(200);
  MODEM.print("AT+QHTTPURL=");
  MODEM.print(strlen(url));
  MODEM.println(",60");
  delay(200);
  showResponse();
  MODEM.println(url);
  delay(500);
  showResponse();
  MODEM.println("AT+QHTTPGET=80");
  delay(2000);
  showResponse();
  MODEM.println("AT+QHTTPREAD=120");
  delay(500);
  String response = "";
  unsigned long start = millis();
  while (millis() - start < 5000) {
    while (MODEM.available()) {
      char c = MODEM.read();
      response += c;
      start = millis();
    }
  }
  return parseVersionResponse(response);
}

bool checkForUpdate() {
  String response = httpGET(VERSION_URL);
  if (response.length() == 0)
    return false;
  new_version = response;
  Serial.println("Current: " + current_version +
                 " | Available: " + new_version);
  return (new_version != current_version);
}

bool downloadFirmwareToSPIFFS(bool clearBefore = true) {
  if (clearBefore) {
    if (!SPIFFS.format())
      return false;
    delay(2000);
  }
  String fw_url = String(FW_BASE_URL) + new_version + ".bin";
  String fw_filename = "/firmware_" + new_version + ".bin";
  File fwFile = SPIFFS.open(fw_filename, FILE_WRITE);
  if (!fwFile)
    return false;
  MODEM.print("AT+QHTTPURL=");
  MODEM.print(fw_url.length());
  MODEM.println(",60");
  delay(200);
  showResponse();
  MODEM.println(fw_url);
  delay(500);
  showResponse();
  MODEM.println("AT+QHTTPGET=120");
  delay(3000);
  String getResp = "";
  unsigned long t0 = millis();
  while (millis() - t0 < 5000) {
    while (MODEM.available()) {
      char c = MODEM.read();
      getResp += c;
      t0 = millis();
    }
  }
  int fw_size = 0;
  int idx = getResp.indexOf("+QHTTPGET:");
  if (idx >= 0) {
    int c1 = getResp.indexOf(',', idx + 10);
    int c2 = getResp.indexOf(',', c1 + 1);
    if (c2 > 0)
      fw_size = getResp.substring(c2 + 1).toInt();
  }
  if (fw_size <= 0) {
    fwFile.close();
    return false;
  }
  uint8_t buf[256];
  size_t total_received = 0;
  unsigned long t_start = millis();
  bool data_mode = false;
  MODEM.println("AT+QHTTPREAD=120");
  delay(10);
  String headerBuffer = "";
  while (!data_mode && millis() - t_start < 10000) {
    while (MODEM.available()) {
      char c = MODEM.read();
      headerBuffer += c;
      if (headerBuffer.indexOf("CONNECT\r\n") >= 0) {
        data_mode = true;
        break;
      }
    }
  }
  while (total_received < fw_size && millis() - t_start < 12000) {
    while (MODEM.available()) {
      int remaining = fw_size - total_received;
      int toRead = min((int)sizeof(buf), remaining);
      int len = MODEM.readBytes((char *)buf, toRead);
      if (len > 0) {
        fwFile.write(buf, len);
        total_received += len;
      }
      t_start = millis();
      if (total_received >= fw_size)
        break;
    }
  }
  fwFile.close();
  return (total_received == fw_size);
}

bool flashFirmwareFromSPIFFS(const char *path) {
  File fwFile = SPIFFS.open(path);
  if (!fwFile)
    return false;
  size_t fwSize = fwFile.size();
  if (!Update.begin(fwSize)) {
    fwFile.close();
    return false;
  }
  uint8_t buf[256];
  while (fwFile.available()) {
    int len = fwFile.read(buf, sizeof(buf));
    if (len > 0)
      Update.write(buf, len);
    else
      delay(1);
  }
  fwFile.close();
  if (Update.end() && Update.isFinished()) {
    Serial.println("[OTA] Complete! Restarting...");
    delay(1000);
    ESP.restart();
    return true;
  }
  return false;
}

void performOTAUpdate() {
  Serial.println("=== Checking OTA Updates ===");
  if (!SPIFFS.begin(true)) {
    SPIFFS.format();
    SPIFFS.begin(true);
  }
  if (!checkForUpdate()) {
    Serial.println("[OTA] Firmware is up to date.");
    return;
  }
  for (int attempt = 1; attempt <= 3; attempt++) {
    Serial.printf("[OTA] Attempt %d of 3\n", attempt);
    if (downloadFirmwareToSPIFFS()) {
      String fw_filename = "/firmware_" + new_version + ".bin";
      if (flashFirmwareFromSPIFFS(fw_filename.c_str()))
        return;
    }
    if (attempt < 3)
      delay(3000);
  }
}

// =====================================================================
//  MIDNIGHT RESET
// =====================================================================
bool getCurrentTime(int &hour, int &minute) {
  String resp = sendAT("AT+CCLK?", 500, "OK");
  int idx = resp.indexOf("\"");
  if (idx == -1)
    return false;
  String s = resp.substring(idx + 1, idx + 20);
  hour = s.substring(9, 11).toInt();
  minute = s.substring(12, 14).toInt();
  return true;
}

void checkMidnightReset() {
  if (millis() - lastResetCheck < 60000)
    return;
  lastResetCheck = millis();
  int hour, minute;
  if (!getCurrentTime(hour, minute))
    return;
  if (hour == resetHour && minute == resetMinute && !resetDone) {
    Serial.println("[RESET] Midnight auto-restart.");
    delay(2000);
    ESP.restart();
  }
  if (hour != resetHour || minute != resetMinute)
    resetDone = false;
}

// =====================================================================
//  NETWORK & POWER (CSQ throttled to once every 15 seconds)
// =====================================================================
void checkNetwork() {
  if (millis() - lastCsqCheck < 15000 && lastCsqCheck != 0) return;
  lastCsqCheck = millis();

  String resp = sendAT("AT+CSQ", 400, "OK");
  int rssi = -1;
  int idx = resp.indexOf("+CSQ:");
  if (idx != -1) {
    int comma = resp.indexOf(",", idx);
    rssi = resp.substring(idx + 6, comma).toInt();
  }
  if (rssi == -1 || rssi == 99)
    nflag = 2;
  else if (rssi < 10)
    nflag = 2;
  else if (rssi < 15)
    nflag = 1;
  else
    nflag = 0;
}

float readPinVoltage(int pin) { return (analogRead(pin) / 4095.0) * 3.3; }
float readExternalVoltage() {
  return readPinVoltage(POWER_SENSE_PIN) * ((100000.0 + 33000.0) / 33000.0);
}
void checkPower() { pflag = (readExternalVoltage() > 0.2) ? 0 : 1; }

// =====================================================================
//  LED INDICATORS
// =====================================================================
void updateLEDs(bool sending) {
  leds[0] = gpsOk ? CRGB(0, 255, 0) : CRGB(255, 0, 0);
  leds[1] = sending        ? CRGB(0, 0, 255)
            : (nflag == 0) ? CRGB(0, 255, 0)
            : (nflag == 1) ? CRGB(255, 165, 0)
                           : CRGB(255, 0, 0);
  leds[2] = (pflag == 0)   ? CRGB(0, 255, 0)
            : (pflag == 1) ? CRGB(255, 165, 0)
                           : CRGB(255, 0, 0);
  FastLED.show();
}

// =====================================================================
//  FAST GPS READER (Returns immediately when +QGPSLOC arrives)
// =====================================================================
bool getGPS() {
  String gpsResponse = sendAT("AT+QGPSLOC=2", 300, "OK");
  int idx = gpsResponse.indexOf("+QGPSLOC:");
  if (idx == -1) {
    gpsOk = false;
    return false;
  }

  gpsResponse.trim();
  String data = gpsResponse.substring(idx + 10);
  data.trim();

  int p1 = data.indexOf(',');
  int p2 = data.indexOf(',', p1 + 1);
  int p3 = data.indexOf(',', p2 + 1);
  int p7 = p3;
  for (int i = 0; i < 4; i++)
    p7 = data.indexOf(',', p7 + 1);
  int p8 = data.indexOf(',', p7 + 1);

  lat = data.substring(p1 + 1, p2).toFloat();
  lon = data.substring(p2 + 1, p3).toFloat();
  spd = data.substring(p7 + 1, p8).toFloat();

  Serial.printf("[GPS] Lat: %.6f | Lon: %.6f | Speed: %.1f km/h\n", lat, lon, spd);
  gpsOk = true;
  return true;
}

// =====================================================================
//  PERSISTENT SSL CONNECTION TO SUPABASE
// =====================================================================
bool ensureSSLConnected() {
  if (sslConnected)
    return true;

  Serial.println("[SSL] Opening persistent connection to Supabase...");
  sendAT("AT+QSSLCLOSE=0", 500, "OK");
  delay(100);

  sendAT("AT+QSSLCFG=\"sslversion\",0,4", 300, "OK");
  sendAT("AT+QSSLCFG=\"seclevel\",0,0", 300, "OK");
  sendAT("AT+QSSLCFG=\"sni\",0,1", 300, "OK");
  sendAT("AT+QSSLCFG=\"ignorelocaltime\",0,1", 300, "OK");

  String openResp = sendAT("AT+QSSLOPEN=1,0,0,\"" + SUPABASE_HOST + "\"," +
                               String(SUPABASE_PORT) + ",0",
                           8000, "+QSSLOPEN");

  if (openResp.indexOf("+QSSLOPEN: 0,0") != -1 ||
      openResp.indexOf("+QSSLOPEN: 0, 0") != -1) {
    Serial.println("[SSL] Connected successfully (Context 0). Pipe is OPEN!");
    sslConnected = true;
    return true;
  }

  Serial.println("[SSL] Connection failed: " + openResp);
  sslConnected = false;
  return false;
}

// =====================================================================
//  TURBO DIRECT TABLE INSERT (Sub-second execution)
// =====================================================================
bool saveToTable(String eventName, float latVal = 0, float lonVal = 0,
                 float spdVal = 0) {
  if (!ensureSSLConnected()) {
    return false;
  }

  // 1. Build JSON Payload
  String jsonBody;
  if (eventName == "GPS") {
    jsonBody = "{\"lat\":" + String(latVal, 6) +
               ",\"lon\":" + String(lonVal, 6) +
               ",\"speed\":" + String(spdVal, 1) + ",\"event\":\"GPS\"}";
  } else {
    jsonBody = "{\"event\":\"" + eventName + "\"}";
  }

  // 2. Build HTTP POST with Connection: keep-alive
  String httpRequest = "POST /rest/v1/" + SUPABASE_TABLE +
                       " HTTP/1.1\r\n"
                       "Host: " +
                       SUPABASE_HOST +
                       "\r\n"
                       "apikey: " +
                       SUPABASE_SERVICE_KEY +
                       "\r\n"
                       "Authorization: Bearer " +
                       SUPABASE_SERVICE_KEY +
                       "\r\n"
                       "Content-Type: application/json\r\n"
                       "Prefer: return=minimal\r\n"
                       "Content-Length: " +
                       String(jsonBody.length()) +
                       "\r\n"
                       "Connection: keep-alive\r\n\r\n" +
                       jsonBody;

  // 3. Send over existing SSL pipe (breaks immediately on > prompt)
  String prompt = sendAT("AT+QSSLSEND=0," + String(httpRequest.length()), 1500, ">");
  if (prompt.indexOf(">") == -1) {
    Serial.println("[SUPABASE] Socket dropped. Reconnecting next cycle.");
    sslConnected = false;
    sendAT("AT+QSSLCLOSE=0", 300, "OK");
    return false;
  }

  MODEM.print(httpRequest);

  // 4. Read response (exits the millisecond 201 Created arrives)
  String result = "";
  unsigned long start = millis();
  while (millis() - start < 1800) {
    String chunk = sendAT("AT+QSSLRECV=0,512", 250, "OK");
    result += chunk;
    if (result.indexOf("201 Created") != -1 || result.indexOf("200 OK") != -1)
      break;
  }

  if (result.indexOf("Connection: close") != -1) {
    sslConnected = false;
    sendAT("AT+QSSLCLOSE=0", 300, "OK");
  }

  if (result.indexOf("201 Created") != -1 || result.indexOf("200 OK") != -1) {
    Serial.println("✅ [TABLE SUCCESS] " + eventName + " saved to Supabase!");
    return true;
  }

  Serial.println("[SUPABASE] Response: " + result);
  return false;
}

// =====================================================================
//  INITIALIZATION
// =====================================================================
void initialization() {
  Serial.println("\n[INIT] ===== SYSTEM BOOT =====");
  MODEM.begin(115200, SERIAL_8N1, MODEM_RX, MODEM_TX);
  delay(500);

  sendAT("AT", 300, "OK");
  sendAT("AT+CFUN=1,1", 300, "OK");
  delay(4000);

  sendAT("AT+CPIN?", 500, "OK");
  sendAT("AT+CREG?", 500, "OK");
  sendAT("AT+CGATT?", 500, "OK");

  // Activate 4G cellular data (PDP context 1)
  sendAT("AT+QICSGP=1,1,\"internet\",\"\",\"\",1", 500, "OK");
  sendAT("AT+QIACT=1", 6000, "OK");
  Serial.println("[INIT] 4G Data Active!");

  // Start GPS engine
  sendAT("AT+QGPS=1", 1000, "OK");
  Serial.println("[INIT] GPS Engine Started!");

  // FastLED
  FastLED.addLeds<WS2812, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(150);
  FastLED.clear();
  FastLED.show();

  // ADC pins
  analogSetPinAttenuation(POWER_SENSE_PIN, ADC_11db);
  analogSetPinAttenuation(BatVoltage_PIN, ADC_11db);

  Serial.println("[INIT] ===== BOOT COMPLETE =====\n");
}

// =====================================================================
//  SETUP
// =====================================================================
void setup() {
  Serial.begin(115200);
  pinMode(18, OUTPUT);
  digitalWrite(18, HIGH);
  initialization();
  performOTAUpdate();
  randomSeed(millis());
}

// =====================================================================
//  MAIN LOOP
// =====================================================================
void loop() {
  digitalWrite(18, HIGH);

  checkMidnightReset();
  checkNetwork();
  checkPower();

  static bool prevPowerState = false;
  bool currentPowerState = (pflag == 0);

  auto sendData = [&](String eventName, float latV = 0, float lonV = 0,
                      float spdV = 0) {
    updateLEDs(true);
    bool ok = saveToTable(eventName, latV, lonV, spdV);
    if (!ok) {
      if (eventName == "POWER_ON")
        pendingPowerOn = true;
      if (eventName == "POWER_OFF")
        pendingPowerOff = true;
    }
  };

  // Retry queued power events
  if (pendingPowerOn) {
    pendingPowerOn = false;
    saveToTable("POWER_ON");
  }
  if (pendingPowerOff) {
    pendingPowerOff = false;
    saveToTable("POWER_OFF");
  }

  // ── POWER ON (Ignition turned ON) ─────────────────────────────────────────
  if (currentPowerState && !prevPowerState) {
    Serial.println("\n========== [EVENT] POWER ON ==========");
    sendData("POWER_ON");
    if (getGPS())
      sendData("GPS", lat, lon, spd);
  }

  // ── POWER OFF (Ignition turned OFF) ───────────────────────────────────────
  else if (!currentPowerState && prevPowerState) {
    Serial.println("\n========== [EVENT] POWER OFF ==========");
    sendData("POWER_OFF");
    if (getGPS())
      sendData("GPS", lat, lon, spd);
  }

  // ── NORMAL GPS (Running & Power is ON) ────────────────────────────────────
  else if (currentPowerState) {
    if (getGPS()) {
      sendData("GPS", lat, lon, spd);
    }
  }

  // ── IDLE (Power OFF) ──────────────────────────────────────────────────────
  else {
    Serial.println("[IDLE] Power off.");
  }

  prevPowerState = currentPowerState;
  updateLEDs(false);
  delay(100); // 100ms yield (Super fast refresh)
}
