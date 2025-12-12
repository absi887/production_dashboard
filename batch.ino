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
#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <EEPROM.h>

// ============================================================================
// PIN DEFINITIONS
// ============================================================================
#define BUTTON 14

// ============================================================================
// CONSTANTS & CONFIGURATION
// ============================================================================

// Button timing
const int LONG_PRESS_MS = 1500;
const int DOUBLE_CLICK_MS = 300;

// HTTP & Network
#define MAX_RETRIES 3
#define HTTP_TIMEOUT 5000
#define STATUS_UPDATE_INTERVAL 60000  // Send status update every 60 seconds
#define COMMAND_CHECK_INTERVAL 2000   // Check for remote commands every 2 seconds
#define MAX_QUEUE_SIZE 10

// EEPROM
#define EEPROM_SIZE 64
#define EEPROM_BATCH_NUM_ADDR 0

// WiFi Credentials
const char* ssid = "AnasAlsayed-2.4";
const char* password = "1234567890";

// Flask Server Configuration
// Update with your computer's IP address (run ipconfig/ifconfig to find it)
const char* serverIP = "192.168.1.66";  // CHANGE THIS to your computer's IP
const int serverPort = 5002;
String serverURL = "";  // Will be constructed in setup()

// NTP Configuration
const char* ntpServer = "pool.ntp.org";
// Timezone offset in seconds (e.g., GMT+3 = 10800, GMT-5 = -18000)
// Adjust this to match your local timezone
const long gmtOffset_sec = 0;  // Change this: GMT+3 = 10800, GMT-5 = -18000, etc.
const int daylightOffset_sec = 0;  // Daylight saving time offset (usually 3600 or 0)

// Machine Identifier
String machineName = "Wood_Line_1";

// ============================================================================
// GLOBAL VARIABLES
// ============================================================================

// State Management
enum State { IDLE, RUNNING, PAUSED };
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
unsigned long lastCommandCheck = 0;
int pauseCount = 0;
int batchNumber = 0;  // Auto-incrementing batch ID

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

// Button Functions
void singlePress();
void doublePress();

// Batch State Functions
void startBatch();
void pauseBatch();
void resumeBatch();
void endBatch();

// Networking Functions
void logEvent(const String &evt, const String &status, bool queueIfOffline = true);
bool sendHTTPRequest(const String &jsonData, int retries = MAX_RETRIES);
void queueEvent(const String &jsonData);
void processEventQueue();
void updatePeriodicStatus();
void reconnectWiFi();
void checkRemoteCommands();
String buildJSONPayload(const String &evt, const String &status);

// Utility Functions
String getTimestamp();
String getFormattedTime(unsigned long millisTime);
float calculateEfficiency(unsigned long activeTime, unsigned long totalTime);
void loadBatchNumber();
void saveBatchNumber();
void resetBatchNumber();
void setBatchNumber(int newNumber);

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\n========================================");
  Serial.println("ESP32 Batch Tracking System Starting...");
  Serial.println("========================================\n");
  
  // Initialize Button
  pinMode(BUTTON, INPUT_PULLUP);

  // Initialize EEPROM and load batch number
  EEPROM.begin(EEPROM_SIZE);
  loadBatchNumber();
  
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
    Serial.printf("  RSSI: %d dBm\n", WiFi.RSSI());
    
    // Build server URL
    serverURL = "http://" + String(serverIP) + ":" + String(serverPort) + "/data";
    Serial.printf("  Server URL: %s\n", serverURL.c_str());
    
    // Configure NTP for timestamps
    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
    
    // Wait for NTP time to sync
    Serial.print("Syncing NTP time");
    int ntpRetries = 0;
    while (ntpRetries < 10) {
      struct tm timeinfo;
      if (getLocalTime(&timeinfo)) {
        Serial.println(" - Success!");
        char timeStr[30];
        strftime(timeStr, sizeof(timeStr), "%Y-%m-%d %H:%M:%S", &timeinfo);
        Serial.printf("  Current time: %s\n", timeStr);
        break;
      }
      delay(500);
      Serial.print(".");
      ntpRetries++;
    }
    if (ntpRetries >= 10) {
      Serial.println(" - Failed! Using system time.");
    }
    
    // Process any queued events from previous session
    processEventQueue();
  } else {
    Serial.println("\n✗ WiFi connection failed. Events will be queued.");
  }
  
  Serial.println("\n✓ System ready!");
  Serial.println("  Single press: Start/Resume batch");
  Serial.println("  Double press: Pause batch");
  Serial.println("  Long press: End batch");
  Serial.println("\n  Serial Commands:");
  Serial.println("    RESET or R    - Reset batch number to 0 (next will be #1)");
  Serial.println("    SET <number>  - Set batch number (e.g., SET 1)");
  Serial.println("    STATUS or S   - Show current status");
  Serial.println("    HELP or H     - Show help");
  Serial.println("========================================\n");
}

