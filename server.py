from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import csv
import os
from datetime import datetime
import json
import socket
import re
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration from .env file
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', 5002))
WIFI_SSID = os.getenv('WIFI_SSID', 'YOUR_WIFI')
WIFI_PASSWORD = os.getenv('WIFI_PASSWORD', '')
MACHINE_NAME = os.getenv('MACHINE_NAME', 'Wood_Line_1')
CSV_FILE = os.getenv('CSV_FILE', 'wood_line_data.csv')
DASHBOARD_TITLE = os.getenv('DASHBOARD_TITLE', 'ESP32 Batch Tracker - Production Dashboard')
DEBUG_MODE = os.getenv('DEBUG_MODE', 'True').lower() == 'true'

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
CORS(app)  # Enable CORS for ESP32 requests
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Command queue for ESP32 control
pending_command = None
command_lock = False

# ESP32 connection tracking
esp32_last_seen = None
esp32_connection_count = 0
esp32_last_batch_id = None

# Initialize the CSV with headers if it doesn't exist
def init_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            # All fields from ESP32 - matches the JSON payload exactly
            headers = [
                "timestamp", "batch_id", "event", "status", "machine",
                "active_runtime_s", "active_runtime_formatted",
                "total_elapsed_s", "total_elapsed_formatted",
                "batch_duration_s", "batch_duration_formatted",
                "total_pause_s", "total_pause_formatted",
                "pause_count", "avg_pause_duration_s",
                "efficiency_percent", "production_rate_per_hour",
                "wifi_rssi", "wifi_quality"
            ]
            writer.writerow(headers)
            print(f"✓ Created CSV file with headers: {CSV_FILE}")

