from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import csv
import os
from datetime import datetime
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
CORS(app)  # Enable CORS for ESP32 requests
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# The name of your data file
CSV_FILE = 'wood_line_data.csv'

# Command queue for ESP32 control
pending_command = None
command_lock = False

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
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Get JSON data from ESP32
        data = request.json
        if not data:
            print("✗ Error: No JSON received")
            return jsonify({"success": False, "error": "No JSON received"}), 400
        
        event = data.get('event', 'UNKNOWN')
        batch_id = data.get('batch_id', 0)
        
        print(f"📥 Received {event} for Batch #{batch_id}")
        print(f"   Machine: {data.get('machine', 'N/A')}")
        print(f"   Status: {data.get('status', 'N/A')}")
        
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
        process = psutil.Process(os.getpid())
        return jsonify({
            "status": "running",
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "connections": len(socketio.server.manager.rooms.get('/', {}).get('', {})) if hasattr(socketio.server, 'manager') else 0
        }), 200
    except:
        return jsonify({
            "status": "running",
            "websocket": "enabled"
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
    
    print("\n" + "="*50)
    print("🚀 ESP32 Batch Tracker Server")
    print("="*50)
    print(f"✓ Server running on: http://0.0.0.0:5002")
    print(f"✓ Local IP: {local_ip}")
    print(f"✓ ESP32 should connect to: http://{local_ip}:5002/data")
    print(f"✓ Dashboard available at: http://{local_ip}:5002/")
    print(f"✓ CSV file: {CSV_FILE}")
    print("="*50)
    print("Waiting for ESP32 data...\n")
    
    # 0.0.0.0 allows connections from external devices (like your ESP32)
    # Use socketio.run instead of app.run for WebSocket support
    socketio.run(app, host='0.0.0.0', port=5002, debug=True, allow_unsafe_werkzeug=True)
    