// ============================================================================
// MAIN LOOP
// ============================================================================
void loop() {
  // Check for Serial commands (for resetting batch number)
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toUpperCase();
    
    if (command == "RESET" || command == "R") {
      resetBatchNumber();
    } else if (command.startsWith("SET ")) {
      int newNum = command.substring(4).toInt();
      setBatchNumber(newNum);
    } else if (command == "STATUS" || command == "S") {
      Serial.printf("Current batch number: %d\n", batchNumber);
      Serial.printf("Current state: %s\n", 
        currentState == IDLE ? "IDLE" : 
        currentState == RUNNING ? "RUNNING" : "PAUSED");
    } else if (command == "HELP" || command == "H") {
      Serial.println("\n=== Available Commands ===");
      Serial.println("RESET or R    - Reset batch number to 0 (next will be #1)");
      Serial.println("SET <number>  - Set batch number to specific value");
      Serial.println("STATUS or S   - Show current status");
      Serial.println("HELP or H     - Show this help");
      Serial.println("========================");
    }
  }
  
  // WiFi Management
  if (WiFi.status() != WL_CONNECTED) {
    reconnectWiFi();
  } else {
    // Process queued events when online
    processEventQueue();
    
    // Check for remote commands from dashboard
    checkRemoteCommands();
    
    // Send periodic status updates during active batches
    if (currentState == RUNNING) {
      updatePeriodicStatus();
    }
  }
  
  // Button Input Handling
  int val = digitalRead(BUTTON);

  // Detect button press
  if (val == LOW && !pressed) {
    pressed = true;
    pressedTime = millis();
  }

  // Detect button release
  if (val == HIGH && pressed) {
    pressed = false;
    releasedTime = millis();
    unsigned long pressDuration = releasedTime - pressedTime;

    // Long press detection
    if (pressDuration > LONG_PRESS_MS) {
      endBatch();
      waitForDouble = false;
      return;
    }

    // Double click detection
    if (millis() - lastPress < DOUBLE_CLICK_MS) {
      doublePress();
      waitForDouble = false;
    } else {
      waitForDouble = true;
    }
    lastPress = millis();
  }

  // Single click timeout
  if (waitForDouble && millis() - lastPress > DOUBLE_CLICK_MS) {
    waitForDouble = false;
    singlePress();
  }
}

// ============================================================================
// BUTTON HANDLING FUNCTIONS
// ============================================================================

void singlePress() {
  if (currentState == IDLE) {
    startBatch();
  } else if (currentState == PAUSED) {
    resumeBatch();
  }
}

void doublePress() {
  if (currentState == RUNNING) {
    pauseBatch();
  }
}

// ============================================================================
// BATCH STATE MANAGEMENT FUNCTIONS
// ============================================================================

void startBatch() {
  if (currentState != IDLE) return;
  
  batchNumber++;
  saveBatchNumber();  // Persist batch number
  
  batchStartTime = millis();
  totalPausedTime = 0;
  pauseCount = 0;
  batchEndTime = 0;
  lastStatusUpdate = millis();
  currentState = RUNNING;
  
  Serial.printf("\n🚀 Starting Batch #%d\n", batchNumber);
  logEvent("START", "RUNNING");
}

