/*
 * ============================================================================
 * ESP32 Batch Tracking System
 * ============================================================================
 * Tracks batch processing with pause/resume functionality
 * Sends data to Flask server and saves to CSV
 * Features: Offline queue, auto-reconnect, periodic status updates
 * ============================================================================
 */

// ============================================================================
// INCLUDES
// ============================================================================
#include <EEPROM.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
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
#define MAX_QUEUE_SIZE 15           // Slightly increased for 3 lines

// EEPROM
#define EEPROM_SIZE 128 // Increased for 3 batch numbers
#define EEPROM_BATCH_BASE_ADDR                                                 \
  0 // Base address for batch numbers (int = 4 bytes each)

// WiFi Credentials
const char *ssid = "AnasAlsayed-2.4";
const char *password = "1234567890";

// Flask Server Configuration
const char *serverIP = "192.168.1.16";
const int serverPort = 5002;
String serverURL = "";

// NTP Configuration
const char *ntpServer = "pool.ntp.org";
const long gmtOffset_sec = 0;
const int daylightOffset_sec = 0;

// Machine Identifier
String machineName = "FAM-Hub-01";
String lineName = "door";
String lineNames[NUM_LINES] = {"door", "frame", "arch"};

// ============================================================================
// GLOBAL VARIABLES
// ============================================================================

enum State { IDLE, RUNNING, PAUSED, PROCUREMENT };

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
};

LineData lines[NUM_LINES];
unsigned long lastCommandCheck = 0;
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
String buildJSONPayload(int idx, const String &evt, const String &status);

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

    serverURL =
        "http://" + String(serverIP) + ":" + String(serverPort) + "/data";
    Serial.printf("  Server URL: %s\n", serverURL.c_str());

    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);

    // WebSocket Setup
    webSocket.begin(serverIP, serverPort, "/ws");
    webSocket.onEvent(webSocketEvent);
    webSocket.setReconnectInterval(5000);

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
  } else {
    Serial.println("\n✗ WiFi connection failed. Events will be queued.");
  }

  Serial.println("\n✓ System ready! (3 Lines Monitoring)");

  // Send an immediate "ONLINE" signal to the dashboard for all lines
  if (WiFi.status() == WL_CONNECTED) {
    for (int i = 0; i < NUM_LINES; i++) {
      logEvent(i, "BOOT", "IDLE");
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
        Serial.printf("%s: Batch #%d | State: %s\n", lineNames[i].c_str(),
                      lines[i].batchNumber,
                      lines[i].currentState == IDLE      ? "IDLE"
                      : lines[i].currentState == RUNNING ? "RUNNING"
                                                         : "PAUSED");
      }
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
  }

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
      // Single Click
      if (line.currentState == IDLE || line.currentState == PROCUREMENT)
        startBatch(idx);
      else if (line.currentState == PAUSED)
        resumeBatch(idx);
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
  digitalWrite(GREEN_LEDS[idx], line.currentState == RUNNING ? HIGH : LOW);
  digitalWrite(YELLOW_LEDS[idx],
               (line.currentState == PAUSED || line.currentState == PROCUREMENT)
                   ? HIGH
                   : LOW);
  digitalWrite(RED_LEDS[idx],
               (line.currentState == IDLE || line.currentState == PROCUREMENT)
                   ? HIGH
                   : LOW);
}

void startBatch(int idx) {
  if (lines[idx].currentState != IDLE && lines[idx].currentState != PROCUREMENT)
    return;

  // Calculate Lead Time if we were in PROCUREMENT mode
  long leadTimeS = 0;
  if (lines[idx].currentState == PROCUREMENT &&
      lines[idx].procurementStartTime > 0) {
    leadTimeS = (millis() - lines[idx].procurementStartTime) / 1000;
  }

  lines[idx].batchNumber++;
  saveBatchNumbers();

  lines[idx].batchStartTime = millis();
  lines[idx].totalPausedTime = 0;
  lines[idx].pauseCount = 0;
  lines[idx].batchEndTime = 0;
  lines[idx].lastStatusUpdate = millis();
  lines[idx].currentState = RUNNING;

  updateLEDs(idx);
  Serial.printf("\n🚀 [%s] Starting Batch #%d (Lead Time: %ld s)\n",
                lineNames[idx].c_str(), lines[idx].batchNumber, leadTimeS);

  // Create payload with lead time
  String jsonData = buildJSONPayload(idx, "START", "RUNNING");
  // Manually insert lead time into JSON before closing brace if needed, or
  // update buildJSONPayload
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
    break;
  case WStype_TEXT: {
    String msg = String((char *)payload);
    Serial.printf("[WSc] Received text: %s\n", msg.c_str());

    // Instant Remote Command Handling
    for (int i = 0; i < NUM_LINES; i++) {
      if (msg.indexOf(lineNames[i]) >= 0) {
        if (msg.indexOf("\"START\"") >= 0)
          startBatch(i);
        else if (msg.indexOf("\"PAUSE\"") >= 0)
          pauseBatch(i);
        else if (msg.indexOf("\"RESUME\"") >= 0)
          resumeBatch(i);
        else if (msg.indexOf("\"END\"") >= 0)
          endBatch(i);
      }
    }
  } break;
  }
}

