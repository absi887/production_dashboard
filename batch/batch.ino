/*
 * ============================================================================
 * ESP32 Batch Tracking System
 * ============================================================================
 * Tracks batch processing with pause/resume; PLAY always continues work:
 *   PAUSED -> RESUME, IDLE/PROCUREMENT -> new START (fixes dashboard "START"
 *   while paused, which previously no-oped).
 * Synchronized multi-lane: use {"line":"all","command":"START"} or serial
 *   STARTALL / PLAYALL — shared millis() batch start for lanes that begin
 *   together.
 * Optional GET /api/machine-config?machine=... JSON keys per lane:
 *   "door_expected_s":3600,"door_qty":11,"door_stage_index":1,
 *   "door_stage_key":"JOB-1|1|Edge Banding|RUNNING|2026-...",
 *   "door_machine":"Edge Banding","door_status":"RUNNING"
 * When door_stage_key changes the ESP32 resets its local stage timer so every
 * production stage stays aligned with the dashboard (Scale Saw, Edge Banding, …).
 * Emits expected_remaining_s, variance_s (actual−expected), STAGE_TARGET
 *   when active time reaches expected_duration_s (if configured).
 * Buttons (INPUT_PULLUP, LOW = pressed): short tap = PLAY, double = PAUSE
 *   while running, long hold = END, triple (idle) = PROCUREMENT.
 * LEDs (synced from API door_status / frame_status / arch_status every 10s and
 *   after each POST /data): Green = RUNNING, Yellow = PAUSED or READY (staged),
 *   Red = IDLE/STOPPED (no active job on lane).
 * ============================================================================
 */

// ============================================================================
// INCLUDES
// ============================================================================
#include <EEPROM.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>


// ============================================================================
// CONSTANTS & CONFIGURATION
// ============================================================================

#define NUM_LINES 3

// Pins for Buttons
const int BUTTON_PINS[NUM_LINES] = {32, 5, 19};

// Pins for LEDs (Green, Yellow, Red per line)
const int GREEN_LEDS[NUM_LINES] = {15, 2, 4};
const int YELLOW_LEDS[NUM_LINES] = {27, 26, 25};
const int RED_LEDS[NUM_LINES] = {14, 21, 22};

// Button timing
const int LONG_PRESS_MS = 1500;
const int DOUBLE_CLICK_MS = 300;

// HTTP & Network
#define MAX_RETRIES 3
#define HTTP_TIMEOUT 5000
#define STATUS_UPDATE_INTERVAL 60000 // Send status update every 60 seconds
#define COMMAND_CHECK_INTERVAL 2000 // Check for remote commands every 2 seconds
#define CONFIG_FETCH_INTERVAL_MS 10000 // Refresh lane status + LEDs from server
#define MAX_QUEUE_SIZE 15           // Slightly increased for 3 lines
#define BUTTON_DEBOUNCE_MS 45       // Ignore very short presses (noise)

// EEPROM
#define EEPROM_SIZE 128 // Increased for 3 batch numbers
#define EEPROM_BATCH_BASE_ADDR                                                 \
  0 // Base address for batch numbers (int = 4 bytes each)

// WiFi Credentials
const char *ssid = "Anas";
const char *password = "1234567890";

// Flask Server Configuration
// Patched automatically from server PUBLIC_URL on backend start (re-flash after deploy).
const char *serverIP = "192.168.1.29";
const int serverPort = 5002;
bool useHTTPS = false;
String serverURL = "https://productionbackend-production-1b08.up.railway.app";
String commandURL = "";
String configURL = "";


// NTP Configuration
const char *ntpServer = "pool.ntp.org";
const long gmtOffset_sec = 0;
const int daylightOffset_sec = 0;

// Machine Identifier
String machineName = "FAM Production Hub";
String lineName = "door";
String lineNames[NUM_LINES] = {"door", "frame", "arch"};

// ============================================================================
// GLOBAL VARIABLES
// ============================================================================

// READY = job deployed on lane, waiting for Start (yellow only — matches dashboard)
enum State { IDLE, READY, RUNNING, PAUSED, PROCUREMENT };

struct LineData {
  // State Management
  State currentState = IDLE;

  // Button Handling
  unsigned long lastPress = 0;
  unsigned long pressedTime = 0;
  unsigned long releasedTime = 0;
  bool pressed = false;
  bool waitForDouble = false;

  // Batch Timer Variables
  unsigned long batchStartTime = 0;
  unsigned long pauseStartTime = 0;
  unsigned long batchEndTime = 0;
  unsigned long totalPausedTime = 0;
  unsigned long lastStatusUpdate = 0;
  unsigned long procurementStartTime = 0;
  int pauseCount = 0;
  int batchNumber = 0;
  int clickCount = 0;

  // Expected duration from server / defaults (seconds, 0 = unknown)
  long expectedDurationSec = 0;
  int plannedQuantity = 1;
  bool stageTargetLogged = false;

  // Dashboard sync (per-stage key from GET /api/machine-config or /data response)
  String lastServerStageKey = "";
  String serverOrderId = "";
  int serverStageIndex = -1;
  String serverMachine = "";
  String serverLaneStatus = "";
};

LineData lines[NUM_LINES];
unsigned long lastCommandCheck = 0;
unsigned long lastConfigFetch = 0;
WebSocketsClient webSocket;

// Offline Event Queue
struct QueuedEvent {
  String jsonData;
  bool isValid;
};
QueuedEvent eventQueue[MAX_QUEUE_SIZE];
int queueHead = 0;
int queueTail = 0;
int queueCount = 0;

// ============================================================================
// FORWARD DECLARATIONS
// ============================================================================

// Button & State Functions
void handleButton(int idx);
void updateLEDs(int idx);
void startBatch(int idx);
void startBatchAt(int idx, unsigned long batchStartMillis);
void unifiedPlay(int idx); // RESUME if paused, else START from IDLE/PROCUREMENT
void unifiedPlayAll();
void pauseBatch(int idx);
void resumeBatch(int idx);
void endBatch(int idx);
void startProcurement(int idx);

