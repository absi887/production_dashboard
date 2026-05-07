import os
os.environ['EVENTLET_NO_GREENDNS'] = 'yes'
import eventlet
eventlet.hubs.use_hub("poll")
eventlet.monkey_patch()
from flask import Flask, request, jsonify, render_template_string
from flask_sock import Sock
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import csv
import os
import math
import pandas as pd
from datetime import datetime, timedelta
import json
import socket
import re
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
import pymongo
from bson import ObjectId

# Load environment variables from .env file
load_dotenv()
# Configuration from .env file
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', 5002))
WIFI_SSID = os.getenv('WIFI_SSID', 'YOUR_WIFI')
WIFI_PASSWORD = os.getenv('WIFI_PASSWORD', '')
MACHINE_NAME = os.getenv('MACHINE_NAME', 'FAM Production Hub')
LINE_NAME = os.getenv('LINE_NAME', 'door')
CSV_FILE = os.getenv('CSV_FILE', 'wood_line_data.csv')
DASHBOARD_TITLE = os.getenv('DASHBOARD_TITLE', 'ESP32 Batch Tracker - Production Dashboard')
DEBUG_MODE = os.getenv('DEBUG_MODE', 'True').lower() == 'true'
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')

# MongoDB Setup
try:
    mongo_client = pymongo.MongoClient(MONGO_URI)
    db = mongo_client['guardsify_db']
    jobs_col = db['jobs']
    state_col = db['state']
    events_col = db['events']
    print(f"🔌 Connected to MongoDB at {MONGO_URI}")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")

# ESP32 connection tracking
esp32_last_seen = None
esp32_connection_count = 0
esp32_last_batch_id = None

# Job Queue Persistence
QUEUE_FILE = 'job_queue_persistence.json'
job_queue = [] # Initialized early

def save_job_queue():
    # Now handled directly in CRUD endpoints, but we can maintain a global list for some functions
    global job_queue
    try:
        # Sync global job_queue with DB if needed, but better to just use DB
        pass
    except Exception as e:
        print(f"❌ Error syncing job queue: {e}")

def load_job_queue():
    global job_queue
    try:
        # Load active jobs from MongoDB
        job_queue = list(jobs_col.find({"status": {"$ne": "FINISHED"}}, {"_id": 0}))
        print(f"📂 Loaded {len(job_queue)} active jobs from MongoDB.")
    except Exception as e:
        print(f"❌ Error loading jobs from MongoDB: {e}")
        job_queue = []

load_job_queue()

# Command queue for ESP32 control (per line)
pending_commands = {
    "door": None,
    "frame": None,
    "arch": None
}
# Factory Config Persistence
CONFIG_FILE = 'factory_config.json'
STATE_FILE = 'production_state.json'
factory_config = {
    "machines": {"door": [], "frame": [], "arch": []},
    "materials": {},
    "line_flow": ["door", "frame", "arch"]
}

def load_factory_config():
    global factory_config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                factory_config = json.load(f)
            print(f"⚙️ Loaded factory config from {CONFIG_FILE}")
        except Exception as e:
            print(f"❌ Error loading config: {e}")

def save_factory_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(factory_config, f, indent=2)

def save_production_state():
    try:
        for line, data in production_state.items():
            state_col.update_one({"line": line}, {"$set": data}, upsert=True)
    except Exception as e:
        print(f"❌ Error saving state to MongoDB: {e}")

def load_production_state():
    global production_state
    try:
        cursor = state_col.find({}, {"_id": 0})
        for doc in cursor:
            line = doc.get("line")
            if line in production_state:
                production_state[line].update(doc)
        print("⚙️ Loaded production state from MongoDB")
    except Exception as e:
        print(f"❌ Error loading state from MongoDB: {e}")

load_production_state()

load_factory_config()

# Helpers that use dynamic config
def get_machines_list(line):
    return factory_config["machines"].get(line, [])

def get_material_leads():
    return factory_config.get("materials", {})

def get_line_flow():
    return factory_config.get("line_flow", ["door", "frame", "arch"])



def get_next_line(current_l):
    flow = get_line_flow()
    try:
        idx = flow.index(current_l)
        if idx + 1 < len(flow):
            return flow[idx+1]
    except: pass
    return None

def get_all_machines(line):
    return [m for m, _ in get_machines_list(line)]

def get_work_hours():
    return factory_config.get("hours_per_day", 7)

def get_work_days():
    return factory_config.get("days_per_week", 5)

def get_max_lead_time():
    """Get the maximum lead time from the material list"""
    leads = get_material_leads()
    return max(leads.values()) if leads else 0

# Button Mapping (12 Buttons on 1 ESP32)
BUTTON_MAP = {
    # Door Line
    1: {"line": "door", "action": "START"},
    2: {"line": "door", "action": "PAUSE"},
    3: {"line": "door", "action": "RESUME"},
    4: {"line": "door", "action": "END"},
    # Frame Line
    5: {"line": "frame", "action": "START"},
    6: {"line": "frame", "action": "PAUSE"},
    7: {"line": "frame", "action": "RESUME"},
    8: {"line": "frame", "action": "END"},
    # Architrave Line
    9: {"line": "arch", "action": "START"},
    10: {"line": "arch", "action": "PAUSE"},
    11: {"line": "arch", "action": "RESUME"},
    12: {"line": "arch", "action": "END"},
}

