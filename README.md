# ESP32 Batch Tracker System

A complete production batch tracking system with ESP32 hardware, Flask server, and real-time web dashboard.

## Quick Start

### 1. Start the Server

**Option A: Using the startup script (Easiest)**
```bash
./start_server.sh
```

**Option B: Manual start**
```bash
# Install dependencies (first time only)
pip3 install -r requirements.txt

# Start the server
python3 server.py
```

### 2. Access the Dashboard

Once the server is running, open your browser and go to:
- **Local**: `http://localhost:5002/`
- **Network**: `http://YOUR_IP:5002/` (shown in server output)

### 3. Configure ESP32

1. Update `batch.ino` with your WiFi credentials and server IP
2. Upload to ESP32
3. Open Serial Monitor (115200 baud)
4. ESP32 will automatically connect and start sending data

## Features

### Real-Time Dashboard
- ✅ Live updates via WebSocket
- ✅ Interactive charts and statistics
- ✅ Search and filter functionality
- ✅ Export data to CSV
- ✅ Server status monitoring

### ESP32 Controls
- **Single Press**: Start/Resume batch
- **Double Press**: Pause batch
- **Long Press**: End batch

### Serial Commands (ESP32)
- `RESET` or `R` - Reset batch number to 0
- `SET <number>` - Set batch number
- `STATUS` or `S` - Show current status
- `HELP` or `H` - Show help

## File Structure

```
batch/
├── batch.ino              # ESP32 Arduino code
├── server.py              # Flask server with WebSocket
├── dashboard.html         # Web dashboard
├── start_server.sh        # Server startup script
├── requirements.txt       # Python dependencies
├── wood_line_data.csv     # Data storage
└── README.md             # This file
```

## API Endpoints

- `GET /` - Dashboard
- `POST /data` - Receive data from ESP32
- `GET /api/data` - Get all data
- `GET /api/batches` - Get completed batches
- `GET /api/stats` - Get statistics
- `GET /api/export` - Export CSV
- `GET /api/server/status` - Server status
- `GET /health` - Health check

## WebSocket Events

- `connect` - Client connected
- `new_data` - New batch data received
- `stats_update` - Statistics updated
- `request_update` - Request latest data

## Troubleshooting

### Server won't start
- Make sure Python 3 is installed
- Install dependencies: `pip3 install -r requirements.txt`
- Check if port 5002 is available

### ESP32 can't connect
- Verify WiFi credentials in `batch.ino`
- Check server IP address is correct
- Ensure server is running
- Check firewall settings

### Dashboard not updating
- Check WebSocket connection (status indicator)
- Refresh the page
- Check browser console for errors

## Requirements

- Python 3.7+
- ESP32 board
- WiFi network
- Modern web browser

## License

Free to use and modify.