// Networking Functions
void webSocketEvent(WStype_t type, uint8_t *payload, size_t length);
void logEvent(int idx, const String &evt, const String &status,
              bool queueIfOffline = true);
bool sendHTTPRequest(const String &jsonData, int retries = MAX_RETRIES);
void queueEvent(const String &jsonData);
void processEventQueue();
void updatePeriodicStatus();
void reconnectWiFi();
void checkRemoteCommands();
void fetchMachineConfig();
void applyRemoteCommandForLine(int idx, const String &command);
void dispatchRemotePayload(const String &payload);
void applyConfigFromPayload(const String &payload);
void applyDashboardLaneStatus(int idx, const String &laneStatus);
String buildJSONPayload(int idx, const String &evt, const String &status);
void checkStageTargets();
void resetStageTimerFromServer(int idx);
void syncStageFromServerConfig(int idx, const String &payload);
void buildServerEndpoints();
String urlEncodeMachineName(const String &name);
static bool extractJsonStringAfterKey(const String &payload, const String &key,
                                      String *out);

// Utility Functions
String getTimestamp();
String getFormattedTime(unsigned long millisTime);
float calculateEfficiency(unsigned long activeTime, unsigned long totalTime);
void loadBatchNumbers();
void saveBatchNumbers();
void resetBatchNumber(int idx);
void setBatchNumber(int idx, int newNumber);

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\n========================================");
  Serial.println("ESP32 3-Line Batch Tracker Starting...");
  Serial.println("========================================\n");

  // Initialize Pins
  for (int i = 0; i < NUM_LINES; i++) {
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
    pinMode(GREEN_LEDS[i], OUTPUT);
    pinMode(YELLOW_LEDS[i], OUTPUT);
    pinMode(RED_LEDS[i], OUTPUT);

    // Set initial LED state
    updateLEDs(i);
  }

  // Initialize EEPROM and load batch numbers
  EEPROM.begin(EEPROM_SIZE);
  loadBatchNumbers();

  // Initialize event queue
  for (int i = 0; i < MAX_QUEUE_SIZE; i++) {
    eventQueue[i].isValid = false;
  }
  Serial.println("✓ Event queue initialized");

  // Initialize WiFi
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(true);
  WiFi.begin(ssid, password);

  Serial.print("Connecting to WiFi: ");
  int retryCount = 0;
  while (WiFi.status() != WL_CONNECTED && retryCount < 20) {
    delay(500);
    Serial.print(".");
    retryCount++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✓ WiFi Connected!");
    Serial.printf("  IP Address: %s\n", WiFi.localIP().toString().c_str());

    buildServerEndpoints();
    if (serverURL.length() == 0) {
      Serial.println("✗ Server URLs not configured — check serverIP / useHTTPS in batch.ino");
    } else {
      Serial.printf("  Server URL: %s\n", serverURL.c_str());
      Serial.printf("  Command URL: %s\n", commandURL.c_str());
      Serial.printf("  Config URL: %s\n", configURL.c_str());
    }

    // Sensible defaults until /api/machine-config returns per-lane targets
    lines[0].expectedDurationSec = 0;
    lines[1].expectedDurationSec = 0;
    lines[2].expectedDurationSec = 0;
    lines[0].plannedQuantity = 1;
    lines[1].plannedQuantity = 1;
    lines[2].plannedQuantity = 1;

    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

    // WebSocket Setup
    if (serverURL.length() > 0) {
      if (useHTTPS) {
        webSocket.beginSSL(serverIP, serverPort, "/ws");
      } else {
        webSocket.begin(serverIP, serverPort, "/ws");
      }
      webSocket.onEvent(webSocketEvent);
      webSocket.setReconnectInterval(5000);
    }


    Serial.print("Syncing NTP time");
    int ntpRetries = 0;
    while (ntpRetries < 10) {
      struct tm timeinfo;
      if (getLocalTime(&timeinfo)) {
        Serial.println(" - Success!");
        break;
      }
      delay(500);
      Serial.print(".");
      ntpRetries++;
    }
    processEventQueue();
    fetchMachineConfig();
    lastConfigFetch = millis();
  } else {
    Serial.println("\n✗ WiFi connection failed. Events will be queued.");
  }

  Serial.println("\n✓ System ready! (3 Lines Monitoring)");
  Serial.println("Serial: STATUS | STARTALL PLAYALL | PAUSEALL | RESUMEALL | "
                 "ENDALL | FETCHCONFIG");

  // Send an immediate "ONLINE" signal to the dashboard for all lines (telemetry only)
  if (WiFi.status() == WL_CONNECTED) {
    fetchMachineConfig();
    for (int i = 0; i < NUM_LINES; i++) {
      logEvent(i, "ONLINE", "IDLE", false);
    }
  }

  Serial.println("========================================\n");
}

// ============================================================================
// MAIN LOOP
// ============================================================================
void loop() {
  // Check for Serial commands
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toUpperCase();

    if (command == "STATUS" || command == "S") {
      for (int i = 0; i < NUM_LINES; i++) {
        const char *st =
            lines[i].currentState == IDLE         ? "IDLE"
            : lines[i].currentState == READY      ? "READY"
            : lines[i].currentState == RUNNING    ? "RUNNING"
            : lines[i].currentState == PAUSED     ? "PAUSED"
            : lines[i].currentState == PROCUREMENT ? "PROCUREMENT"
                                                  : "?";
        Serial.printf(
            "%s: Batch #%d | %s | exp %lds | qty %d\n", lineNames[i].c_str(),
            lines[i].batchNumber, st, lines[i].expectedDurationSec,
            lines[i].plannedQuantity);
      }
    } else if (command == "STARTALL" || command == "PLAYALL") {
      unifiedPlayAll();
    } else if (command == "PAUSEALL") {
      for (int i = 0; i < NUM_LINES; i++) {
        if (lines[i].currentState == RUNNING)
          pauseBatch(i);
      }
    } else if (command == "RESUMEALL") {
      for (int i = 0; i < NUM_LINES; i++) {
        if (lines[i].currentState == PAUSED)
          resumeBatch(i);
      }
    } else if (command == "ENDALL") {
      for (int i = 0; i < NUM_LINES; i++) {
        if (lines[i].currentState != IDLE)
          endBatch(i);
      }
    } else if (command == "FETCHCONFIG") {
      fetchMachineConfig();
    }
  }

  // WiFi & Queue Management
  if (WiFi.status() != WL_CONNECTED) {
    reconnectWiFi();
  } else {
    webSocket.loop();
    processEventQueue();
    checkRemoteCommands();
    updatePeriodicStatus();
    if (millis() - lastConfigFetch >= CONFIG_FETCH_INTERVAL_MS) {
      lastConfigFetch = millis();
      fetchMachineConfig();
    }
  }

  checkStageTargets();

  // Handle Buttons for all lines
  for (int i = 0; i < NUM_LINES; i++) {
    handleButton(i);
  }
}