def get_local_ip():
    """Get local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except:
            return "127.0.0.1"

def update_arduino_code():
    """Update Arduino code with current configuration"""
    # Try both possible locations
    arduino_file = Path(__file__).parent / "batch.ino"
    if not arduino_file.exists():
        arduino_file = Path(__file__).parent / "batch" / "batch.ino"
    if not arduino_file.exists():
        print(f"⚠️  Arduino file not found. Tried: batch.ino and batch/batch.ino")
        return False
    
    server_ip = get_local_ip()
    
    try:
        with open(arduino_file, 'r') as f:
            content = f.read()
        
        # Update server IP
        content = re.sub(
            r'const char\* serverIP = "[^"]*";',
            f'const char* serverIP = "{server_ip}";',
            content
        )
        
        # Update WiFi SSID
        content = re.sub(
            r'const char\* ssid = "[^"]*";',
            f'const char* ssid = "{WIFI_SSID}";',
            content
        )
        
        # Update WiFi password
        content = re.sub(
            r'const char\* password = "[^"]*";',
            f'const char* password = "{WIFI_PASSWORD}";',
            content
        )
        
        # Update machine name
        content = re.sub(
            r'String machineName = "[^"]*";',
            f'String machineName = "{MACHINE_NAME}";',
            content
        )
        
        # Update line name
        if 'String lineName =' in content:
            content = re.sub(
                r'String lineName = "[^"]*";',
                f'String lineName = "{LINE_NAME}";',
                content
            )
        else:
            # If lineName doesn't exist yet, insert it after machineName
            content = content.replace(
                f'String machineName = "{MACHINE_NAME}";',
                f'String machineName = "{MACHINE_NAME}";\nString lineName = "{LINE_NAME}";'
            )
        
        # Update server port
        content = re.sub(
            r'const int serverPort = \d+;',
            f'const int serverPort = {SERVER_PORT};',
            content
        )
        
        with open(arduino_file, 'w') as f:
            f.write(content)
        
        print(f"✓ Updated Arduino code:")
        print(f"  - Server IP: {server_ip}")
        print(f"  - WiFi SSID: {WIFI_SSID}")
        print(f"  - Machine: {MACHINE_NAME}")
        print(f"  - Port: {SERVER_PORT}")
        return True
        
    except Exception as e:
        print(f"✗ Error updating Arduino code: {e}")
        return False

# Update Arduino code on server start
print("🔧 Updating Arduino configuration...")
update_arduino_code()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
sock = Sock(app)
CORS(app)  # Enable CORS for ESP32 requests
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Multi-Line State
production_state = {
    "door": {"status": "STOPPED", "current_machine": None, "current_machine_index": 0, "batch_id": 0, "quantity": 0, "start_time": None, "material": None, "order_id": None, "lead_time": None},
    "frame": {"status": "STOPPED", "current_machine": None, "current_machine_index": 0, "batch_id": 0, "quantity": 0, "start_time": None, "material": None, "order_id": None, "lead_time": None},
    "arch": {"status": "STOPPED", "current_machine": None, "current_machine_index": 0, "batch_id": 0, "quantity": 0, "start_time": None, "material": None, "order_id": None, "lead_time": None},
}

quarterly_reports = []

# Logic Functions
def calculate_quantity(prev_time, prev_machine, machines_list):
    prev_machine = str(prev_machine).strip().lower()
    for name, time in machines_list:
        if name.lower() == prev_machine:
            return math.floor(prev_time / time)
    return 0

def remaining_time(current_machine, quantity, machines_list):
    if not current_machine: return 0
    current_machine = str(current_machine).strip().lower()
    total = 0
    start = False
    for name, time in machines_list:
        if name.lower() == current_machine:
            start = True
        if start:
            total += time * quantity
    return total

def calculate_days(minutes):
    hours = minutes / 60
    return math.ceil(hours / get_work_hours())

def add_weekends(days):
    """Simple logic to skip weekends if working 5 days"""
    if get_work_days() >= 7: return days
    weeks = days // get_work_days()
    remaining = days % get_work_days()
    return (weeks * 7) + remaining

def get_line_efficiency(line_name):
    """Calculate the average efficiency for a specific line using MongoDB data"""
    try:
        cursor = events_col.find(
            {"event": "END", "line": line_name.lower(), "efficiency_percent": {"$gt": 0}},
            {"efficiency_percent": 1}
        ).sort([("timestamp", pymongo.DESCENDING)]).limit(10)
        
        efficiencies = [doc.get("efficiency_percent", 0) for doc in cursor]
        
        if efficiencies:
            avg_eff = sum(efficiencies) / len(efficiencies)
            avg_eff = max(50.0, min(150.0, avg_eff))
            return avg_eff / 100.0
    except Exception as e:
        print(f"Error calculating line efficiency: {e}")
        
    return 1.0
        
    return 1.0

def get_queue_backlog(line_name):
    """Calculate total processing minutes for all jobs in the queue for a specific line"""
    global job_queue
    total_mins = 0
    machines_list = get_machines_list(line_name)
    
    for job in job_queue:
        if job.get('line', '').lower() == line_name.lower():
            qty = int(job.get('quantity', 1))
            total_mins += sum(t * qty for _, t in machines_list)
    return total_mins

# CSV functionality replaced by MongoDB

@app.route('/data', methods=['POST', 'OPTIONS'])
def receive_data():
    global esp32_last_seen, esp32_connection_count, esp32_last_batch_id
    
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No JSON received"}), 400
        
        # New Logic: Handle Button ID
        button_id = data.get('button_id')
        if button_id in BUTTON_MAP:
            mapping = BUTTON_MAP[button_id]
            line = mapping['line']
            action = mapping['action']
            
            # Update State
            production_state[line]['status'] = action
            if 'machine' in data:
                production_state[line]['current_machine'] = data['machine']
            
            if action == 'START':
                production_state[line]['batch_id'] += 1
                production_state[line]['start_time'] = datetime.now().isoformat()
            
            print(f"🔘 Button {button_id} pressed: Line={line}, Action={action}, Machine={production_state[line]['current_machine']}")
            
            # Broadcast update
            socketio.emit('line_update', {
                'line': line,
                'status': action,
                'state': production_state[line],
                'timestamp': datetime.now().isoformat()
            })
            
            return jsonify({"success": True, "line": line, "action": action}), 200

        # ESP32 rich telemetry logic
        event = data.get('event', 'UNKNOWN')
        batch_id = data.get('batch_id', 0)
        status = data.get('status', 'UNKNOWN').upper()
        machine = data.get('machine', MACHINE_NAME)
        line = data.get('line', LINE_NAME).lower()
        
        esp32_last_seen = datetime.now()
        esp32_connection_count += 1
        esp32_last_batch_id = batch_id
        
        # Map specific statuses to standardized dashboard statuses
        status_map = {
            "RUNNING": "RUNNING",
            "PAUSED": "PAUSED",
            "RESUME": "RUNNING",
            "STOPPED": "STOPPED",
            "COMPLETED": "FINISHED",
            "FINISHED": "FINISHED",
            "START": "RUNNING",
            "END": "FINISHED",
            "PROCUREMENT": "PAUSED"
        }
        mapped_status = status_map.get(status, status)
        
        # Update Production State for the specific line
        if line in production_state:
            # Prevent generic Arduino name from overwriting the precise backend sequence
            backend_machine = production_state[line].get('current_machine', '')
            if backend_machine and (machine.lower() == line.lower() or machine.lower() == 'wood_line_1' or machine == MACHINE_NAME):
                machine = backend_machine

            production_state[line]['status'] = mapped_status
            production_state[line]['batch_id'] = batch_id
            production_state[line]['current_machine'] = machine
            
            if event == 'START':
                production_state[line]['start_time'] = datetime.now().isoformat()

            # 🛡️ AUTOMATION LOGIC: Complete Job on END
            if event == 'END' or status == 'COMPLETED':
                mapped_status = "FINISHED"
                production_state[line]['status'] = "FINISHED"
                complete_job_on_line(line)
                # The auto-pull within complete_job_on_line might set the state back to RUNNING. 
                # We fetch the latest status to ensure alignment.
                mapped_status = production_state[line]['status']
            
            print(f"📊 ESP32 Update: Line={line}, Machine={machine}, Event={event}, Status={mapped_status}")
            
            # Broadcast update
            socketio.emit('line_update', {
                'line': line,
                'status': mapped_status,
                'state': {
                    **production_state[line],
                    'all_machines': get_all_machines(line) # Send for UI stepper
                },
                'data': data,
                'timestamp': datetime.now().isoformat()
            })
            
            # Save state if anything changed
            if mapped_status == "FINISHED" or mapped_status == "READY" or mapped_status == "RUNNING":
                save_job_queue()
                save_production_state()
                socketio.emit('jobs_imported', {"jobs": job_queue})
        
        # Save to MongoDB events collection
        event_doc = {**data}
        event_doc['status'] = mapped_status
        if 'timestamp' not in event_doc:
            event_doc['timestamp'] = datetime.now().isoformat()
        event_doc['source'] = data.get('source', 'Arduino')
        
        try:
            events_col.insert_one(event_doc)
        except Exception as e:
            print(f"❌ Error saving event to MongoDB: {e}")
            
        socketio.emit('new_data', {'event': event, 'batch_id': batch_id, 'data': data})
        return jsonify({"success": True, "message": "Telemetry saved", "line": line}), 200
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

def quarterly_report_task():
    """Background task to snapshot state every 15 minutes"""
    while True:
        time.sleep(15 * 60) # 15 minutes
        report = {
            "timestamp": datetime.now().isoformat(),
            "door": production_state["door"].copy(),
            "frame": production_state["frame"].copy(),
            "arch": production_state["arch"].copy()
        }
        quarterly_reports.append(report)
        socketio.emit('quarterly_report', report)
        print(f"📊 Quarterly report generated at {report['timestamp']}")

# Start reporting thread
threading.Thread(target=quarterly_report_task, daemon=True).start()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "ok", 
        "service": "ESP32 Batch Tracker",
        "websocket": "enabled",
        "uptime": "running"
    }), 200

@app.route('/api/command', methods=['POST'])
def set_command():
    """Set a command for ESP32 to execute"""
    global pending_commands
    
    try:
        data = request.json
        command = data.get('command', '').upper()
        line = data.get('line', 'door').lower()
        
        if line not in pending_commands:
            return jsonify({"error": f"Invalid line: {line}"}), 400
            
        valid_commands = ['START', 'PAUSE', 'RESUME', 'END']
        if command not in valid_commands:
            return jsonify({"error": f"Invalid command. Must be one of: {', '.join(valid_commands)}"}), 400
        
        pending_commands[line] = command
        
        # Emit WebSocket event
        socketio.emit('command_sent', {
            'command': command, 
            'line': line,
            'timestamp': datetime.now().isoformat()
        })
        
        print(f"📤 Command set for {line}: {command}")
        
        # Log to MongoDB
        try:
            events_col.insert_one({
                "timestamp": datetime.now().isoformat(),
                "line": line,
                "event": "COMMAND",
                "status": command,
                "source": "Website"
            })
        except Exception as e:
            print(f"❌ Error logging command to MongoDB: {e}")

        return jsonify({
            "success": True,
            "command": command,
            "line": line,
            "message": f"Command '{command}' queued for {line}"
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/command', methods=['GET'])
def get_command():
    """Get pending command for ESP32 (ESP32 polls this)"""
    global pending_commands
    
    line = request.args.get('line', 'all').lower()
    
    if line == 'all':
        active_commands = {l: cmd for l, cmd in pending_commands.items() if cmd}
        if active_commands:
            # Clear them
            for l in active_commands:
                pending_commands[l] = None
            return jsonify({"commands": active_commands}), 200
        return jsonify({"commands": {}}), 200
    
    if line in pending_commands and pending_commands[line]:
        command = pending_commands[line]
        pending_commands[line] = None # Clear after retrieval
        return jsonify({"command": command}), 200
    
    return jsonify({"command": None}), 200

@app.route('/api/command/clear', methods=['POST'])
def clear_command():
    """Clear any pending command"""
    global pending_commands
    data = request.json
    line = data.get('line', 'door').lower()
    
    if line in pending_commands:
        pending_commands[line] = None
        return jsonify({"success": True, "message": f"Command cleared for {line}"}), 200
    
    # Clear all if no line specified
    for l in pending_commands:
        pending_commands[l] = None
    return jsonify({"success": True, "message": "All commands cleared"}), 200

@app.route('/api/server/status', methods=['GET'])
def server_status():
    """Get server status and information"""
    try:
        import psutil
        import os
        import socket
        process = psutil.Process(os.getpid())
        
        # Get local IP
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        # Calculate time since last ESP32 contact
        esp32_status = "never"
        if esp32_last_seen:
            time_diff = (datetime.now() - esp32_last_seen).total_seconds()
            if time_diff < 60:
                esp32_status = f"{int(time_diff)}s ago"
            elif time_diff < 3600:
                esp32_status = f"{int(time_diff/60)}m ago"
            else:
                esp32_status = f"{int(time_diff/3600)}h ago"
        
        db_connected = False
        try:
            jobs_col.count_documents({})
            db_connected = True
        except:
            pass

        return jsonify({
            "status": "running",
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "db_connected": db_connected,
            "esp32_online": esp32_last_seen is not None and (datetime.now() - esp32_last_seen).total_seconds() < 30,
            "esp32_last_seen": esp32_status,
            "jobs_count": jobs_col.count_documents({}) if db_connected else 0,
            "events_count": events_col.count_documents({}) if db_connected else 0,
            "config": {
                "server_host": SERVER_HOST,
                "server_port": SERVER_PORT,
                "local_ip": local_ip,
                "data_endpoint": f"http://{local_ip}:{SERVER_PORT}/data",
                "wifi_ssid": WIFI_SSID,
                "machine_name": MACHINE_NAME
            }
        }), 200
    except Exception as e:
        return jsonify({
            "status": "running",
            "websocket": "enabled",
            "error": str(e),
            "esp32_last_seen": esp32_status if 'esp32_last_seen' in globals() else "never",
            "esp32_connection_count": esp32_connection_count if 'esp32_connection_count' in globals() else 0
        }), 200

@app.route('/api/test', methods=['GET', 'POST'])
def test_endpoint():
    """Test endpoint for ESP32 connectivity"""
    import socket
    client_ip = request.remote_addr
    method = request.method
    
    # Get local IP for config
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    response_data = {
        "success": True,
        "message": "Server is reachable!",
        "method": method,
        "client_ip": client_ip,
        "server_time": datetime.now().isoformat(),
        "endpoint": "/api/test",
        "config": {
            "server_host": SERVER_HOST,
            "server_port": SERVER_PORT,
            "local_ip": local_ip,
            "data_endpoint": f"http://{local_ip}:{SERVER_PORT}/data",
            "wifi_ssid": WIFI_SSID,
            "machine_name": MACHINE_NAME
        }
    }
    
    if method == 'POST':
        response_data["received_data"] = request.json if request.json else request.get_data(as_text=True)
    
    print(f"✓ Test endpoint accessed by {client_ip} via {method}")
    return jsonify(response_data), 200

@app.route('/api/state', methods=['GET'])
def get_full_state():
    """Get the full live production state for all lines"""
    return jsonify(production_state), 200

@app.route('/api/esp32-status', methods=['GET'])
def esp32_status():
    """Get ESP32 connection status"""
    global esp32_last_seen, esp32_connection_count, esp32_last_batch_id
    
    status = "disconnected"
    time_since_last = None
    
    if esp32_last_seen:
        time_diff = (datetime.now() - esp32_last_seen).total_seconds()
        if time_diff < 300:  # 5 minutes
            status = "connected"
        elif time_diff < 3600:  # 1 hour
            status = "recently_connected"
        else:
            status = "disconnected"
        time_since_last = time_diff
    
    return jsonify({
        "status": status,
        "last_seen": esp32_last_seen.isoformat() if esp32_last_seen else None,
        "time_since_last_seconds": time_since_last,
        "total_connections": esp32_connection_count,
        "last_batch_id": esp32_last_batch_id
    }), 200

def automation_loop():
    """Background task to automate specific machines like 'Paint' based on defined minutes"""
    print("🤖 Automation Engine Started...")
    while True:
        try:
            eventlet.sleep(30) # Check every 30 seconds
            now = datetime.now()
            
            for line in ["door", "frame", "arch"]:
                state = production_state.get(line)
                if not state or state.get('status') != 'RUNNING':
                    continue
                
                current_machine = state.get('current_machine')
                if not current_machine or "Paint" not in str(current_machine):
                    continue
                
                start_time_str = state.get('start_time')
                if not start_time_str:
                    continue
                
                try:
                    start_time = datetime.fromisoformat(start_time_str)
                    
                    # Find defined minutes for Paint in the config
                    machines = get_machines_list(line)
                    paint_mins = 90 # Default fallback
                    for m, mins in machines:
                        if m == current_machine:
                            paint_mins = mins
                            break
                    
                    quantity = state.get('quantity', 1) or 1
                    total_required_mins = paint_mins * quantity
                    
                    elapsed_mins = (now - start_time).total_seconds() / 60
                    
                    if elapsed_mins >= total_required_mins:
                        print(f"🤖 Automation: Auto-advancing '{line}' from '{current_machine}' after {int(elapsed_mins)} mins")
                        do_advance_job(line)
                            
                except Exception as e:
                    print(f"❌ Automation Error on line {line}: {e}")
                    
        except Exception as e:
            print(f"❌ Global Automation Loop Error: {e}")

@sock.route('/ws')
def handle_ws(ws):
    """Handle plain WebSocket connection from ESP32"""
    print('ESP32 connected via plain WebSocket')
    while True:
        try:
            message = ws.receive()
            if not message:
                break
            
            data = json.loads(message)
            print(f"📩 WS Received: {data}")
            
            # Use existing logic to process data
            with app.test_request_context('/data', method='POST', json=data):
                response = receive_data()
                print(f"✅ WS Response sent: {response[0].get_data(as_text=True)}")
                
        except Exception as e:
            print(f"❌ WS Error: {e}")
            break
    print('ESP32 disconnected from plain WebSocket')

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print('Client connected via WebSocket')
    emit('connected', {'message': 'Connected to ESP32 Batch Tracker'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    print('Client disconnected from WebSocket')

@socketio.on('request_update')
def handle_request_update():
    """Handle client request for data update"""
    try:
        # Send stats
        stats = calculate_stats()
        emit('stats_update', stats)
        
        # Send production line states
        for line in ['door', 'frame', 'arch']:
            emit('line_update', {
                'line': line,
                'status': production_state[line]['status'],
                'state': production_state[line],
                'timestamp': datetime.now().isoformat()
            })
            
        # Send job queue
        emit('jobs_imported', {"count": len(job_queue), "jobs": job_queue})
    except Exception as e:
        emit('error', {'message': str(e)})

def calculate_stats():
    """Calculate current statistics"""
    if not os.path.exists(CSV_FILE):
        return {
            "total_batches": 0,
            "total_runtime": 0,
            "total_runtime_formatted": "00:00:00",
            "avg_efficiency": 0,
            "avg_production_rate": 0,
            "total_pauses": 0,
            "avg_duration": 0,
            "avg_duration_formatted": "00:00",
            "best_efficiency": 0,
            "worst_efficiency": 0,
            "total_events": 0
        }
    
    total_batches = events_col.count_documents({"event": "END"})
    total_events = events_col.count_documents({})
    
    pipeline = [
        {"$match": {"event": "END"}},
        {"$group": {
            "_id": None,
            "total_runtime": {"$sum": {"$toInt": "$batch_duration_s"}},
            "avg_efficiency": {"$avg": {"$toDouble": "$efficiency_percent"}},
            "avg_production_rate": {"$avg": {"$toDouble": "$production_rate_per_hour"}},
            "avg_duration": {"$avg": {"$toInt": "$batch_duration_s"}},
            "total_pauses": {"$sum": {"$toInt": "$pause_count"}},
            "best_efficiency": {"$max": {"$toDouble": "$efficiency_percent"}},
            "worst_efficiency": {"$min": {"$toDouble": "$efficiency_percent"}}
        }}
    ]
    
    res = list(events_col.aggregate(pipeline))
    stats = res[0] if res else {}
    
    total_runtime = stats.get("total_runtime", 0)
    avg_efficiency = stats.get("avg_efficiency", 0)
    avg_production_rate = stats.get("avg_production_rate", 0)
    avg_duration = stats.get("avg_duration", 0)
    total_pauses = stats.get("total_pauses", 0)
    best_efficiency = stats.get("best_efficiency", 0)
    worst_efficiency = stats.get("worst_efficiency", 0)
    
    return {
        "total_batches": total_batches,
        "total_runtime": total_runtime,
        "total_runtime_formatted": format_time(total_runtime),
        "avg_efficiency": round(avg_efficiency, 2),
        "avg_production_rate": round(avg_production_rate, 2),
        "total_pauses": total_pauses,
        "avg_duration": round(avg_duration, 0),
        "avg_duration_formatted": format_time(int(avg_duration)),
        "best_efficiency": round(best_efficiency, 2),
        "worst_efficiency": round(worst_efficiency, 2),
        "total_events": total_events
    }

@app.route('/api/data', methods=['GET'])
def get_data():
    """Get all batch data from MongoDB"""
    try:
        cursor = events_col.find({}, {"_id": 0})
        data = list(cursor)
        return jsonify({"data": data, "count": len(data)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/batches', methods=['GET'])
def get_batches():
    """Get completed batches summary from MongoDB"""
    try:
        cursor = events_col.find({"event": "END", "batch_id": {"$ne": ""}}, {"_id": 0}).sort([("batch_id", pymongo.DESCENDING)])
        batch_list = []
        for doc in cursor:
            batch_list.append({
                'batch_id': doc.get('batch_id'),
                'timestamp': doc.get('timestamp'),
                'duration_s': int(doc.get('batch_duration_s', 0) or 0),
                'duration_formatted': doc.get('batch_duration_formatted', '00:00'),
                'active_runtime_s': int(doc.get('active_runtime_s', 0) or 0),
                'efficiency': float(doc.get('efficiency_percent', 0) or 0),
                'pause_count': int(doc.get('pause_count', 0) or 0),
                'production_rate': float(doc.get('production_rate_per_hour', 0) or 0)
            })
        
        return jsonify({"batches": batch_list, "count": len(batch_list)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get overall statistics from MongoDB"""
    try:
        stats = calculate_stats()
        return jsonify(stats), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route('/api/predictions', methods=['GET'])