void pauseBatch() {
  if (currentState != RUNNING) return;
  
  pauseStartTime = millis();
  currentState = PAUSED;
  pauseCount++;
  
  Serial.printf("⏸ Pausing Batch #%d (Pause #%d)\n", batchNumber, pauseCount);
  logEvent("PAUSE", "PAUSED");
}

void resumeBatch() {
  if (currentState != PAUSED) return;
  
  totalPausedTime += millis() - pauseStartTime;
  currentState = RUNNING;
  
  Serial.printf("▶ Resuming Batch #%d\n", batchNumber);
  logEvent("RESUME", "RUNNING");
}

void endBatch() {
  if (currentState == IDLE) return;
  
  if (currentState == PAUSED) {
      totalPausedTime += millis() - pauseStartTime;
  }
  
  batchEndTime = millis();
  currentState = IDLE;
  
  unsigned long duration = (batchEndTime - batchStartTime) / 1000;
  Serial.printf("✅ Ending Batch #%d (Duration: %lu seconds)\n", batchNumber, duration);
  logEvent("END", "COMPLETED");
}

// ============================================================================
// NETWORKING FUNCTIONS
// ============================================================================

void logEvent(const String &evt, const String &status, bool queueIfOffline) {
  String jsonData = buildJSONPayload(evt, status);
  
  // Debug: Print JSON payload preview
  Serial.printf("📤 Sending %s (Batch #%d): %s...\n", evt.c_str(), batchNumber, jsonData.substring(0, 150).c_str());
  
  // Try to send immediately if online
  if (WiFi.status() == WL_CONNECTED) {
    if (sendHTTPRequest(jsonData)) {
      Serial.printf("✓ Successfully sent %s (Batch #%d)\n", evt.c_str(), batchNumber);
      return;
    } else {
      Serial.printf("✗ Failed to send %s (Batch #%d) - will queue\n", evt.c_str(), batchNumber);
    }
  } else {
    Serial.printf("⚠ WiFi offline - queuing %s (Batch #%d)\n", evt.c_str(), batchNumber);
  }
  
  // If offline or send failed, queue it
  if (queueIfOffline) {
    queueEvent(jsonData);
  }
}