void handleButton(int idx) {
  LineData &line = lines[idx];
  int val = digitalRead(BUTTON_PINS[idx]);

  if (val == LOW && !line.pressed) {
    line.pressed = true;
    line.pressedTime = millis();
  }

  if (val == HIGH && line.pressed) {
    line.pressed = false;
    line.releasedTime = millis();
    unsigned long pressDuration = line.releasedTime - line.pressedTime;

    if (pressDuration < BUTTON_DEBOUNCE_MS) {
      return;
    }

    if (pressDuration > LONG_PRESS_MS) {
      endBatch(idx);
      line.clickCount = 0;
      return;
    }

    line.clickCount++;
    line.lastPress = millis();
  }

  if (line.clickCount > 0 && millis() - line.lastPress > DOUBLE_CLICK_MS) {
    int clicks = line.clickCount;
    line.clickCount = 0;

    if (clicks == 1) {
      // Single click = PLAY (start new job or resume after pause)
      unifiedPlay(idx);
    } else if (clicks == 2) {
      // Double Click
      if (line.currentState == RUNNING)
        pauseBatch(idx);
    } else if (clicks == 3) {
      // Triple Click
      if (line.currentState == IDLE)
        startProcurement(idx);
    }
  }
}

void updateLEDs(int idx) {
  LineData &line = lines[idx];
  // Green = running | Yellow = paused / ready / procurement | Red = idle (no job)
  const bool green = line.currentState == RUNNING;
  const bool yellow = line.currentState == PAUSED || line.currentState == READY ||
                      line.currentState == PROCUREMENT;
  const bool red = line.currentState == IDLE;
  digitalWrite(GREEN_LEDS[idx], green ? HIGH : LOW);
  digitalWrite(YELLOW_LEDS[idx], yellow ? HIGH : LOW);
  digitalWrite(RED_LEDS[idx], red ? HIGH : LOW);
}

void unifiedPlay(int idx) {
  if (lines[idx].currentState == PAUSED) {
    resumeBatch(idx);
  } else if (lines[idx].currentState == IDLE ||
             lines[idx].currentState == PROCUREMENT ||
             lines[idx].currentState == READY) {
    startBatch(idx);
  }
}

void unifiedPlayAll() {
  for (int i = 0; i < NUM_LINES; i++) {
    if (lines[i].currentState == PAUSED)
      resumeBatch(i);
  }
  unsigned long syncT = millis();
  for (int i = 0; i < NUM_LINES; i++) {
    if (lines[i].currentState == IDLE || lines[i].currentState == READY ||
        lines[i].currentState == PROCUREMENT)
      startBatchAt(i, syncT);
  }
}

void startBatch(int idx) { startBatchAt(idx, millis()); }

void startBatchAt(int idx, unsigned long batchStartMillis) {
  if (lines[idx].currentState != IDLE && lines[idx].currentState != PROCUREMENT &&
      lines[idx].currentState != READY)
    return;

  long leadTimeS = 0;
  if (lines[idx].currentState == PROCUREMENT &&
      lines[idx].procurementStartTime > 0) {
    leadTimeS = (millis() - lines[idx].procurementStartTime) / 1000;
  }

  lines[idx].batchNumber++;
  saveBatchNumbers();

  lines[idx].batchStartTime = batchStartMillis;
  lines[idx].totalPausedTime = 0;
  lines[idx].pauseCount = 0;
  lines[idx].batchEndTime = 0;
  lines[idx].lastStatusUpdate = millis();
  lines[idx].currentState = RUNNING;
  lines[idx].stageTargetLogged = false;

  updateLEDs(idx);
  Serial.printf("\n🚀 [%s] Starting Batch #%d (Lead Time: %ld s)\n",
                lineNames[idx].c_str(), lines[idx].batchNumber, leadTimeS);

  logEvent(idx, "START", "RUNNING");
}

void pauseBatch(int idx) {
  if (lines[idx].currentState != RUNNING)
    return;

  lines[idx].pauseStartTime = millis();
  lines[idx].currentState = PAUSED;
  lines[idx].pauseCount++;

  updateLEDs(idx);
  Serial.printf("⏸ [%s] Pausing Batch #%d\n", lineNames[idx].c_str(),
                lines[idx].batchNumber);
  logEvent(idx, "PAUSE", "PAUSED");
}

void resumeBatch(int idx) {
  if (lines[idx].currentState != PAUSED)
    return;

  lines[idx].totalPausedTime += millis() - lines[idx].pauseStartTime;
  lines[idx].currentState = RUNNING;

  updateLEDs(idx);
  Serial.printf("▶ [%s] Resuming Batch #%d\n", lineNames[idx].c_str(),
                lines[idx].batchNumber);
  logEvent(idx, "RESUME", "RUNNING");
}