def get_predictions():
    """Calculate predictions based on current state and Material Lead Times"""
    try:
        # 1. Current Order Remaining & Queue Backlog
        door_rem = remaining_time(production_state["door"]["current_machine"], production_state["door"]["quantity"] or 10, get_machines_list("door"))
        frame_rem = remaining_time(production_state["frame"]["current_machine"], production_state["frame"]["quantity"] or 10, get_machines_list("frame"))
        arch_rem = remaining_time(production_state["arch"]["current_machine"], production_state["arch"]["quantity"] or 10, get_machines_list("arch"))
        
        door_total = (door_rem + get_queue_backlog("door")) / get_line_efficiency("door")
        frame_total = (frame_rem + get_queue_backlog("frame")) / get_line_efficiency("frame")
        arch_total = (arch_rem + get_queue_backlog("arch")) / get_line_efficiency("arch")
        
        remaining_current_mins = max(door_total, frame_total, arch_total)
        current_days_capacity = calculate_days(remaining_current_mins)
        
        # 2. Supply Lag (Absolute Bottleneck across Active & Queued jobs)
        max_material_lead = 0
        bottleneck_material = "None"
        
        material_leads = get_material_leads()
        
        # Check Active Jobs
        all_relevant_jobs = []
        for line in ["door", "frame", "arch"]:
            state = production_state.get(line, {})
            if state.get('order_id'):
                all_relevant_jobs.append(state)
        
        # Check Queued Jobs
        all_relevant_jobs.extend(job_queue)
        
        for job in all_relevant_jobs:
            mats = job.get('materials', [])
            if not mats:
                mat = job.get('material')
                lead = job.get('lead_time')
                if mat or lead:
                    mats = [{"name": mat, "lead_time": lead}]
            
            # Use import time for dynamic calculation
            import_time_str = job.get('imported_at')
            elapsed_days = 0
            if import_time_str:
                try:
                    import_time = datetime.fromisoformat(import_time_str)
                    elapsed_days = (datetime.now() - import_time).days
                except: pass

            for m in mats:
                mat_name = m.get('name', '')
                custom_lead = m.get('lead_time')
                
                defined_lead = 0
                if custom_lead is not None and str(custom_lead).strip() != "":
                    try: defined_lead = int(custom_lead)
                    except: pass
                elif mat_name and str(mat_name).lower() != 'none':
                    for known_mat, lead in material_leads.items():
                        if known_mat.lower() in mat_name.lower() or mat_name.lower() in known_mat.lower():
                            defined_lead = max(defined_lead, lead)
                
                # Dynamic lead = Total Lead - Time Already Passed
                remaining_lead = max(0, defined_lead - elapsed_days)
                
                if remaining_lead > max_material_lead:
                    max_material_lead = remaining_lead
                    bottleneck_material = mat_name or "Custom Lead"
                
                if mat_name or custom_lead:
                    has_active_material = True
                    
                current_max = 0
                if custom_lead is not None and str(custom_lead).strip() != "":
                    try:
                        current_max = int(custom_lead)
                    except: pass
                elif mat_name and str(mat_name).lower() != 'none':
                    for known_mat, lead in material_leads.items():
                        if known_mat.lower() in mat_name.lower() or mat_name.lower() in known_mat.lower():
                            current_max = max(current_max, lead)
                
                if current_max > max_material_lead:
                    max_material_lead = current_max
                    bottleneck_material = mat_name or "Custom"
        
        # 3. Decision Maker Logic
        decision = ""
        case = ""
        
        if not has_active_material:
            start_calendar = add_weekends(current_days_capacity)
            decision = f"✅ READY: No specific material bottlenecks assigned. Line is available for new orders in {current_days_capacity} days."
            case = "Ready for production"
        elif max_material_lead > current_days_capacity:
            start_calendar = add_weekends(max_material_lead)
            diff = max_material_lead - current_days_capacity
            decision = f"⚠️ DELAY DETECTED: Material '{bottleneck_material}' will take {max_material_lead} days. Production is ready in {current_days_capacity} days. Recommendation: Wait {diff} days or prioritize other jobs."
            case = "Material NOT arrived"
        else:
            start_calendar = add_weekends(current_days_capacity)
            decision = f"✅ ALL CLEAR: All materials (bottleneck: {bottleneck_material}) are estimated to arrive within {max_material_lead} days. Production is ready in {current_days_capacity} days."
            case = "Material arrived early"
            
        # 4. New Order Processing
        qty_new = production_state["door"].get("quantity", 20) or 20
        door_proc = sum(t * qty_new for _, t in get_machines_list("door"))
        frame_proc = sum(t * qty_new for _, t in get_machines_list("frame"))
        arch_proc = sum(t * qty_new for _, t in get_machines_list("arch"))
        
        processing_total_mins = max(door_proc, frame_proc, arch_proc)
        processing_days = calculate_days(processing_total_mins)
        
        processing_calendar = add_weekends(processing_days)
        finish_calendar = start_calendar + processing_calendar
        
        return jsonify({
            "current_finish_days": current_days_capacity,
            "material_lead_time": max_material_lead,
            "new_order_start_days": start_calendar,
            "expected_finish_days": finish_calendar,
            "decision": decision,
            "case": case,
            "last_calculation": datetime.now().isoformat()
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/analyze-job', methods=['POST'])
def analyze_job():
    """Analyze a new hypothetical job"""
    try:
        data = request.json
        line = data.get('line', '').lower()
        if 'door' in line: line = 'door'
        elif 'frame' in line: line = 'frame'
        elif 'arch' in line: line = 'arch'
        else: line = ''
        
        qty = int(data.get('quantity', 1))
        materials = data.get('materials', [])
        single_material = data.get('material')
        
        if single_material:
            materials = [{"name": single_material}]
            
        # 0. Auto-assign Line if not provided
        if not line:
            best_line = 'door'
            lowest_backlog = float('inf')
            for l in ['door', 'frame', 'arch']:
                line_state = production_state.get(l, {})
                cur_m = line_state.get('current_machine')
                cur_q = line_state.get('quantity') or 0
                m_list = get_machines_list(l)
                rem_mins = remaining_time(cur_m, cur_q, m_list)
                q_mins = get_queue_backlog(l)
                eff = get_line_efficiency(l)
                tot_mins = (rem_mins + q_mins) / (eff if eff > 0 else 1)
                if tot_mins < lowest_backlog:
                    lowest_backlog = tot_mins
                    best_line = l
            line = best_line
        
        # 1. Current Order Remaining & Queue Backlog
        machines_list = get_machines_list(line)
        efficiency_modifier = get_line_efficiency(line)

        # Use the maximum backlog across all lines for the global finish time
        backlogs = []
        for l in ['door', 'frame', 'arch']:
            line_state = production_state.get(l, {})
            l_cur_m = line_state.get('current_machine')
            l_cur_q = line_state.get('quantity') or 0
            l_m_list = get_machines_list(l)
            l_eff = get_line_efficiency(l)
            l_rem = remaining_time(l_cur_m, l_cur_q, l_m_list)
            l_que = get_queue_backlog(l)
            backlogs.append((l_rem + l_que) / (l_eff if l_eff > 0 else 1))
            
        total_backlog_mins_max = max(backlogs) if backlogs else 0
        current_days_capacity = calculate_days(total_backlog_mins_max)
        total_backlog_mins = total_backlog_mins_max # For backward compatibility in result
        
        # 2. Material Lead Time (Calculate Bottleneck)
        max_material_lead = 0
        config_materials = factory_config.get('materials', {})
        for m in materials:
            mat_name = m.get('name', '')
            custom_lead = m.get('lead_time')
            
            current_max = 0
            if custom_lead is not None and custom_lead != "":
                try:
                    current_max = int(custom_lead)
                except: pass
            elif mat_name and mat_name.lower() != 'none':
                for known_mat, lead in config_materials.items():
                    if known_mat.lower() in mat_name.lower() or mat_name.lower() in known_mat.lower():
                        current_max = max(current_max, lead)
            
            max_material_lead = max(max_material_lead, current_max)
                    
        # 3. Decision Maker
        decision = ""
        case = ""
        if max_material_lead == 0:
            start_calendar = add_weekends(current_days_capacity)
            decision = f"✅ READY: Line '{line}' is available in {current_days_capacity} days."
            case = "Ready"
        elif max_material_lead > current_days_capacity:
            start_calendar = add_weekends(max_material_lead)
            diff = max_material_lead - current_days_capacity
            decision = f"⚠️ DELAY DETECTED: Material bottleneck takes {max_material_lead} days. Line is ready in {current_days_capacity} days. Wait {diff} days."
            case = "Material Delayed"
        else:
            start_calendar = add_weekends(current_days_capacity)
            decision = f"✅ ALL CLEAR: All materials arrive in {max_material_lead} days or less. Line ready in {current_days_capacity} days."
            case = "Material Early"
            
        # 4. Processing Time
        processing_mins = sum(t * qty for _, t in machines_list)
        processing_mins_adjusted = processing_mins / efficiency_modifier
        processing_days = calculate_days(processing_mins_adjusted)
        
        processing_calendar = add_weekends(processing_days)
        finish_calendar = start_calendar + processing_calendar
        
        return jsonify({
            "current_finish_days": current_days_capacity,
            "material_lead_time": max_material_lead,
            "new_order_start_days": start_calendar,
            "expected_finish_days": finish_calendar,
            "expected_finish_mins": int(total_backlog_mins + (sum(m[1] for m in machines_list) * qty)),
            "processing_days": processing_calendar,
            "efficiency_modifier": round(efficiency_modifier, 2),
            "decision": decision,
            "case": case
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/add-job', methods=['POST'])
def add_job():
    """Add a single job to the queue"""
    global job_queue
    try:
        data = request.json
        line = data.get('line', '').lower()
        if 'door' in line: line = 'door'
        elif 'frame' in line: line = 'frame'
        elif 'arch' in line: line = 'arch'
        else: line = ''
        
        # Auto-assign if line is empty
        if not line:
            best_line = 'door'
            lowest_backlog = float('inf')
            for l in ['door', 'frame', 'arch']:
                cur_m = production_state[l].get('current_machine')
                cur_q = production_state[l].get('quantity') or 0
                m_list = get_machines_list(l)
                rem_mins = remaining_time(cur_m, cur_q, m_list)
                q_mins = get_queue_backlog(l)
                eff = get_line_efficiency(l)
                tot_mins = (rem_mins + q_mins) / (eff if eff > 0 else 1)
                if tot_mins < lowest_backlog:
                    lowest_backlog = tot_mins
                    best_line = l
            line = best_line
            
        order_id = data.get('order_id')
        if not order_id or str(order_id).strip() == "":
            order_id = f"JOB-{int(time.time() * 1000) % 1000000}"

        materials = data.get('materials', [])
        # For legacy compatibility, join material names
        material_summary = ", ".join([m.get('name', '') for m in materials if m.get('name')])

        job = {
            "order_id": order_id,
            "line": line,
            "quantity": int(data.get('quantity', 1)),
            "materials": materials,
            "material": material_summary, # Legacy summary
            "machine": data.get('machine', ''),
            "imported_at": datetime.now().isoformat(),
            "status": "QUEUED"
        }
        

                
        # Save to MongoDB
        try:
            jobs_col.insert_one(job)
            load_job_queue() # Refresh global queue
        except Exception as e:
            print(f"❌ Error saving job to MongoDB: {e}")
                
        socketio.emit('jobs_imported', {"count": 1, "jobs": job_queue})
        
        return jsonify({"success": True, "message": "Job added to queue", "job": {**job, "_id": str(job.get("_id", ""))} if "_id" in job else job}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export', methods=['GET'])
def export_csv():
    """Export event data from MongoDB as CSV"""
    try:
        import io
        import csv
        from flask import send_file
        
        cursor = events_col.find({}, {"_id": 0})
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Get headers from first document if exists
        first = events_col.find_one({}, {"_id": 0})
        if first:
            headers = list(first.keys())
            writer.writerow(headers)
            for doc in cursor:
                writer.writerow([doc.get(h, "") for h in headers])
        
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            as_attachment=True,
            download_name=f'batch_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
            mimetype='text/csv'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs/export', methods=['GET'])
def export_jobs():
    """Export current job queue as CSV"""
    global job_queue
    try:
        import io
        import pandas as pd
        from flask import send_file
        
        if not job_queue:
            return jsonify({"error": "Job queue is empty"}), 400
            
        df = pd.DataFrame(job_queue)
        
        # Save to buffer
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        
        return send_file(
            buf,
            as_attachment=True,
            download_name=f'job_queue_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
            mimetype='text/csv'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/template', methods=['GET'])
def download_template():
    """Generate and return a sample CSV template for bulk job imports"""
    try:
        import io
        import pandas as pd
        from flask import send_file
        
        # Create a sample dataframe
        data = {
            'Order ID': ['JOB-001', 'JOB-002'],
            'Production Line': ['door', 'frame'],
            'Quantity': [25, 50],
            'Material': ['Solid chipboard 38mm', 'Softwood 45mm'],
            'Lead Time': [14, 7]
        }
        df = pd.DataFrame(data)
        
        # Save to buffer
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        
        return send_file(
            buf,
            as_attachment=True,
            download_name='ai_job_template.csv',
            mimetype='text/csv'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/recent', methods=['GET'])
def get_recent():
    """Get recent activity from MongoDB"""
    try:
        limit = int(request.args.get('limit', 50))
        cursor = events_col.find({}, {"_id": 0}).sort([("timestamp", pymongo.DESCENDING)]).limit(limit)
        data = list(cursor)
        return jsonify({"data": data, "count": len(data)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trends', methods=['GET'])
def get_trends():
    """Get trends from MongoDB"""
    try:
        cursor = events_col.find({"event": "END"}, {"_id": 0})
        trends = []
        for doc in cursor:
            trends.append({
                'date': doc.get('timestamp', '').split('T')[0],
                'batch_id': doc.get('batch_id'),
                'efficiency': float(doc.get('efficiency_percent', 0) or 0),
                'production_rate': float(doc.get('production_rate_per_hour', 0) or 0),
                'duration': int(doc.get('batch_duration_s', 0) or 0)
            })
        return jsonify({"trends": trends, "count": len(trends)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs/delete', methods=['POST'])
def delete_job():
    """Remove a job from the queue"""
    global job_queue
    try:
        data = request.json
        order_id = data.get('order_id')
        if not order_id:
            return jsonify({"error": "Order ID required"}), 400
            
        res = jobs_col.delete_one({"order_id": str(order_id)})
        load_job_queue()
        
        if res.deleted_count > 0:
            socketio.emit('jobs_imported', {"count": 1, "jobs": job_queue})
            return jsonify({"success": True, "message": f"Job {order_id} removed"}), 200
        else:
            return jsonify({"error": "Job not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs/edit', methods=['POST'])
def edit_job():
    """Modify a job in the queue"""
    global job_queue
    try:
        data = request.json
        order_id = data.get('order_id')
        if not order_id:
            return jsonify({"error": "Order ID required"}), 400
            
        update_fields = {}
        if 'quantity' in data: update_fields['quantity'] = int(data['quantity'])
        if 'material' in data: update_fields['material'] = data['material']
        if 'lead_time' in data: update_fields['lead_time'] = data['lead_time']
        if 'line' in data: update_fields['line'] = data['line']
        
        res = jobs_col.update_one({"order_id": str(order_id)}, {"$set": update_fields})
        
        if res.matched_count > 0:
            load_job_queue()
            socketio.emit('jobs_imported', {"count": 1, "jobs": job_queue})
            return jsonify({"success": True, "message": "Job updated"}), 200
        else:
            return jsonify({"error": "Job not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs/update-status', methods=['POST'])
def update_job_status():
    """Manually update a job's status in the queue"""
    global job_queue
    try:
        data = request.json
        order_id = data.get('order_id')
        new_status = data.get('status', 'QUEUED').upper()
        
        for job in job_queue:
            if str(job.get('order_id')) == str(order_id):
                job['status'] = new_status
                break
        
        res = jobs_col.update_one({"order_id": str(order_id)}, {"$set": {"status": new_status}})
        
        if res.matched_count > 0:
            load_job_queue()
            socketio.emit('jobs_imported', {"count": 0, "jobs": job_queue})
            return jsonify({"success": True}), 200
        else:
            return jsonify({"error": "Job not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def complete_job_on_line(line):
    global production_state, job_queue
    state = production_state[line]
    order_id = state.get('order_id')
    
    if not order_id:
        return
        
    print(f"🏁 Completing job {order_id} on line {line}")
    
    next_l = get_next_line(line)
    if next_l:
        if production_state[next_l]['status'] in ['STOPPED', 'FINISHED']:
            print(f"🚀 Auto-Flow: Moving job {order_id} from {line} to {next_l}")
            next_machines = get_machines_list(next_l)
            next_total_mins = sum(m[1] for m in next_machines) * state.get('quantity', 1)
            
            production_state[next_l].update({
                'order_id': order_id,
                'quantity': state.get('quantity', 1),
                'materials': state.get('materials', []),
                'material': state.get('material', ''),
                'status': 'READY',
                'current_machine': get_all_machines(next_l)[0] if next_machines else "",
                'current_machine_index': 0,
                'lead_time': f"{next_total_mins} mins"
            })
            socketio.emit('line_update', {
                'line': next_l,
                'status': 'READY',
                'state': {**production_state[next_l], 'all_machines': get_all_machines(next_l)},
                'timestamp': datetime.now().isoformat()
            })
            for job in job_queue:
                if job['order_id'] == order_id:
                    job['line'] = next_l
                    job['status'] = 'READY'
                    break
        else:
            print(f"⏸️ Next line {next_l} is busy. Queuing job {order_id} for {next_l}.")
            for job in job_queue:
                if job['order_id'] == order_id:
                    job['line'] = next_l
                    job['status'] = 'QUEUED'
                    break
            socketio.emit('jobs_imported', {"jobs": job_queue})
    else:
        for job in job_queue:
            if job['order_id'] == order_id:
                job['status'] = "FINISHED"
                break
                
    state['order_id'] = None
    state['quantity'] = 0
    state['materials'] = []
    state['material'] = ''
    state['lead_time'] = None
    state['current_machine'] = ""
    state['current_machine_index'] = 0
    state['status'] = 'STOPPED'
    
    success, msg = do_deploy_job(line)
    if success:
        print(f"🚀 Auto-Pull: {msg}")
    
    socketio.emit('line_update', {
        'line': line,
        'status': state['status'],
        'state': state,
        'timestamp': datetime.now().isoformat()
    })

def do_deploy_job(line, order_id=None):
    global job_queue, pending_commands, production_state
    
    if production_state[line]['status'] not in ['STOPPED', 'FINISHED']:
        return False, f"Line {line} is currently busy ({production_state[line]['status']})"

    job = None
    if order_id:
        job = next((j for j in job_queue if str(j.get('order_id')) == str(order_id)), None)
    else:
        job = next((j for j in job_queue if j.get('status') == 'QUEUED' and (not j.get('line') or j.get('line') == line)), None)
        
    if not job:
        return False, "No jobs available in queue"
        
    max_lead = 0
    for m in job.get('materials', []):
        m_name = m.get('name', '')
        m_lead = m.get('lead_time')
        curr_lead = 0
        if m_lead:
            try: curr_lead = int(m_lead)
            except: pass
        elif m_name:
            config_materials = factory_config.get('materials', {})
            for km, l in config_materials.items():
                if km.lower() in m_name.lower() or m_name.lower() in km.lower():
                    curr_lead = max(curr_lead, l)
        max_lead = max(max_lead, curr_lead)

    machines = get_machines_list(line)
    mins_per_unit = sum(m[1] for m in machines)
    total_mins = mins_per_unit * job.get('quantity', 1)
    
    production_state[line]['order_id'] = job['order_id']
    production_state[line]['quantity'] = job.get('quantity', 1)
    production_state[line]['materials'] = job.get('materials', [])
    production_state[line]['material'] = job.get('material', '')
    production_state[line]['lead_time'] = f"{total_mins} mins"
    production_state[line]['status'] = 'READY'
    if machines:
        production_state[line]['current_machine'] = machines[0][0]
        production_state[line]['current_machine_index'] = 0
    
    job['status'] = 'READY'
    job['line'] = line
    
    save_job_queue()
    save_production_state()
    
    socketio.emit('line_update', {
        'line': line, 
        'status': 'READY', 
        'state': production_state[line],
        'timestamp': datetime.now().isoformat()
    })
    socketio.emit('jobs_imported', {"count": 0, "jobs": job_queue})
    return True, f"Job {job['order_id']} deployed to {line}"

@app.route('/api/jobs/deploy', methods=['POST'])
def deploy_job():
    """Move a job from queue to active production state"""
    try:
        data = request.json
        line = data.get('line', 'door')
        order_id = data.get('order_id')
        success, msg = do_deploy_job(line, order_id)
        if success:
            return jsonify({"success": True, "message": msg}), 200
        else:
            status = 404 if "No jobs" in msg else 400
            return jsonify({"error": msg}), status
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/activity/delete', methods=['POST'])
def delete_activity():
    """Delete a record from the activity CSV"""
    try:
        data = request.json
        batch_id = data.get('batch_id')
        timestamp = data.get('timestamp')
        
        query = {}
        if batch_id: query['batch_id'] = str(batch_id)
        elif timestamp: query['timestamp'] = timestamp
        else: return jsonify({"error": "Batch ID or Timestamp required"}), 400
            
        res = events_col.delete_one(query)
        if res.deleted_count > 0:
            return jsonify({"success": True, "message": "Record deleted"}), 200
        else:
            return jsonify({"error": "Record not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs/clear', methods=['POST'])
def clear_queue():
    """Wipe the entire job queue"""
    global job_queue
    try:
        jobs_col.delete_many({})
        load_job_queue()
        socketio.emit('jobs_imported', {"count": 0, "jobs": job_queue})
        return jsonify({"success": True, "message": "Queue cleared"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/activity/clear-logs', methods=['POST'])
def clear_logs():
    """Wipe the activity log CSV"""
    try:
        events_col.delete_many({})
        return jsonify({"success": True, "message": "Logs cleared"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    global factory_config
    if request.method == 'POST':
        factory_config = request.json
        save_factory_config()
        socketio.emit('config_update', factory_config)
        return jsonify({"success": True}), 200
    return jsonify(factory_config), 200

def do_advance_job(line):
    """Refactored core logic for job advancement"""
    if line not in production_state:
        return False, "Invalid line"
        
    state = production_state[line]
    current_idx = state.get('current_machine_index', 0)
    order_id = state.get('order_id')
    
    if not order_id:
        return False, "No active job on this line"
        
    machines_list = get_machines_list(line)
    
    if current_idx + 1 < len(machines_list):
        next_idx = current_idx + 1
        next_m = machines_list[next_idx][0]
        print(f"⏩ Advancement: Moving {line} to {next_m} (idx: {next_idx})")
        state['current_machine'] = next_m
        state['current_machine_index'] = next_idx
        state['start_time'] = datetime.now().isoformat()
    else:
        # End of line
        complete_job_on_line(line)
        
    save_job_queue()
    socketio.emit('line_update', {'line': line, 'status': state['status'], 'state': {**state, 'all_machines': get_all_machines(line)}})
    socketio.emit('jobs_imported', {"jobs": job_queue})
    
    # Log to MongoDB
    try:
        events_col.insert_one({
            "timestamp": datetime.now().isoformat(),
            "line": line,
            "order_id": order_id,
            "machine": state.get('current_machine'),
            "status": state.get('status'),
            "action": "ADVANCE"
        })
    except Exception as e:
        print(f"❌ Error logging advance to MongoDB: {e}")

    return True, "Job advanced"

@app.route('/api/jobs/advance', methods=['POST'])
def advance_job():
    """Manually advance a job to the next machine or line"""
    line = request.json.get('line')
    success, msg = do_advance_job(line)
    if success:
        return jsonify({"success": True, "message": msg}), 200
    else:
        return jsonify({"error": msg}), 400

@app.route('/api/activity/logs', methods=['GET'])
def get_activity_logs():
    """Read the last 100 rows from the activity CSV"""
    try:
        cursor = events_col.find({}, {"_id": 0}).sort([("timestamp", pymongo.DESCENDING)]).limit(100)
        logs = list(cursor)
        return jsonify(logs), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/import-jobs', methods=['POST'])
def import_jobs():
    """Import job orders from Excel or CSV"""
    global job_queue
    
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    try:
        # Try reading as CSV first, then Excel if it fails
        try:
            df = pd.read_csv(file)
            # If it's a binary file (like Excel), read_csv might throw a UnicodeDecodeError or have 1 column
            if len(df.columns) <= 1:
                raise ValueError("Possible binary file")
        except:
            file.seek(0) # Reset file pointer
            df = pd.read_excel(file)
            
        if df is None or df.empty:
            return jsonify({"error": "The uploaded file is empty or could not be read."}), 400
            
        # Standardize column names (lowercase)
        df.columns = [c.lower().replace(' ', '_').strip() for c in df.columns]
        
        # Column Aliases
        aliases = {
            'line': ['production_line', 'line_name', 'work_line', 'line'],
            'quantity': ['qty', 'amount', 'units', 'quantity', 'batch_id'],
            'order_id': ['order', 'id', 'job_no', 'order_id'],
            'material': ['material_type', 'item', 'material'],
            'lead_time': ['lead', 'lead_time', 'custom_lead_time', 'lead_days']
        }
        
        # Map aliases
        for target, options in aliases.items():
            for opt in options:
                if opt in df.columns and target not in df.columns:
                    df.rename(columns={opt: target}, inplace=True)

        # Smart fallback for 'line' if missing but 'machine' is present
        if 'line' not in df.columns and 'machine' in df.columns:
            def guess_line(m):
                m = str(m).lower()
                door_m = [n.lower() for n, _ in get_machines_list("door")]
                frame_m = [n.lower() for n, _ in get_machines_list("frame")]
                if m in door_m: return 'door'
                if m in frame_m: return 'frame'
                return 'arch'
            df['line'] = df['machine'].apply(guess_line)

        # Smart fallback for 'quantity' if missing (default to 1)
        if 'quantity' not in df.columns:
            df['quantity'] = 1

        required_cols = ['line', 'quantity']
        for col in required_cols:
            if col not in df.columns:
                return jsonify({"error": f"Missing required column: {col}. Found columns: {list(df.columns)}"}), 400
        
        new_jobs = df.to_dict('records')
        
        # Add to queue and optionally update active line if IDLE
        for i, job in enumerate(new_jobs):
            line_val = str(job.get('line', '')).lower()
            # Map common names to internal keys
            if 'door' in line_val: line = 'door'
            elif 'frame' in line_val: line = 'frame'
            elif 'arch' in line_val or 'architrave' in line_val: line = 'arch'
            else: line = 'door' # Default

            # Auto-generate Order ID if missing
            order_id = job.get('order_id')
            if not order_id or str(order_id).lower() == 'nan' or str(order_id).strip() == "":
                order_id = f"AUTO-{int(time.time() * 1000) % 1000000 + i}"

            if line in production_state and production_state[line]['status'] == 'STOPPED':
                # Calculate bottleneck lead time
                max_lead = 0
                mat_name = job.get('material', '')
                custom_lead = job.get('lead_time')
                if custom_lead:
                    try: max_lead = int(custom_lead)
                    except: pass
                elif mat_name:
                    config_materials = factory_config.get('materials', {})
                    for km, l in config_materials.items():
                        if km.lower() in mat_name.lower() or mat_name.lower() in km.lower():
                            max_lead = max(max_lead, l)

                # Auto-assign first job to line if it's stopped
                production_state[line]['quantity'] = int(job.get('quantity', 0))
                production_state[line]['material'] = job.get('material')
                production_state[line]['order_id'] = order_id
                production_state[line]['lead_time'] = max_lead if max_lead > 0 else None
                if 'machine' in job:
                    production_state[line]['current_machine'] = job['machine']
            
            job_doc = {
                **job,
                "order_id": order_id,
                "line": line, # Use standardized key
                "imported_at": datetime.now().isoformat(),
                "status": "QUEUED"
            }
            
            # Save to MongoDB
            try:
                jobs_col.insert_one(job_doc)
            except Exception as e:
                print(f"❌ Error importing job to MongoDB: {e}")
            
        print(f"📥 Imported {len(new_jobs)} jobs from {file.filename}")
        load_job_queue()
        socketio.emit('jobs_imported', {"count": len(new_jobs), "jobs": job_queue})
        
        return jsonify({
            "success": True, 
            "message": f"Successfully imported {len(new_jobs)} jobs",
            "count": len(new_jobs)
        }), 200
        
    except Exception as e:
        print(f"✗ Import error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    """Get the current job queue"""
    return jsonify({"jobs": job_queue}), 200

def format_time(seconds):
    """Format seconds to HH:MM:SS"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

@app.route('/')
def dashboard():
    """Serve the dashboard HTML"""
    try:
        with open('dashboard.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
            from flask import Response
            return Response(html_content, mimetype='text/html')
    except FileNotFoundError:
        return "Dashboard file not found. Please ensure dashboard.html exists.", 404

# Start the automation loop
eventlet.spawn(automation_loop)

if __name__ == '__main__':
    # CSV functionality replaced by MongoDB
    
    # Get local IP address
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print("\n" + "="*60)
    print("🚀 ESP32 Batch Tracker Server")
    print("="*60)
    print(f"✓ Server running on: http://{SERVER_HOST}:{SERVER_PORT}")
    print(f"✓ Local IP: {local_ip}")
    print(f"\n📡 ESP32 Connection Endpoints:")
    print(f"   Data endpoint:  http://{local_ip}:{SERVER_PORT}/data")
    print(f"   Test endpoint:  http://{local_ip}:{SERVER_PORT}/api/test")
    print(f"   Command check:  http://{local_ip}:{SERVER_PORT}/api/command")
    print(f"\n🌐 Dashboard:")
    print(f"   URL: http://{local_ip}:{SERVER_PORT}/")
    print(f"   Or:  http://localhost:{SERVER_PORT}/")
    print(f"\n📊 API Endpoints:")
    print(f"   Status: http://{local_ip}:{SERVER_PORT}/api/server/status")
    print(f"   ESP32:  http://{local_ip}:{SERVER_PORT}/api/esp32/status")
    print(f"\n💾 Database (MongoDB):")
    print(f"   Database:  guardsify_db")
    print(f"   Connected: Yes")
    print(f"\n⚙️  Configuration (.env file):")
    print(f"   WiFi SSID: {WIFI_SSID}")
    print(f"   Machine: {MACHINE_NAME}")
    print(f"   Server IP (ESP32): {local_ip}")
    print("="*60)
    print("\n✅ Arduino code has been automatically updated!")
    print(f"   ESP32 will connect to: {local_ip}:{SERVER_PORT}")
    print("\n📡 Waiting for ESP32 data...\n")
    
    # Use config values for server host and port
    # 0.0.0.0 allows connections from external devices (like your ESP32)
    # Use socketio.run instead of app.run for WebSocket support
    socketio.run(app, host=SERVER_HOST, port=SERVER_PORT, debug=DEBUG_MODE, allow_unsafe_werkzeug=True)
    