bool sendHTTPRequest(const String &jsonData, int retries) {
  if (WiFi.status() != WL_CONNECTED) return false;
  
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
    
    Serial.printf("  HTTP Error: %d (attempt %d/%d)\n", code, attempt + 1, retries);
    
    if (attempt < retries - 1) {
      delay(500 * (attempt + 1));  // Exponential backoff
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
  if (queueCount == 0 || WiFi.status() != WL_CONNECTED) return;
  
  int processed = 0;
  const int MAX_PER_LOOP = 3;  // Process max 3 at a time to avoid blocking
  
  while (queueCount > 0 && processed < MAX_PER_LOOP) {
    if (sendHTTPRequest(eventQueue[queueHead].jsonData)) {
      eventQueue[queueHead].isValid = false;
      eventQueue[queueHead].jsonData = "";  // Free memory
      queueHead = (queueHead + 1) % MAX_QUEUE_SIZE;
      queueCount--;
      processed++;
      Serial.printf("✓ Processed queued event (%d remaining)\n", queueCount);
    } else {
      Serial.printf("✗ Failed to send queued event, will retry later\n");
      break;  // Stop if send fails
    }
    delay(100);  // Small delay between sends
  }
  
  if (processed > 0) {
    Serial.printf("📊 Queue: %d processed, %d remaining\n", processed, queueCount);
  }
}

void updatePeriodicStatus() {
  if (millis() - lastStatusUpdate < STATUS_UPDATE_INTERVAL) return;
  
  lastStatusUpdate = millis();
  logEvent("STATUS", "RUNNING", true);
}

void checkRemoteCommands() {
  if (millis() - lastCommandCheck < COMMAND_CHECK_INTERVAL) return;
  if (WiFi.status() != WL_CONNECTED) return;
  
  lastCommandCheck = millis();
  
  HTTPClient http;
  String commandURL = "http://" + String(serverIP) + ":" + String(serverPort) + "/api/command";
  http.begin(commandURL);
  http.setTimeout(2000);
  
  int code = http.GET();
  
  if (code == HTTP_CODE_OK || code == 200) {
    String response = http.getString();
    
    // Parse JSON response (simple parsing)
    if (response.indexOf("\"command\"") >= 0) {
      int cmdStart = response.indexOf("\"command\"") + 11;
      int cmdEnd = response.indexOf("\"", cmdStart);
      if (cmdEnd > cmdStart) {
        String command = response.substring(cmdStart, cmdEnd);
        command.trim();
        command.toUpperCase();
        
        if (command.length() > 0 && command != "NULL" && command != "NONE") {
          Serial.printf("📥 Received remote command: %s\n", command.c_str());
          
          // Execute command
          if (command == "START" && currentState == IDLE) {
            startBatch();
          } else if (command == "PAUSE" && currentState == RUNNING) {
            pauseBatch();
          } else if (command == "RESUME" && currentState == PAUSED) {
            resumeBatch();
          } else if (command == "END" && (currentState == RUNNING || currentState == PAUSED)) {
            endBatch();
          } else {
            Serial.printf("⚠ Command %s ignored (current state: %s)\n", 
              command.c_str(),
              currentState == IDLE ? "IDLE" : currentState == RUNNING ? "RUNNING" : "PAUSED");
          }
        }
      }
    }
  }
  
  http.end();
}

void reconnectWiFi() {
  static unsigned long lastReconnectAttempt = 0;
  static int reconnectDelay = 1000;
  
  if (millis() - lastReconnectAttempt < reconnectDelay) return;
  
  lastReconnectAttempt = millis();
  Serial.println("🔄 Attempting WiFi reconnection...");
  
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    reconnectDelay = (reconnectDelay * 2 < 30000) ? reconnectDelay * 2 : 30000;  // Max 30s delay
  } else {
    reconnectDelay = 1000;  // Reset on success
    Serial.println("✓ WiFi reconnected!");
    processEventQueue();
  }
}
String buildJSONPayload(const String &evt, const String &status) {
  unsigned long now = millis();
  unsigned long elapsedTime = (batchStartTime > 0) ? (now - batchStartTime) : 0;
  long activeRuntimeSecs = (elapsedTime > totalPausedTime) ? (elapsedTime - totalPausedTime) / 1000 : 0;
  long totalElapsedSecs = elapsedTime / 1000;
  long accumulatedPauseSecs = totalPausedTime / 1000;
  long rssi = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : -100;
  
  // Calculate batch duration for END events
  long batchDurationSecs = 0;
  float efficiency = 0.0;
  float productionRate = 0.0;
  
  if (evt == "END" && batchEndTime > 0 && batchStartTime > 0) {
    unsigned long totalBatchTime = batchEndTime - batchStartTime;
    batchDurationSecs = totalBatchTime / 1000;
    efficiency = calculateEfficiency(totalBatchTime - totalPausedTime, totalBatchTime);
    activeRuntimeSecs = (totalBatchTime - totalPausedTime) / 1000;
    totalElapsedSecs = batchDurationSecs;
    if (batchDurationSecs > 0) {
      productionRate = 3600.0 / batchDurationSecs;
    }
  } else if (evt == "START") {
    activeRuntimeSecs = 0;
    totalElapsedSecs = 0;
    batchDurationSecs = 0;
    efficiency = 0.0;
    productionRate = 0.0;
  } else if (currentState == RUNNING || currentState == PAUSED) {
    efficiency = calculateEfficiency(elapsedTime - totalPausedTime, elapsedTime);
    if (activeRuntimeSecs > 0) {
      productionRate = 3600.0 / activeRuntimeSecs;
    }
  }

  // FIX: Determine WiFi Quality string here, before snprintf
  const char* wifiQuality = "Poor";
  if (rssi > -50) wifiQuality = "Excellent";
  else if (rssi > -60) wifiQuality = "Good";
  else if (rssi > -70) wifiQuality = "Fair";

  // INCREASED BUFFER SIZE (Already in your code, keeping it safe)
  char buffer[4096]; 
  
  int result = snprintf(buffer, sizeof(buffer),
    "{"
    "\"timestamp\":\"%s\","
    "\"batch_id\":%d,"
    "\"event\":\"%s\","
    "\"status\":\"%s\","
    "\"machine\":\"%s\","
    "\"active_runtime_s\":%ld,"
    "\"active_runtime_formatted\":\"%s\","
    "\"total_elapsed_s\":%ld,"
    "\"total_elapsed_formatted\":\"%s\","
    "\"batch_duration_s\":%ld,"
    "\"batch_duration_formatted\":\"%s\","
    "\"total_pause_s\":%ld,"
    "\"total_pause_formatted\":\"%s\","
    "\"pause_count\":%d,"
    "\"avg_pause_duration_s\":%ld,"
    "\"efficiency_percent\":%.2f,"
    "\"production_rate_per_hour\":%.2f,"
    "\"wifi_rssi\":%ld,"
    "\"wifi_quality\":\"%s\""
    "}",
    getTimestamp().c_str(),
    batchNumber,
    evt.c_str(),
    status.c_str(),
    machineName.c_str(),
    activeRuntimeSecs,
    getFormattedTime(activeRuntimeSecs * 1000).c_str(),
    totalElapsedSecs,
    getFormattedTime(totalElapsedSecs * 1000).c_str(),
    batchDurationSecs,
    getFormattedTime(batchDurationSecs * 1000).c_str(),
    accumulatedPauseSecs,
    getFormattedTime(accumulatedPauseSecs * 1000).c_str(),
    pauseCount,
    (pauseCount > 0) ? (accumulatedPauseSecs / pauseCount) : 0L,
    efficiency,
    productionRate,
    rssi,
    wifiQuality // Passing the simple variable here instead of the complex logic
  );
  
  if (result >= sizeof(buffer)) {
    Serial.println("⚠ Warning: JSON buffer may be truncated!");
  }
  
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
    snprintf(buffer, sizeof(buffer), "Uptime: %02lu:%02lu:%02lu", hours, minutes, secs);
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
    snprintf(buffer, sizeof(buffer), "%02lu:%02lu:%02lu", hours, minutes, seconds);
  } else {
    snprintf(buffer, sizeof(buffer), "%02lu:%02lu", minutes, seconds);
  }
  return String(buffer);
}

float calculateEfficiency(unsigned long activeTime, unsigned long totalTime) {
  if (totalTime == 0) return 0.0;
  return (float(activeTime) / float(totalTime)) * 100.0;
}

void loadBatchNumber() {
  EEPROM.get(EEPROM_BATCH_NUM_ADDR, batchNumber);
  if (batchNumber < 0 || batchNumber > 100000) {
    batchNumber = 0;  // Reset if invalid
  }
  Serial.printf("✓ Loaded batch number: %d\n", batchNumber);
}

void saveBatchNumber() {
  EEPROM.put(EEPROM_BATCH_NUM_ADDR, batchNumber);
  EEPROM.commit();
}

void resetBatchNumber() {
  batchNumber = 0;  // Will become 1 on next startBatch() call
  saveBatchNumber();
  Serial.println("✓ Batch number reset to 0 (next batch will be #1)");
}

void setBatchNumber(int newNumber) {
  if (newNumber >= 0 && newNumber <= 100000) {
    batchNumber = newNumber;
    saveBatchNumber();
    Serial.printf("✓ Batch number set to: %d\n", batchNumber);
  } else {
    Serial.println("✗ Invalid batch number. Must be between 0 and 100000");
  }
}