void endBatch(int idx) {
  if (lines[idx].currentState == IDLE)
    return;

  if (lines[idx].currentState == PAUSED) {
    lines[idx].totalPausedTime += millis() - lines[idx].pauseStartTime;
  }

  lines[idx].batchEndTime = millis();
  lines[idx].currentState = IDLE;
  lines[idx].procurementStartTime = 0; // Reset lead timer

  updateLEDs(idx);
  unsigned long duration =
      (lines[idx].batchEndTime - lines[idx].batchStartTime) / 1000;
  Serial.printf("✅ [%s] Ending Batch #%d (Duration: %lu s)\n",
                lineNames[idx].c_str(), lines[idx].batchNumber, duration);
  logEvent(idx, "END", "STOPPED");
}

void startProcurement(int idx) {
  if (lines[idx].currentState != IDLE)
    return;

  lines[idx].currentState = PROCUREMENT;
  lines[idx].procurementStartTime = millis();

  updateLEDs(idx);
  Serial.printf(
      "📦 [%s] Entering PROCUREMENT Mode (Lead Time tracking started)\n",
      lineNames[idx].c_str());
  logEvent(idx, "PROCUREMENT", "WAITING");
}

// ============================================================================
// NETWORKING FUNCTIONS
// ============================================================================

void webSocketEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
  case WStype_DISCONNECTED:
    Serial.println("[WSc] Disconnected!");
    break;
  case WStype_CONNECTED:
    Serial.printf("[WSc] Connected to url: %s\n", payload);
    fetchMachineConfig();
    break;
  case WStype_TEXT: {
    String msg = String((char *)payload);
    Serial.printf("[WSc] Received text: %s\n", msg.c_str());
    if (msg.indexOf("_status") >= 0 || msg.indexOf("\"commands\"") >= 0) {
      applyConfigFromPayload(msg);
    }
    dispatchRemotePayload(msg);
  } break;
  }
}

void logEvent(int idx, const String &evt, const String &status,
              bool queueIfOffline) {
  String jsonData = buildJSONPayload(idx, evt, status);

  Serial.printf("📤 [%s] Pushing %s (Batch #%d)...\n", lineNames[idx].c_str(),
                evt.c_str(), lines[idx].batchNumber);

  // HTTP first — response body includes door_status / frame_status / arch_status for LEDs
  bool okHttp = false;
  if (WiFi.status() == WL_CONNECTED) {
    okHttp = sendHTTPRequest(jsonData);
    if (okHttp) {
      Serial.printf("✓ [%s] Logged via HTTP (LEDs synced)\n", lineNames[idx].c_str());
    }
  }

  // WebSocket notify (dashboard live); config still comes from HTTP body or /api/machine-config
  if (WiFi.status() == WL_CONNECTED) {
    if (webSocket.sendTXT(jsonData)) {
      Serial.printf("⚡ [%s] Also sent via WebSocket\n", lineNames[idx].c_str());
    }
    if (!okHttp) {
      fetchMachineConfig();
    }
  }

  if (!okHttp && queueIfOffline) {
    queueEvent(jsonData);
  }
}

bool sendHTTPRequest(const String &jsonData, int retries) {
  if (WiFi.status() != WL_CONNECTED)
    return false;

  WiFiClientSecure client;
  if (useHTTPS) client.setInsecure();
  
  HTTPClient http;
  if (useHTTPS) {
    http.begin(client, serverURL);
  } else {
    http.begin(serverURL);
  }
  
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(HTTP_TIMEOUT);
  // Railway and other hosts may 301 HTTP→HTTPS; must follow or buttons appear dead.
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);


  for (int attempt = 0; attempt < retries; attempt++) {
    int code = http.POST(jsonData);

    // Flask server returns 200 on success
    if (code == HTTP_CODE_OK || code == 200) {
      String response = http.getString();
      if (response.length() > 0) {
        Serial.printf("  Response: %s\n", response.substring(0, 120).c_str());
        applyConfigFromPayload(response);
      }
      http.end();
      return true;
    }

    Serial.printf("  HTTP Error: %d (attempt %d/%d)\n", code, attempt + 1,
                  retries);

    if (attempt < retries - 1) {
      delay(500 * (attempt + 1)); // Exponential backoff
    }
  }

  http.end();
  return false;
}

void queueEvent(const String &jsonData) {
  if (queueCount >= MAX_QUEUE_SIZE) {
    Serial.println("⚠ Event queue full! Dropping oldest event.");
    // Remove oldest event (circular buffer)
    queueHead = (queueHead + 1) % MAX_QUEUE_SIZE;
    queueCount--;
  }

  eventQueue[queueTail].jsonData = jsonData;
  eventQueue[queueTail].isValid = true;
  queueTail = (queueTail + 1) % MAX_QUEUE_SIZE;
  queueCount++;

  Serial.printf("  Queued event (%d in queue)\n", queueCount);
}

void processEventQueue() {
  if (queueCount == 0 || WiFi.status() != WL_CONNECTED)
    return;

  int processed = 0;
  const int MAX_PER_LOOP = 3; // Process max 3 at a time to avoid blocking

  while (queueCount > 0 && processed < MAX_PER_LOOP) {
    if (sendHTTPRequest(eventQueue[queueHead].jsonData)) {
      eventQueue[queueHead].isValid = false;
      eventQueue[queueHead].jsonData = ""; // Free memory
      queueHead = (queueHead + 1) % MAX_QUEUE_SIZE;
      queueCount--;
      processed++;
      Serial.printf("✓ Processed queued event (%d remaining)\n", queueCount);
    } else {
      Serial.printf("✗ Failed to send queued event, will retry later\n");
      break; // Stop if send fails
    }
    delay(100); // Small delay between sends
  }

  if (processed > 0) {
    Serial.printf("📊 Queue: %d processed, %d remaining\n", processed,
                  queueCount);
  }
}

void updatePeriodicStatus() {
  for (int i = 0; i < NUM_LINES; i++) {
    if (lines[i].currentState == RUNNING &&
        (millis() - lines[i].lastStatusUpdate >= STATUS_UPDATE_INTERVAL)) {
      lines[i].lastStatusUpdate = millis();
      logEvent(i, "STATUS", "RUNNING", true);
    }
  }
}