@app.route('/data', methods=['POST', 'OPTIONS'])
def receive_data():
    global esp32_last_seen, esp32_connection_count, esp32_last_batch_id
    
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Get client IP for logging
        client_ip = request.remote_addr
        print(f"\n{'='*60}")
        print(f"📡 Incoming request from: {client_ip}")
        print(f"   Method: {request.method}")
        print(f"   Headers: {dict(request.headers)}")
        
        # Get JSON data from ESP32
        data = request.json
        if not data:
            # Try to get raw data for debugging
            raw_data = request.get_data(as_text=True)
            print(f"✗ Error: No JSON received")
            print(f"   Raw data: {raw_data[:200]}")
            return jsonify({"success": False, "error": "No JSON received", "received": raw_data[:100]}), 400
        
        event = data.get('event', 'UNKNOWN')
        batch_id = data.get('batch_id', 0)
        
        # Update ESP32 tracking
        esp32_last_seen = datetime.now()
        esp32_connection_count += 1
        esp32_last_batch_id = batch_id
        
        print(f"📥 Received {event} for Batch #{batch_id}")
        print(f"   Machine: {data.get('machine', 'N/A')}")
        print(f"   Status: {data.get('status', 'N/A')}")
        print(f"   Timestamp: {data.get('timestamp', 'N/A')}")
        print(f"   Total connections: {esp32_connection_count}")
        print(f"{'='*60}\n")
        
        # Prepare row data (order must match headers exactly)
        row = [
            data.get("timestamp", ""),
            data.get("batch_id", 0),
            data.get("event", ""),
            data.get("status", ""),
            data.get("machine", ""),
            data.get("active_runtime_s", 0),
            data.get("active_runtime_formatted", ""),
            data.get("total_elapsed_s", 0),
            data.get("total_elapsed_formatted", ""),
            data.get("batch_duration_s", 0),
            data.get("batch_duration_formatted", ""),
            data.get("total_pause_s", 0),
            data.get("total_pause_formatted", ""),
            data.get("pause_count", 0),
            data.get("avg_pause_duration_s", 0),
            data.get("efficiency_percent", 0.0),
            data.get("production_rate_per_hour", 0.0),
            data.get("wifi_rssi", 0),
            data.get("wifi_quality", "")
        ]
        
        # Save to CSV
        with open(CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(row)
            
        print(f"✓ Data saved successfully to {CSV_FILE}")
        
        # Emit WebSocket event to all connected clients
        socketio.emit('new_data', {
            'event': event,
            'batch_id': batch_id,
            'data': data,
            'timestamp': datetime.now().isoformat()
        })
        
        # Emit stats update
        try:
            stats = calculate_stats()
            socketio.emit('stats_update', stats)
        except:
            pass  # Don't fail if stats calculation fails
        
        # Return JSON response (ESP32 expects this)
        return jsonify({
            "success": True,
            "message": "Data saved successfully",
            "event": event,
            "batch_id": batch_id
        }), 200
        
    except Exception as e:
        error_msg = f"Error processing data: {str(e)}"
        print(f"✗ {error_msg}")
        return jsonify({"success": False, "error": error_msg}), 500

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
    global pending_command, command_lock
    
    try:
        data = request.json
        command = data.get('command', '').upper()
        
        valid_commands = ['START', 'PAUSE', 'RESUME', 'END']
        if command not in valid_commands:
            return jsonify({"error": f"Invalid command. Must be one of: {', '.join(valid_commands)}"}), 400
        
        pending_command = command
        command_lock = True
        
        # Emit WebSocket event
        socketio.emit('command_sent', {'command': command, 'timestamp': datetime.now().isoformat()})
        
        print(f"📤 Command set: {command}")
        return jsonify({
            "success": True,
            "command": command,
            "message": f"Command '{command}' queued for ESP32"
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/command', methods=['GET'])
def get_command():
    """Get pending command for ESP32 (ESP32 polls this)"""
    global pending_command, command_lock
    
    if pending_command and command_lock:
        command = pending_command
        pending_command = None
        command_lock = False
        return jsonify({"command": command}), 200
    
    return jsonify({"command": None}), 200

@app.route('/api/command/clear', methods=['POST'])
def clear_command():
    """Clear any pending command"""
    global pending_command, command_lock
    pending_command = None
    command_lock = False
    return jsonify({"success": True, "message": "Command cleared"}), 200

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
        
        return jsonify({
            "status": "running",
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "connections": len(socketio.server.manager.rooms.get('/', {}).get('', {})) if hasattr(socketio.server, 'manager') else 0,
            "esp32_last_seen": esp32_status,
            "esp32_connection_count": esp32_connection_count,
            "esp32_last_batch_id": esp32_last_batch_id,
            "csv_file_exists": os.path.exists(CSV_FILE),
            "csv_file_size": os.path.getsize(CSV_FILE) if os.path.exists(CSV_FILE) else 0,
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

@app.route('/api/esp32/status', methods=['GET'])
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
        stats = calculate_stats()
        emit('stats_update', stats)
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
    
    total_batches = 0
    total_runtime = 0
    total_pauses = 0
    total_events = 0
    efficiencies = []
    production_rates = []
    durations = []
    
    with open(CSV_FILE, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_events += 1
            if row['event'] == 'END':
                total_batches += 1
                duration = int(row.get('batch_duration_s', 0) or 0)
                total_runtime += duration
                durations.append(duration)
                total_pauses += int(row.get('pause_count', 0) or 0)
                eff = float(row.get('efficiency_percent', 0) or 0)
                rate = float(row.get('production_rate_per_hour', 0) or 0)
                if eff > 0:
                    efficiencies.append(eff)
                if rate > 0:
                    production_rates.append(rate)
    
    avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 0
    avg_production_rate = sum(production_rates) / len(production_rates) if production_rates else 0
    avg_duration = sum(durations) / len(durations) if durations else 0
    best_efficiency = max(efficiencies) if efficiencies else 0
    worst_efficiency = min(efficiencies) if efficiencies else 0
    
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
    """Get all batch data as JSON"""
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify({"data": [], "count": 0}), 200
        
        data = []
        with open(CSV_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data.append(row)
        
        return jsonify({"data": data, "count": len(data)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/batches', methods=['GET'])
def get_batches():
    """Get completed batches summary"""
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify({"batches": []}), 200
        
        batches = {}
        with open(CSV_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['event'] == 'END' and row['batch_id']:
                    batch_id = row['batch_id']
                    batches[batch_id] = {
                        'batch_id': batch_id,
                        'timestamp': row['timestamp'],
                        'duration_s': int(row.get('batch_duration_s', 0) or 0),
                        'duration_formatted': row.get('batch_duration_formatted', '00:00'),
                        'active_runtime_s': int(row.get('active_runtime_s', 0) or 0),
                        'efficiency': float(row.get('efficiency_percent', 0) or 0),
                        'pause_count': int(row.get('pause_count', 0) or 0),
                        'production_rate': float(row.get('production_rate_per_hour', 0) or 0)
                    }
        
        batch_list = list(batches.values())
        batch_list.sort(key=lambda x: int(x['batch_id']), reverse=True)
        
        return jsonify({"batches": batch_list, "count": len(batch_list)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get overall statistics with enhanced metrics"""
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify({
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
            }), 200
        
        total_batches = 0
        total_runtime = 0
        total_pauses = 0
        total_events = 0
        efficiencies = []
        production_rates = []
        durations = []
        
        with open(CSV_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_events += 1
                if row['event'] == 'END':
                    total_batches += 1
                    duration = int(row.get('batch_duration_s', 0) or 0)
                    total_runtime += duration
                    durations.append(duration)
                    total_pauses += int(row.get('pause_count', 0) or 0)
                    eff = float(row.get('efficiency_percent', 0) or 0)
                    rate = float(row.get('production_rate_per_hour', 0) or 0)
                    if eff > 0:
                        efficiencies.append(eff)
                    if rate > 0:
                        production_rates.append(rate)
        
        avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 0
        avg_production_rate = sum(production_rates) / len(production_rates) if production_rates else 0
        avg_duration = sum(durations) / len(durations) if durations else 0
        best_efficiency = max(efficiencies) if efficiencies else 0
        worst_efficiency = min(efficiencies) if efficiencies else 0
        
        return jsonify({
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
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/export', methods=['GET'])
def export_csv():
    """Export CSV file for download"""
    try:
        from flask import send_file
        if os.path.exists(CSV_FILE):
            return send_file(CSV_FILE, as_attachment=True, download_name=f'batch_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv')
        else:
            return jsonify({"error": "No data file found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/recent', methods=['GET'])
def get_recent():
    """Get recent activity (last N events)"""
    try:
        limit = int(request.args.get('limit', 50))
        if not os.path.exists(CSV_FILE):
            return jsonify({"data": [], "count": 0}), 200
        
        data = []
        with open(CSV_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            data = rows[-limit:] if len(rows) > limit else rows
        
        return jsonify({"data": data, "count": len(data)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trends', methods=['GET'])
def get_trends():
    """Get trends over time"""
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify({"trends": []}), 200
        
        trends = []
        with open(CSV_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['event'] == 'END':
                    trends.append({
                        'date': row['timestamp'].split(' ')[0] if ' ' in row['timestamp'] else row['timestamp'],
                        'batch_id': row['batch_id'],
                        'efficiency': float(row.get('efficiency_percent', 0) or 0),
                        'production_rate': float(row.get('production_rate_per_hour', 0) or 0),
                        'duration': int(row.get('batch_duration_s', 0) or 0)
                    })
        
        return jsonify({"trends": trends, "count": len(trends)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

if __name__ == '__main__':
    init_csv()
    
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
    print(f"\n💾 Data:")
    print(f"   CSV file: {CSV_FILE}")
    print(f"   Exists: {'Yes' if os.path.exists(CSV_FILE) else 'No'}")
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
    