void logEvent(int idx, const String &evt, const String &status,
              bool queueIfOffline) {
  String jsonData = buildJSONPayload(idx, evt, status);

  Serial.printf("📤 [%s] Pushing %s (Batch #%d)...\n", lineNames[idx].c_str(),
                evt.c_str(), lines[idx].batchNumber);

  // 1. Try WebSocket first (Instant)
  bool sentWS = false;
  if (WiFi.status() == WL_CONNECTED) {
    sentWS = webSocket.sendTXT(jsonData);
    if (sentWS) {
      Serial.printf("⚡ [%s] Sent via WebSocket\n", lineNames[idx].c_str());
    }
  }

  // 2. Try HTTP as fallback (Reliable/Database)
  if (WiFi.status() == WL_CONNECTED) {
    if (sendHTTPRequest(jsonData)) {
      Serial.printf("✓ [%s] Successfully logged via HTTP\n",
                    lineNames[idx].c_str());
      return;
    }
  }

  // 3. Queue if both failed
  if (queueIfOffline && !sentWS) {
    queueEvent(jsonData);
  }
}

bool sendHTTPRequest(const String &jsonData, int retries) {
  if (WiFi.status() != WL_CONNECTED)
    return false;

  HTTPClient http;
  http.begin(serverURL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(HTTP_TIMEOUT);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);

  for (int attempt = 0; attempt < retries; attempt++) {
    int code = http.POST(jsonData);

    // Flask server returns 200 on success
    if (code == HTTP_CODE_OK || code == 200) {
      // Read response for debugging
      String response = http.getString();
      if (response.length() > 0) {
        Serial.printf("  Response: %s\n", response.substring(0, 100).c_str());
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

  HTTPClient http;
  // Check commands for all lines at once
  String commandURL = "http://" + String(serverIP) + ":" + String(serverPort) +
                      "/api/command?line=all";
  http.begin(commandURL);
  http.setTimeout(2000);

  int code = http.GET();

  if (code == HTTP_CODE_OK || code == 200) {
    String response = http.getString();

    // Simple parsing for {"line": "Line_1", "command": "START"}
    for (int i = 0; i < NUM_LINES; i++) {
      if (response.indexOf(lineNames[i]) >= 0) {
        String command = "";
        if (response.indexOf("\"START\"") >= 0)
          command = "START";
        else if (response.indexOf("\"PAUSE\"") >= 0)
          command = "PAUSE";
        else if (response.indexOf("\"RESUME\"") >= 0)
          command = "RESUME";
        else if (response.indexOf("\"END\"") >= 0)
          command = "END";

        if (command != "") {
          Serial.printf("📥 Remote Command for %s: %s\n", lineNames[i].c_str(),
                        command.c_str());
          if (command == "START")
            startBatch(i);
          else if (command == "PAUSE")
            pauseBatch(i);
          else if (command == "RESUME")
            resumeBatch(i);
          else if (command == "END")
            endBatch(i);
        }
      }
    }
  }
  http.end();
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

  char buffer[1024];
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
           "\"wifi_quality\":\"%s\""
           "}",
           getTimestamp().c_str(), line.batchNumber, evt.c_str(),
           status.c_str(), machineName.c_str(), lineNames[idx].c_str(),
           activeRuntimeSecs, totalElapsedSecs, accumulatedPauseSecs, leadTimeS,
           line.pauseCount, efficiency, productionRate, rssi, wifiQuality);

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