void checkRemoteCommands() {
  if (millis() - lastCommandCheck < COMMAND_CHECK_INTERVAL)
    return;
  if (WiFi.status() != WL_CONNECTED)
    return;

  lastCommandCheck = millis();

  WiFiClientSecure client;
  if (useHTTPS) client.setInsecure();
  
  HTTPClient http;
  if (useHTTPS) {
    http.begin(client, commandURL);
  } else {
    http.begin(commandURL);
  }
  http.setTimeout(2000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);

  int code = http.GET();

  if (code == HTTP_CODE_OK || code == 200) {
    String response = http.getString();
    dispatchRemotePayload(response);
  }
  http.end();
}

static bool parseJsonStringCommand(const String &response, String *cmdOut) {
  *cmdOut = "";
  if (response.indexOf("\"PLAY\"") >= 0 || response.indexOf("\"START\"") >= 0)
    *cmdOut = "START";
  else if (response.indexOf("\"PAUSE\"") >= 0)
    *cmdOut = "PAUSE";
  else if (response.indexOf("\"RESUME\"") >= 0)
    *cmdOut = "RESUME";
  else if (response.indexOf("\"END\"") >= 0)
    *cmdOut = "END";
  return cmdOut->length() > 0;
}

static bool parseLineTarget(const String &response, int *lineIdxOut) {
  if (response.indexOf("\"line\":\"all\"") >= 0 ||
      response.indexOf("\"line\": \"all\"") >= 0) {
    *lineIdxOut = -1;
    return true;
  }
  for (int i = 0; i < NUM_LINES; i++) {
    String a = "\"line\":\"" + lineNames[i] + "\"";
    String b = "\"line\": \"" + lineNames[i] + "\"";
    if (response.indexOf(a) >= 0 || response.indexOf(b) >= 0) {
      *lineIdxOut = i;
      return true;
    }
  }
  return false;
}

void applyRemoteCommandForLine(int idx, const String &command) {
  if (idx < 0 || idx >= NUM_LINES)
    return;
  if (command == "START")
    unifiedPlay(idx);
  else if (command == "PAUSE")
    pauseBatch(idx);
  else if (command == "RESUME")
    resumeBatch(idx);
  else if (command == "END")
    endBatch(idx);
}

static bool extractQuotedCommandAfterKey(const String &payload, int searchFrom,
                                         const String &key, String *cmdOut) {
  int p = payload.indexOf(key, searchFrom);
  if (p < 0)
    return false;
  p += key.length();
  while (p < (int)payload.length() &&
         (payload.charAt(p) == ' ' || payload.charAt(p) == '\t'))
    p++;
  if (p >= (int)payload.length())
    return false;
  if (payload.charAt(p) == '"') {
    int end = payload.indexOf('"', p + 1);
    if (end < 0)
      return false;
    *cmdOut = payload.substring(p + 1, end);
    return cmdOut->length() > 0;
  }
  int end = p;
  while (end < (int)payload.length() && payload.charAt(end) != ',' &&
         payload.charAt(end) != '}' && payload.charAt(end) != ']')
    end++;
  *cmdOut = payload.substring(p, end);
  cmdOut->trim();
  if (*cmdOut == "null")
    cmdOut = "";
  return cmdOut->length() > 0;
}

static bool extractJsonStringAfterKey(const String &payload, const String &key,
                                      String *out) {
  *out = "";
  int p = payload.indexOf(key);
  if (p < 0)
    return false;
  p += key.length();
  if (p >= (int)payload.length() || payload.charAt(p) != '"')
    return false;
  int end = payload.indexOf('"', p + 1);
  if (end < 0)
    return false;
  *out = payload.substring(p + 1, end);
  return true;
}

String urlEncodeMachineName(const String &name) {
  String out = name;
  out.replace(" ", "%20");
  out.replace("#", "%23");
  out.replace("&", "%26");
  return out;
}

void buildServerEndpoints() {
  serverURL = "";
  commandURL = "";
  configURL = "";

  String host = String(serverIP);
  host.trim();
  if (host.length() == 0) {
    Serial.println("✗ serverIP is empty — set PUBLIC_URL on backend or edit batch.ino");
    return;
  }

  const String encodedMachine = urlEncodeMachineName(machineName);
  const int port = serverPort > 0 ? serverPort : (useHTTPS ? 443 : 80);

  if (useHTTPS) {
    serverURL = "https://" + host + "/data";
    commandURL = "https://" + host + "/api/command?line=all";
    configURL = "https://" + host + "/api/machine-config?machine=" + encodedMachine;
  } else {
    serverURL = "http://" + host + ":" + String(port) + "/data";
    commandURL = "http://" + host + ":" + String(port) + "/api/command?line=all";
    configURL = "http://" + host + ":" + String(port) + "/api/machine-config?machine=" +
                encodedMachine;
  }
}

void resetStageTimerFromServer(int idx) {
  if (idx < 0 || idx >= NUM_LINES)
    return;
  LineData &line = lines[idx];
  if (line.currentState != RUNNING && line.currentState != PAUSED)
    return;
  line.batchStartTime = millis();
  line.totalPausedTime = 0;
  line.pauseStartTime = 0;
  line.stageTargetLogged = false;
  line.lastStatusUpdate = millis();
  Serial.printf("🔄 [%s] Stage timer synced from dashboard (stage %d)\n",
                lineNames[idx].c_str(), line.serverStageIndex);
}

void syncStageFromServerConfig(int idx, const String &payload) {
  if (idx < 0 || idx >= NUM_LINES)
    return;
  LineData &line = lines[idx];

  String stageKey;
  String orderId;
  String machine;
  String laneStatus;
  String stageKeyA = "\"" + lineNames[idx] + "_stage_key\":\"";
  String stageKeyB = "\"" + lineNames[idx] + "_stage_key\": \"";
  String orderKeyA = "\"" + lineNames[idx] + "_order_id\":\"";
  String orderKeyB = "\"" + lineNames[idx] + "_order_id\": \"";
  String machineKeyA = "\"" + lineNames[idx] + "_machine\":\"";
  String machineKeyB = "\"" + lineNames[idx] + "_machine\": \"";
  String statusKeyA = "\"" + lineNames[idx] + "_status\":\"";
  String statusKeyB = "\"" + lineNames[idx] + "_status\": \"";
  String stageIdxKey = "\"" + lineNames[idx] + "_stage_index\":";

  if (!extractJsonStringAfterKey(payload, stageKeyA, &stageKey))
    extractJsonStringAfterKey(payload, stageKeyB, &stageKey);
  if (!extractJsonStringAfterKey(payload, orderKeyA, &orderId))
    extractJsonStringAfterKey(payload, orderKeyB, &orderId);
  if (!extractJsonStringAfterKey(payload, machineKeyA, &machine))
    extractJsonStringAfterKey(payload, machineKeyB, &machine);
  if (!extractJsonStringAfterKey(payload, statusKeyA, &laneStatus))
    extractJsonStringAfterKey(payload, statusKeyB, &laneStatus);

  int p = payload.indexOf(stageIdxKey);
  if (p >= 0) {
    p += stageIdxKey.length();
    line.serverStageIndex = payload.substring(p).toInt();
  }

  if (orderId.length() > 0)
    line.serverOrderId = orderId;
  if (machine.length() > 0)
    line.serverMachine = machine;
  if (laneStatus.length() > 0)
    line.serverLaneStatus = laneStatus;

  if (stageKey.length() == 0)
    return;

  if (line.lastServerStageKey.length() > 0 && stageKey != line.lastServerStageKey) {
    resetStageTimerFromServer(idx);
  }
  line.lastServerStageKey = stageKey;
}

/** Map dashboard lane status → local state + LEDs (idempotent; always refreshes LEDs). */
void applyDashboardLaneStatus(int idx, const String &laneStatus) {
  if (idx < 0 || idx >= NUM_LINES)
    return;
  String st = laneStatus;
  st.trim();
  st.toUpperCase();
  if (st.length() == 0)
    return;

  LineData &line = lines[idx];
  const State prev = line.currentState;
  State next = IDLE;

  if (st == "RUNNING")
    next = RUNNING;
  else if (st == "PAUSED")
    next = PAUSED;
  else if (st == "READY")
    next = READY;
  else if (st == "WAITING" || st == "PROCUREMENT")
    next = PROCUREMENT;
  else
    next = IDLE; // STOPPED, FINISHED, IDLE, unknown

  if (next == prev) {
    updateLEDs(idx);
    return;
  }

  if (next == RUNNING) {
    if (prev == PAUSED)
      line.totalPausedTime += millis() - line.pauseStartTime;
    else if (prev == IDLE || prev == READY || prev == PROCUREMENT) {
      line.batchStartTime = millis();
      line.totalPausedTime = 0;
      line.pauseStartTime = 0;
      line.batchEndTime = 0;
      line.stageTargetLogged = false;
    }
  } else if (next == PAUSED) {
    if (prev == RUNNING)
      line.pauseStartTime = millis();
  } else if (next == IDLE) {
    if (prev == PAUSED)
      line.totalPausedTime += millis() - line.pauseStartTime;
    if (prev == RUNNING || prev == PAUSED)
      line.batchEndTime = millis();
    line.procurementStartTime = 0;
  }

  line.currentState = next;
  updateLEDs(idx);
  Serial.printf("💡 [%s] LED sync ← dashboard %s\n", lineNames[idx].c_str(),
                st.c_str());
}

void applyConfigFromPayload(const String &payload) {
  for (int i = 0; i < NUM_LINES; i++) {
    String expKey = "\"" + lineNames[i] + "_expected_s\":";
    String qtyKey = "\"" + lineNames[i] + "_qty\":";
    int p = payload.indexOf(expKey);
    if (p >= 0) {
      p += expKey.length();
      long v = payload.substring(p).toInt();
      if (v >= 0 && v < 8640000L)
        lines[i].expectedDurationSec = v;
    }
    p = payload.indexOf(qtyKey);
    if (p >= 0) {
      p += qtyKey.length();
      int q = payload.substring(p).toInt();
      if (q > 0 && q < 100000)
        lines[i].plannedQuantity = q;
    }
    syncStageFromServerConfig(i, payload);

    String laneStatus;
    String statusKeyA = "\"" + lineNames[i] + "_status\":\"";
    String statusKeyB = "\"" + lineNames[i] + "_status\": \"";
    if (extractJsonStringAfterKey(payload, statusKeyA, &laneStatus) ||
        extractJsonStringAfterKey(payload, statusKeyB, &laneStatus)) {
      applyDashboardLaneStatus(i, laneStatus);
    }
  }
}

void dispatchRemotePayload(const String &payload) {
  applyConfigFromPayload(payload);

  int listStart = payload.indexOf("\"command_list\"");
  if (listStart >= 0) {
    bool any = false;
    for (int i = 0; i < NUM_LINES; i++) {
      String lineKey = "\"line\":\"" + lineNames[i] + "\"";
      String lineKeySp = "\"line\": \"" + lineNames[i] + "\"";
      int pos = payload.indexOf(lineKey, listStart);
      if (pos < 0)
        pos = payload.indexOf(lineKeySp, listStart);
      if (pos < 0)
        continue;
      String cmd;
      if (extractQuotedCommandAfterKey(payload, pos, "\"command\":", &cmd) ||
          extractQuotedCommandAfterKey(payload, pos, "\"command\": ", &cmd)) {
        Serial.printf("📥 Remote Command (list %s): %s\n", lineNames[i].c_str(),
                      cmd.c_str());
        applyRemoteCommandForLine(i, cmd);
        any = true;
      }
    }
    if (any)
      return;
  }

  int cmdBlock = payload.indexOf("\"commands\"");
  if (cmdBlock >= 0) {
    bool any = false;
    for (int i = 0; i < NUM_LINES; i++) {
      String cmd;
      String keyA = "\"" + lineNames[i] + "\":\"";
      String keyB = "\"" + lineNames[i] + "\": \"";
      if (extractQuotedCommandAfterKey(payload, cmdBlock, keyA, &cmd) ||
          extractQuotedCommandAfterKey(payload, cmdBlock, keyB, &cmd)) {
        Serial.printf("📥 Remote Command (map %s): %s\n", lineNames[i].c_str(),
                      cmd.c_str());
        applyRemoteCommandForLine(i, cmd);
        any = true;
      }
    }
    if (any)
      return;
  }

  String cmd;
  if (!parseJsonStringCommand(payload, &cmd))
    return;

  int lineSpec = 0;
  if (parseLineTarget(payload, &lineSpec)) {
    if (lineSpec == -1) {
      Serial.printf("📥 Remote Command (all lanes): %s\n", cmd.c_str());
      if (cmd == "START")
        unifiedPlayAll();
      else if (cmd == "PAUSE") {
        for (int i = 0; i < NUM_LINES; i++) {
          if (lines[i].currentState == RUNNING)
            pauseBatch(i);
        }
      } else if (cmd == "RESUME") {
        for (int i = 0; i < NUM_LINES; i++) {
          if (lines[i].currentState == PAUSED)
            resumeBatch(i);
        }
      } else if (cmd == "END") {
        for (int i = 0; i < NUM_LINES; i++) {
          if (lines[i].currentState != IDLE)
            endBatch(i);
        }
      }
      return;
    }
    Serial.printf("📥 Remote Command for %s: %s\n", lineNames[lineSpec].c_str(),
                  cmd.c_str());
    applyRemoteCommandForLine(lineSpec, cmd);
    return;
  }

  for (int i = 0; i < NUM_LINES; i++) {
    if (payload.indexOf(lineNames[i]) >= 0) {
      Serial.printf("📥 Remote Command (legacy match %s): %s\n",
                    lineNames[i].c_str(), cmd.c_str());
      applyRemoteCommandForLine(i, cmd);
      break;
    }
  }
}

void fetchMachineConfig() {
  if (WiFi.status() != WL_CONNECTED || configURL.length() == 0)
    return;

  WiFiClientSecure client;
  if (useHTTPS)
    client.setInsecure();

  HTTPClient http;
  if (useHTTPS) {
    http.begin(client, configURL);
  } else {
    http.begin(configURL);
  }
  http.setTimeout(HTTP_TIMEOUT);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);

  int code = http.GET();
  if (code != HTTP_CODE_OK && code != 200) {
    Serial.printf("✗ Config fetch HTTP %d\n", code);
    http.end();
    return;
  }

  String body = http.getString();
  http.end();
  applyConfigFromPayload(body);
  Serial.printf(
      "✓ Machine config refreshed (%s exp %lds stage %d)\n",
      lineNames[0].c_str(), lines[0].expectedDurationSec, lines[0].serverStageIndex);
}

void checkStageTargets() {
  unsigned long now = millis();
  for (int i = 0; i < NUM_LINES; i++) {
    LineData &line = lines[i];
    if (line.currentState != RUNNING || line.expectedDurationSec <= 0 ||
        line.stageTargetLogged)
      continue;
    unsigned long elapsed = now - line.batchStartTime;
    long activeS =
        (elapsed > line.totalPausedTime) ? (elapsed - line.totalPausedTime) / 1000 : 0;
    if (activeS >= line.expectedDurationSec) {
      line.stageTargetLogged = true;
      Serial.printf("⏱ [%s] Expected stage time reached (%ld s)\n",
                    lineNames[i].c_str(), line.expectedDurationSec);
      logEvent(i, "STAGE_TARGET", "RUNNING");
    }
  }
}

void reconnectWiFi() {
  static unsigned long lastReconnectAttempt = 0;
  static int reconnectDelay = 1000;

  if (millis() - lastReconnectAttempt < reconnectDelay)
    return;

  lastReconnectAttempt = millis();
  Serial.println("🔄 Attempting WiFi reconnection...");

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    Serial.printf("  Reconnecting to %s...\n", ssid);
    reconnectDelay = (reconnectDelay * 2 < 30000) ? reconnectDelay * 2
                                                  : 30000; // Max 30s delay
  } else {
    reconnectDelay = 1000; // Reset on success
    Serial.println("✓ WiFi reconnected!");
    buildServerEndpoints();
    fetchMachineConfig();
    processEventQueue();
  }
}
String buildJSONPayload(int idx, const String &evt, const String &status) {
  LineData &line = lines[idx];
  unsigned long now = millis();
  unsigned long elapsedTime =
      (line.batchStartTime > 0) ? (now - line.batchStartTime) : 0;
  long activeRuntimeSecs = (elapsedTime > line.totalPausedTime)
                               ? (elapsedTime - line.totalPausedTime) / 1000
                               : 0;
  long totalElapsedSecs = elapsedTime / 1000;
  long accumulatedPauseSecs = line.totalPausedTime / 1000;
  long rssi = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : -100;

  long batchDurationSecs = 0;
  float efficiency = 0.0;
  float productionRate = 0.0;

  if (evt == "END" && line.batchEndTime > 0 && line.batchStartTime > 0) {
    unsigned long totalBatchTime = line.batchEndTime - line.batchStartTime;
    batchDurationSecs = totalBatchTime / 1000;
    efficiency = calculateEfficiency(totalBatchTime - line.totalPausedTime,
                                     totalBatchTime);
    activeRuntimeSecs = (totalBatchTime - line.totalPausedTime) / 1000;
    totalElapsedSecs = batchDurationSecs;
    if (batchDurationSecs > 0)
      productionRate = 3600.0 / batchDurationSecs;
  } else if (line.currentState == RUNNING || line.currentState == PAUSED) {
    efficiency =
        calculateEfficiency(elapsedTime - line.totalPausedTime, elapsedTime);
    if (activeRuntimeSecs > 0)
      productionRate = 3600.0 / activeRuntimeSecs;
  }

  // Lead Time calculation for START events
  long leadTimeS = 0;
  if (evt == "START" && line.procurementStartTime > 0) {
    leadTimeS = (millis() - line.procurementStartTime) / 1000;
  }

  const char *wifiQuality = (rssi > -50)   ? "Excellent"
                            : (rssi > -60) ? "Good"
                            : (rssi > -70) ? "Fair"
                                           : "Poor";

  long expectedRemS = 0;
  long varianceS = 0;
  if (line.expectedDurationSec > 0) {
    if (evt == "END") {
      varianceS = activeRuntimeSecs - line.expectedDurationSec;
      expectedRemS = 0;
    } else if (line.currentState == RUNNING || line.currentState == PAUSED) {
      expectedRemS = line.expectedDurationSec - activeRuntimeSecs;
      if (expectedRemS < 0)
        expectedRemS = 0;
      varianceS = activeRuntimeSecs - line.expectedDurationSec;
    }
  }

  char buffer[1600];
  LineData &lineRef = lines[idx];
  const char *dashOrder =
      lineRef.serverOrderId.length() > 0 ? lineRef.serverOrderId.c_str() : "";
  int dashStage =
      lineRef.serverStageIndex >= 0 ? lineRef.serverStageIndex : -1;
  const char *dashMachine =
      lineRef.serverMachine.length() > 0 ? lineRef.serverMachine.c_str() : "";

  snprintf(buffer, sizeof(buffer),
           "{"
           "\"timestamp\":\"%s\","
           "\"batch_id\":%d,"
           "\"event\":\"%s\","
           "\"status\":\"%s\","
           "\"machine\":\"%s\","
           "\"line\":\"%s\","
           "\"active_runtime_s\":%ld,"
           "\"total_elapsed_s\":%ld,"
           "\"total_pause_s\":%ld,"
           "\"lead_time_s\":%ld,"
           "\"pause_count\":%d,"
           "\"efficiency_percent\":%.2f,"
           "\"production_rate_per_hour\":%.2f,"
           "\"wifi_rssi\":%ld,"
           "\"wifi_quality\":\"%s\","
           "\"expected_duration_s\":%ld,"
           "\"planned_quantity\":%d,"
           "\"expected_remaining_s\":%ld,"
           "\"variance_s\":%ld,"
           "\"lane_sync_start_ms\":%lu,"
           "\"dashboard_order_id\":\"%s\","
           "\"dashboard_stage_index\":%d,"
           "\"dashboard_machine\":\"%s\""
           "}",
           getTimestamp().c_str(), line.batchNumber, evt.c_str(),
           status.c_str(), machineName.c_str(), lineNames[idx].c_str(),
           activeRuntimeSecs, totalElapsedSecs, accumulatedPauseSecs, leadTimeS,
           line.pauseCount, efficiency, productionRate, rssi, wifiQuality,
           line.expectedDurationSec, line.plannedQuantity, expectedRemS,
           varianceS, (unsigned long)line.batchStartTime, dashOrder, dashStage,
           dashMachine);

  return String(buffer);
}
// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

String getTimestamp() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    // Fallback: use millis() to create a relative timestamp if NTP not synced
    unsigned long seconds = millis() / 1000;
    unsigned long hours = seconds / 3600;
    unsigned long minutes = (seconds % 3600) / 60;
    unsigned long secs = seconds % 60;
    char buffer[30];
    snprintf(buffer, sizeof(buffer), "Uptime: %02lu:%02lu:%02lu", hours,
             minutes, secs);
    return String(buffer);
  }

  char buffer[30];
  strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", &timeinfo);
  return String(buffer);
}

String getFormattedTime(unsigned long millisTime) {
  unsigned long totalSeconds = millisTime / 1000;
  unsigned long hours = totalSeconds / 3600;
  unsigned long minutes = (totalSeconds % 3600) / 60;
  unsigned long seconds = totalSeconds % 60;

  char buffer[20];
  if (hours > 0) {
    snprintf(buffer, sizeof(buffer), "%02lu:%02lu:%02lu", hours, minutes,
             seconds);
  } else {
    snprintf(buffer, sizeof(buffer), "%02lu:%02lu", minutes, seconds);
  }
  return String(buffer);
}

float calculateEfficiency(unsigned long activeTime, unsigned long totalTime) {
  if (totalTime == 0)
    return 0.0;
  return (float(activeTime) / float(totalTime)) * 100.0;
}

void loadBatchNumbers() {
  for (int i = 0; i < NUM_LINES; i++) {
    int addr = EEPROM_BATCH_BASE_ADDR + (i * sizeof(int));
    EEPROM.get(addr, lines[i].batchNumber);
    if (lines[i].batchNumber < 0 || lines[i].batchNumber > 100000) {
      lines[i].batchNumber = 0;
    }
    Serial.printf("✓ [%s] Loaded batch number: %d\n", lineNames[i].c_str(),
                  lines[i].batchNumber);
  }
}

void saveBatchNumbers() {
  for (int i = 0; i < NUM_LINES; i++) {
    int addr = EEPROM_BATCH_BASE_ADDR + (i * sizeof(int));
    EEPROM.put(addr, lines[i].batchNumber);
  }
  EEPROM.commit();
}

void resetBatchNumber(int idx) {
  lines[idx].batchNumber = 0;
  saveBatchNumbers();
  Serial.printf("✓ [%s] Batch number reset\n", lineNames[idx].c_str());
}

void setBatchNumber(int idx, int newNumber) {
  if (newNumber >= 0 && newNumber <= 100000) {
    lines[idx].batchNumber = newNumber;
    saveBatchNumbers();
    Serial.printf("✓ [%s] Batch number set to: %d\n", lineNames[idx].c_str(),
                  lines[idx].batchNumber);
  }
}
