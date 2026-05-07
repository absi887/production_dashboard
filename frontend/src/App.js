
import React, { useState, useEffect } from 'react';
import io from 'socket.io-client';
import axios from 'axios';
import {
  Play, Pause, Square, Trash2, Plus, Zap, LayoutDashboard,
  Settings, FileText, Clock, AlertCircle, Info, Download, ChevronUp, ChevronDown
} from 'lucide-react';
import './App.css';

const SERVER_URL = 'http://localhost:5002';

// --- Utility Components ---

const renderStatusBadge = (status) => {
  const classes = {
    RUNNING: 'badge-running',
    STOPPED: 'badge-stopped',
    PAUSED: 'badge-paused',
    FINISHED: 'badge-finished',
    QUEUED: 'badge-queued',
    READY: 'badge-running'
  };
  return <span className={`badge ${classes[status] || 'badge-queued'}`}>{status}</span>;
};

const MachineStepper = ({ current, currentIndex: explicitIndex, machines }) => {
  if (!machines || machines.length === 0) return null;
  const currentIndex = explicitIndex !== undefined ? explicitIndex : machines.indexOf(current);

  return (
    <div className="stepper-container">
      <div className="stepper-track">
        {machines.map((m, i) => (
          <div key={`${m}-${i}`} className={`step ${i < currentIndex ? 'completed' : i === currentIndex ? 'active' : ''}`}>
            <div className="step-dot"></div>
            <div className="step-label">{m}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

const LogView = () => {
  const [logs, setLogs] = useState([]);
  useEffect(() => {
    axios.get(`${SERVER_URL}/api/activity/logs`).then(res => setLogs(res.data));
  }, []);

  return (
    <div className="logs-view">
      <div className="section-header">
        <h2>System Activity Logs</h2>
        <p>Full audit trail of production events</p>
      </div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Source</th>
              <th>Line</th>
              <th>Machine</th>
              <th>Action</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log, i) => (
              <tr key={i}>
                <td className="log-time">{new Date(log.timestamp).toLocaleString()}</td>
                <td><span className={`source-badge ${log.source?.toLowerCase()}`}>{log.source || 'Arduino'}</span></td>
                <td>{log.line?.toUpperCase()}</td>
                <td>{log.machine || '---'}</td>
                <td><span className="log-event">{log.event}</span></td>
                <td>{renderStatusBadge(log.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const ProductionLineView = ({ line, state, configMachines, onDeploy, onStatusChange, onAdvance, queue }) => {
  const machines = configMachines ? configMachines.map(m => m[0]) : [];

  // Calculate expected time for current stage
  let currentStageTime = 0;
  if (state.current_machine && configMachines) {
    const match = configMachines.find(m => m[0] === state.current_machine);
    if (match) currentStageTime = match[1];
  }

  return (
    <div className="line-focus-view stacked">
      <div className="line-card focus">
        <div className="line-header">
          <h3>{line.toUpperCase()} LINE</h3>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
            {renderStatusBadge(state.status)}
            {state.decision && <span style={{ fontSize: '0.75rem', color: 'var(--accent)', fontWeight: '600' }}>{state.decision}</span>}
          </div>
        </div>

        <div className="line-info-grid">
          <div className="info-box">
            <label>Batch Quantity</label>
            <div className="value-large">{state.quantity || 0}</div>
          </div>
          <div className="info-box">
            <label>Active Job ID</label>
            <div className="value-large">{state.order_id || 'IDLE'}</div>
          </div>
          <div className="info-box">
            <label>Current Stage</label>
            <div className="value-large">{state.current_machine || '---'}</div>
            {currentStageTime > 0 && <div style={{ fontSize: '0.9rem', color: 'var(--text-dim)', marginTop: '4px' }}>Expected: {currentStageTime * (state.quantity || 1)} mins</div>}
          </div>
        </div>

        <MachineStepper
          current={state.current_machine}
          currentIndex={state.order_id ? (state.current_machine_index !== undefined ? state.current_machine_index : machines.indexOf(state.current_machine)) : -1}
          machines={machines}
        />

        <div className="line-actions large">
          <div className="primary-actions" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', width: '100%' }}>
            <button
              className="btn-deploy-next"
              onClick={onDeploy}
              disabled={state.status !== 'STOPPED' && state.status !== 'FINISHED'}
              style={{
                background: (state.status !== 'STOPPED' && state.status !== 'FINISHED') ? 'rgba(0,0,0,0.05)' : 'var(--primary)',
                color: (state.status !== 'STOPPED' && state.status !== 'FINISHED') ? 'rgba(0,0,0,0.3)' : '#ffffff',
                boxShadow: (state.status !== 'STOPPED' && state.status !== 'FINISHED') ? 'none' : ''
              }}
            >
              <Download size={18} /> Deploy Next
            </button>

            <button
              className="btn-deploy-next"
              onClick={() => state.status === 'PAUSED' ? onStatusChange('RESUME') : onStatusChange('START')}
              disabled={state.status !== 'READY' && state.status !== 'PAUSED'}
              style={{
                background: (state.status !== 'READY' && state.status !== 'PAUSED') ? 'rgba(0,0,0,0.05)' : 'var(--success)',
                color: (state.status !== 'READY' && state.status !== 'PAUSED') ? 'rgba(0,0,0,0.3)' : '#ffffff',
                boxShadow: (state.status !== 'READY' && state.status !== 'PAUSED') ? 'none' : ''
              }}
            >
              <Play size={18} /> {state.status === 'PAUSED' ? 'Resume Job' : 'Start Job'}
            </button>

            <button
              className="btn-deploy-next"
              onClick={onAdvance}
              disabled={state.status !== 'RUNNING'}
              style={{
                background: state.status !== 'RUNNING' ? 'rgba(0,0,0,0.05)' : 'linear-gradient(135deg, var(--accent), #d97706)',
                color: state.status !== 'RUNNING' ? 'rgba(0,0,0,0.3)' : '#ffffff',
                boxShadow: state.status !== 'RUNNING' ? 'none' : ''
              }}
            >
              <Zap size={18} /> Move to Next Stage
            </button>
          </div>
          <div className="line-controls">
            <button className="btn-pause" onClick={() => onStatusChange('PAUSE')} disabled={state.status !== 'RUNNING'}><Pause size={18} /></button>
            <button className="btn-stop" onClick={() => onStatusChange('END')} disabled={state.status === 'STOPPED' || state.status === 'FINISHED'}><Square size={18} /></button>
          </div>
        </div>
      </div>
    </div>
  );
};

const SettingsView = ({ config, onSave }) => {
  const [machines, setMachines] = useState(() => {
    const m = { ...config.machines };
    Object.keys(m).forEach(line => {
      m[line] = m[line].map((arr) => ({ id: Math.random().toString(36).substr(2, 9), name: arr[0], time: arr[1] }));
    });
    return m;
  });

  const [materials, setMaterials] = useState(() =>
    Object.entries(config.materials || {}).map(([name, lead]) => ({ id: Math.random().toString(36).substr(2, 9), name, lead }))
  );

  const handleAddMachine = (line) => {
    setMachines(prev => ({
      ...prev,
      [line]: [...prev[line], { id: Math.random().toString(36).substr(2, 9), name: "New Machine", time: 5 }]
    }));
  };

  const handleUpdateMachine = (line, id, field, value) => {
    setMachines(prev => ({
      ...prev,
      [line]: prev[line].map(m => m.id === id ? { ...m, [field]: value } : m)
    }));
  };

  const handleRemoveMachine = (line, id) => {
    setMachines(prev => ({
      ...prev,
      [line]: prev[line].filter(m => m.id !== id)
    }));
  };

  const handleMoveMachine = (line, index, direction) => {
    setMachines(prev => {
      const list = [...prev[line]];
      if (direction === 'up' && index > 0) {
        [list[index], list[index - 1]] = [list[index - 1], list[index]];
      } else if (direction === 'down' && index < list.length - 1) {
        [list[index], list[index + 1]] = [list[index + 1], list[index]];
      }
      return { ...prev, [line]: list };
    });
  };

  const handleAddMaterial = () => {
    setMaterials(prev => [...prev, { id: Math.random().toString(36).substr(2, 9), name: "New Material", lead: 10 }]);
  };

  const handleUpdateMaterial = (id, field, value) => {
    setMaterials(prev => prev.map(m => m.id === id ? { ...m, [field]: value } : m));
  };

  const handleRemoveMaterial = (id) => {
    setMaterials(prev => prev.filter(m => m.id !== id));
  };

  const saveSettings = () => {
    const formattedMachines = {};
    Object.keys(machines).forEach(line => {
      formattedMachines[line] = machines[line].map(m => [m.name, parseInt(m.time) || 0]);
    });

    const formattedMaterials = {};
    materials.forEach(m => {
      if (m.name.trim()) formattedMaterials[m.name.trim()] = parseInt(m.lead) || 0;
    });

    onSave({ ...config, machines: formattedMachines, materials: formattedMaterials });
  };

  return (
    <div className="settings-view" style={{ maxWidth: '1400px', margin: '0 auto' }}>
      <div className="settings-grid">
        <div className="settings-card">
          <div className="card-header">
            <h3>Production Sequences</h3>
            <p>Customize the workflow for each line</p>
          </div>
          {Object.entries(machines).map(([line, list]) => (
            <div key={line} className="config-section">
              <div className="section-header">
                <h4>{line.toUpperCase()} LINE SEQUENCE</h4>
                <button className="btn-add-mat" onClick={() => handleAddMachine(line)}>
                  <Plus size={14} /> Add Machine
                </button>
              </div>
              <div className="machine-list-edit">
                {list.map((m, i) => (
                  <div key={m.id} className="machine-row-edit">
                    <div className="seq-controls">
                      <button onClick={() => handleMoveMachine(line, i, 'up')} disabled={i === 0}>
                        <ChevronUp size={16} />
                      </button>
                      <button onClick={() => handleMoveMachine(line, i, 'down')} disabled={i === list.length - 1}>
                        <ChevronDown size={16} />
                      </button>
                    </div>
                    <input
                      placeholder="Machine Name"
                      value={m.name}
                      onChange={(e) => handleUpdateMachine(line, m.id, 'name', e.target.value)}
                    />
                    <div className="input-with-unit">
                      <input
                        type="number"
                        value={m.time}
                        onChange={(e) => handleUpdateMachine(line, m.id, 'time', e.target.value)}
                      />
                      <span>mins</span>
                    </div>
                    <button className="btn-del-mat" onClick={() => handleRemoveMachine(line, m.id)}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="settings-card">
          <div className="card-header">
            <h3>Material Database</h3>
            <p>Manage lead times and availability</p>
          </div>
          <div className="section-header">
            <h4>MATERIALS & LEAD TIMES</h4>
            <button className="btn-add-mat" onClick={handleAddMaterial}>
              <Plus size={14} /> Add Material
            </button>
          </div>
          <div className="material-list-edit">
            {materials.map(m => (
              <div key={m.id} className="material-row-edit">
                <input
                  placeholder="Material Name"
                  value={m.name}
                  onChange={(e) => handleUpdateMaterial(m.id, 'name', e.target.value)}
                />
                <div className="input-with-unit">
                  <input
                    type="number"
                    value={m.lead}
                    onChange={(e) => handleUpdateMaterial(m.id, 'lead', e.target.value)}
                  />
                  <span>days</span>
                </div>
                <button className="btn-del-mat" onClick={() => handleRemoveMaterial(m.id)}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="settings-footer">
        <button className="btn-primary large" onClick={saveSettings}>Save Configuration</button>
      </div>
    </div>
  );
};

const ModernDialog = ({ isOpen, title, message, type = 'info', onConfirm, onCancel, confirmText = 'OK', cancelText = 'Cancel' }) => {
  if (!isOpen) return null;
  return (
    <div className="modal-overlay" style={{ zIndex: 2000 }}>
      <div className="modal-content dialog-modal" style={{ maxWidth: '400px' }}>
        <div className="modal-header" style={{ borderBottom: 'none', paddingBottom: '0.5rem' }}>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '1.4rem', color: type === 'error' ? 'var(--danger)' : type === 'warning' ? 'var(--warning)' : 'var(--primary)' }}>
            {type === 'error' ? <AlertCircle size={22} /> : type === 'warning' ? <AlertCircle size={22} /> : <Info size={22} />}
            {title}
          </h2>
        </div>
        <div className="modal-body" style={{ padding: '0.5rem 2rem 2rem 2rem', color: 'var(--text-dim)', fontSize: '1.05rem', lineHeight: '1.5' }}>
          {message}
        </div>
        <div className="modal-footer" style={{ padding: '1rem 2rem', background: 'transparent', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', gap: '0.75rem' }}>
          {onCancel && <button className="btn-secondary" onClick={onCancel} style={{ padding: '0.6rem 1.2rem', fontSize: '0.95rem' }}>{cancelText}</button>}
          <button
            className="btn-primary"
            style={{
              padding: '0.6rem 1.2rem',
              fontSize: '0.95rem',
              background: type === 'error' || type === 'warning' ? 'var(--danger)' : 'var(--primary)',
              boxShadow: type === 'error' || type === 'warning' ? '0 4px 12px rgba(239, 68, 68, 0.2)' : '0 4px 12px rgba(48, 84, 150, 0.3)'
            }}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
};

// --- Main App Component ---

function App() {
  const [socket, setSocket] = useState(null);
  const [productionState, setProductionState] = useState({
    door: { status: 'STOPPED', current_machine: null, batch_id: 0, quantity: 0, order_id: null, all_machines: [] },
    frame: { status: 'STOPPED', current_machine: null, batch_id: 0, quantity: 0, order_id: null, all_machines: [] },
    arch: { status: 'STOPPED', current_machine: null, batch_id: 0, quantity: 0, order_id: null, all_machines: [] },
  });
  const [jobQueue, setJobQueue] = useState([]);
  const [activeTab, setActiveTab] = useState('summary');
  const [config, setConfig] = useState({ machines: { door: [], frame: [], arch: [] }, materials: {} });
  const [showPlanner, setShowPlanner] = useState(false);
  const [plannerData, setPlannerData] = useState({
    order_id: `JOB-${Math.floor(Math.random() * 1000000)}`,
    quantity: 1,
    materials: []
  });
  const [globalAnalysis, setGlobalAnalysis] = useState(null);
  const [analysisResult, setAnalysisResult] = useState(null);
  const [systemStatus, setSystemStatus] = useState({ db: 'checking', esp32: 'checking', server: 'checking' });
  const [dialogConfig, setDialogConfig] = useState({ isOpen: false, title: '', message: '', type: 'info', onConfirm: null, onCancel: null, confirmText: 'OK', cancelText: 'Cancel' });

  const showDialog = (config) => {
    setDialogConfig({ ...config, isOpen: true });
  };

  const closeDialog = () => {
    setDialogConfig(prev => ({ ...prev, isOpen: false }));
  };

  useEffect(() => {
    const newSocket = io(SERVER_URL);
    setSocket(newSocket);

    // Initial fetch
    axios.get(`${SERVER_URL}/api/config`).then(res => {
      setConfig(res.data);
      setProductionState(prev => {
        const next = { ...prev };
        Object.keys(res.data.machines).forEach(line => {
          next[line] = {
            ...next[line],
            all_machines: res.data.machines[line].map(m => m[0])
          };
        });
        return next;
      });
    });

    newSocket.on('connect', () => {
      console.log('✓ Socket Connected');
      newSocket.emit('request_update');
    });

    newSocket.on('line_update', (data) => {
      setProductionState(prev => ({
        ...prev,
        [data.line]: { ...prev[data.line], ...data.state, status: data.status }
      }));
    });

    newSocket.on('jobs_imported', (data) => {
      setJobQueue(data.jobs || []);
    });

    newSocket.on('stats_update', (data) => {
      // Fetch global analysis whenever stats update
      axios.get(`${SERVER_URL}/api/predictions`).then(res => setGlobalAnalysis(res.data));
    });

    // Status polling
    const fetchStatus = () => {
      axios.get(`${SERVER_URL}/api/server/status`)
        .then(res => {
          setSystemStatus({
            db: res.data.db_connected ? 'online' : 'offline',
            esp32: res.data.esp32_online ? 'online' : 'offline',
            server: 'online'
          });
        })
        .catch(() => {
          setSystemStatus({ db: 'offline', esp32: 'offline', server: 'offline' });
        });
    };

    fetchStatus();
    const statusInterval = setInterval(fetchStatus, 5000);

    return () => {
      newSocket.close();
      clearInterval(statusInterval);
    };
  }, []);

  const sendCommand = (line, command) => {
    axios.post(`${SERVER_URL}/api/command`, { line, command });
  };

  const advanceJob = (line) => {
    axios.post(`${SERVER_URL}/api/jobs/advance`, { line })
      .then(() => socket?.emit('request_update'))
      .catch(err => showDialog({ title: 'Error', message: err.response?.data?.error || "Failed to advance job", type: 'error', onConfirm: closeDialog }));
  };

  const deployNextJob = (line) => {
    const nextInQueue = jobQueue.find(j => (j.line === line || !j.line) && j.status === 'QUEUED');
    if (nextInQueue) {
      axios.post(`${SERVER_URL}/api/jobs/deploy`, { line, order_id: nextInQueue.order_id })
        .then(() => socket?.emit('request_update'))
        .catch(err => showDialog({ title: 'Deployment Error', message: err.response?.data?.error || "Deployment failed", type: 'error', onConfirm: closeDialog }));
    } else {
      showDialog({ title: 'Queue Empty', message: "No jobs in queue for this line.", type: 'info', onConfirm: closeDialog });
    }
  };

  const deleteJob = (orderId) => {
    showDialog({
      title: 'Confirm Deletion',
      message: `Are you sure you want to delete job ${orderId}? This action cannot be undone.`,
      type: 'warning',
      confirmText: 'Delete',
      onConfirm: () => {
        axios.post(`${SERVER_URL}/api/jobs/delete`, { order_id: orderId })
          .then(() => socket?.emit('request_update'));
        closeDialog();
      },
      onCancel: closeDialog
    });
  };


  const handleShowPlanner = () => {
    setPlannerData(prev => ({
      ...prev,
      order_id: `JOB-${Math.floor(Math.random() * 1000000)}`,
      materials: [],
      quantity: 1,
      line: 'door'
    }));
    setAnalysisResult(null);
    setShowPlanner(true);
  };

  const handleToggleMaterial = (matName) => {
    setPlannerData(prev => {
      const exists = prev.materials.some(m => m.name === matName);
      if (exists) {
        return { ...prev, materials: prev.materials.filter(m => m.name !== matName) };
      } else {
        return { ...prev, materials: [...prev.materials, { name: matName }] };
      }
    });
  };

  const analyzeJob = () => {
    axios.post(`${SERVER_URL}/api/analyze-job`, plannerData)
      .then(res => setAnalysisResult(res.data))
      .catch(err => showDialog({ title: 'Analysis Error', message: err.response?.data?.error || "Analysis failed", type: 'error', onConfirm: closeDialog }));
  };

  const submitJob = () => {
    const payload = {
      ...plannerData,
      decision: analysisResult?.decision,
      start_days: analysisResult?.new_order_start_days,
      finish_days: analysisResult?.expected_finish_days
    };
    axios.post(`${SERVER_URL}/api/add-job`, payload)
      .then(() => {
        setShowPlanner(false);
        setAnalysisResult(null);
        socket?.emit('request_update');
      });
  };

  return (
    <div className="app-container">
      <aside className="sidebar">
        <div className="logo">
          <Zap size={24} color="#ffb800" fill="#ffb800" />
        </div>

        <nav className="side-nav">
          <button className={activeTab === 'summary' ? 'active' : ''} onClick={() => setActiveTab('summary')}>
            <LayoutDashboard size={20} /> Dashboard
          </button>
          <button className={activeTab === 'logs' ? 'active' : ''} onClick={() => setActiveTab('logs')}>
            <FileText size={20} /> Activity Logs
          </button>
          <button className={activeTab === 'settings' ? 'active' : ''} onClick={() => setActiveTab('settings')}>
            <Settings size={20} /> Settings
          </button>
        </nav>

        <div className="sidebar-footer">
          <div className="status-group">
            <div className="status-item">
              <div className={`status-dot ${systemStatus.server}`}></div>
              <span>System</span>
            </div>
            <div className="status-item">
              <div className={`status-dot ${systemStatus.db}`}></div>
              <span>Database</span>
            </div>
            <div className="status-item">
              <div className={`status-dot ${systemStatus.esp32}`}></div>
              <span>ESP32 Hub</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="top-bar">
          <div className="search-bar">
            <h1>{activeTab.toUpperCase()}</h1>
          </div>
          <div className="user-profile">
            <button className="btn-planner" onClick={handleShowPlanner}>
              <Plus size={18} /> New Job
            </button>
          </div>
        </header>

        <div className="dashboard-content">
          {activeTab === 'summary' && (
            <div className="all-lines-stack">
              {globalAnalysis && (
                <div className="global-analysis-banner">
                  <div className="analysis-text-content">
                    <div className="banner-tag"><Zap size={14} fill="currentColor" /> AI GLOBAL ADVISOR</div>
                    <h2>{globalAnalysis.decision}</h2>
                    <p>Live production forecast based on current queue and material lead times.</p>
                  </div>
                  <div className="banner-metrics">
                    <div className="banner-metric">
                      <label>Current order finish</label>
                      <span>{globalAnalysis.current_finish_days}<small>days</small></span>
                    </div>
                    <div className="banner-metric">
                      <label>New order can start</label>
                      <span>{globalAnalysis.new_order_start_days}<small>days</small></span>
                    </div>
                    <div className="banner-metric">
                      <label>New order finish</label>
                      <span>{globalAnalysis.expected_finish_days || (globalAnalysis.new_order_start_days + 4)}<small>days</small></span>
                    </div>
                  </div>
                </div>
              )}

              <div className="stats-grid">
                <div className="stat-card kpi-premium">
                  <div className="kpi-icon queue"><LayoutDashboard size={24} /></div>
                  <div className="kpi-content">
                    <div className="stat-label">Queue Backlog</div>
                    <div className="stat-value">{jobQueue.filter(j => j.status === 'QUEUED').length} <small>Jobs</small></div>
                    <div className="kpi-trend">Waiting for allocation</div>
                  </div>
                </div>

                <div className="stat-card kpi-premium">
                  <div className="kpi-icon efficiency"><Zap size={24} /></div>
                  <div className="kpi-content">
                    <div className="stat-label">Factory Efficiency</div>
                    <div className="stat-value">{globalAnalysis?.factory_efficiency || 0}<small>%</small></div>
                    <div className="kpi-trend positive">Real-time performance</div>
                  </div>
                </div>

                <div className="stat-card kpi-premium">
                  <div className="kpi-icon timeline"><Clock size={24} /></div>
                  <div className="kpi-content">
                    <div className="stat-label">Next Available</div>
                    <div className="stat-value">{globalAnalysis?.new_order_start_days || 0}<small>days</small></div>
                    <div className="kpi-trend">Projected readiness</div>
                  </div>
                </div>


              </div>

              <div className="lines-vertical-stack">
                {Object.entries(productionState).map(([line, state]) => (
                  <ProductionLineView
                    key={line}
                    line={line}
                    state={state}
                    configMachines={config.machines && config.machines[line]}
                    onDeploy={() => deployNextJob(line)}
                    onStatusChange={(cmd) => sendCommand(line, cmd)}
                    onAdvance={() => advanceJob(line)}
                    queue={jobQueue.filter(j => j.line === line || !j.line)}
                  />
                ))}
              </div>

              <div className="section-header" style={{ marginTop: '3rem' }}>
                <h2>Global Production Queue</h2>
              </div>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Order ID</th>
                      <th>Materials</th>
                      <th>Qty</th>
                      <th>Decision Maker Timeline</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobQueue.map(job => (
                      <tr key={job.order_id}>
                        <td><span className="job-id">{job.order_id}</span></td>
                        <td className="materials-cell"><span>{job.material}</span></td>
                        <td>{job.quantity}</td>
                        <td style={{ maxWidth: '300px', fontSize: '0.8rem', color: 'var(--text-dim)' }}>
                          {job.decision || '---'}
                        </td>
                        <td>{renderStatusBadge(job.status)}</td>
                        <td className="action-btns">
                          <button className="btn-delete" onClick={() => deleteJob(job.order_id)}><Trash2 size={16} /></button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {activeTab === 'logs' && <LogView />}
          {activeTab === 'settings' && (
            <SettingsView config={config} onSave={(c) => {
              axios.post(`${SERVER_URL}/api/config`, c).then(() => setConfig(c));
            }} />
          )}
        </div>
      </main>

      {showPlanner && (
        <div className="modal-overlay">
          <div className="modal-content planner-modal">
            <div className="modal-header">
              <h2>Add New Production Job</h2>
              <button className="btn-close" onClick={() => setShowPlanner(false)}>&times;</button>
            </div>
            <div className="modal-body">
              <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                <div className="form-group">
                  <label>Order ID</label>
                  <input value={plannerData.order_id} onChange={(e) => setPlannerData({ ...plannerData, order_id: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>Production Line</label>
                  <select
                    value={plannerData.line}
                    disabled
                    style={{ width: '100%', padding: '0.75rem', background: 'rgba(0,0,0,0.05)', border: '1px solid var(--border)', color: 'rgba(0,0,0,0.5)', borderRadius: '8px', cursor: 'not-allowed' }}
                  >
                    <option value="door">Door Line (Start)</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>Quantity</label>
                  <input type="number" min="1" value={plannerData.quantity} onChange={(e) => setPlannerData({ ...plannerData, quantity: parseInt(e.target.value) })} />
                </div>
              </div>

              <div className="materials-section" style={{ marginTop: '1.5rem' }}>
                <div className="section-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h3>Required Materials</h3>
                  <button
                    onClick={() => setPlannerData(prev => {
                      const allMats = Object.keys(config.materials || {});
                      return {
                        ...prev,
                        materials: prev.materials.length === allMats.length ? [] : allMats.map(name => ({ name }))
                      };
                    })}
                    style={{
                      background: 'rgba(56, 189, 248, 0.1)',
                      border: '1px solid rgba(56, 189, 248, 0.2)',
                      color: 'var(--accent)',
                      padding: '0.4rem 0.8rem',
                      borderRadius: '6px',
                      cursor: 'pointer',
                      fontSize: '0.9rem',
                      fontWeight: '600'
                    }}
                  >
                    {plannerData.materials.length === Object.keys(config.materials || {}).length ? "Deselect All" : "Select All"}
                  </button>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '1rem' }}>
                  {Object.entries(config.materials || {}).map(([mat, lead]) => {
                    const isSelected = plannerData.materials.some(m => m.name === mat);
                    return (
                      <div
                        key={mat}
                        onClick={() => handleToggleMaterial(mat)}
                        style={{
                          padding: '0.75rem 1rem',
                          borderRadius: '12px',
                          border: isSelected ? '1px solid var(--primary)' : '1px solid var(--border)',
                          background: isSelected ? 'rgba(48, 84, 150, 0.1)' : 'rgba(0, 0, 0, 0.03)',
                          color: isSelected ? 'var(--text)' : 'var(--text-dim)',
                          cursor: 'pointer',
                          fontSize: '1rem',
                          fontWeight: isSelected ? '600' : '400',
                          transition: 'all 0.2s',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between'
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                          <div style={{
                            width: '16px', height: '16px', borderRadius: '4px',
                            border: '1px solid var(--border)', background: isSelected ? 'var(--primary)' : 'transparent',
                            display: 'flex', alignItems: 'center', justifyContent: 'center'
                          }}>
                            {isSelected && <div style={{ width: '8px', height: '8px', borderRadius: '2px', background: 'var(--text)' }}></div>}
                          </div>
                          <span>{mat}</span>
                        </div>
                        <span style={{ fontSize: '0.85rem', color: isSelected ? 'var(--accent)' : 'var(--text-dim)', background: 'rgba(0,0,0,0.2)', padding: '2px 8px', borderRadius: '10px' }}>
                          {lead} days lead
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {analysisResult && (
                <div className="analysis-card">
                  <div className="analysis-header">
                    <Zap size={18} color="var(--accent)" />
                    <h4>Planning Results</h4>
                  </div>
                  <div className="analysis-metrics">
                    <div className="metric">
                      <label>Current order finish</label>
                      <span>{analysisResult.current_finish_days}<small>days</small></span>
                    </div>
                    <div className="metric">
                      <label>New order can start</label>
                      <span>{analysisResult.new_order_start_days}<small>days</small></span>
                    </div>
                    <div className="metric">
                      <label>New order finish</label>
                      <span>{analysisResult.expected_finish_days}<small>days</small></span>
                    </div>
                  </div>
                  <div className="analysis-note">
                    <Info size={20} />
                    <span>{analysisResult.decision}</span>
                  </div>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={analyzeJob}>Analyze Timeline</button>
              <button className="btn-primary" onClick={submitJob}>Add to Production Queue</button>
            </div>
          </div>
        </div>
      )}

      {dialogConfig.isOpen && (
        <ModernDialog
          {...dialogConfig}
          onConfirm={() => { if (dialogConfig.onConfirm) dialogConfig.onConfirm(); else closeDialog(); }}
          onCancel={dialogConfig.onCancel}
        />
      )}
    </div>
  );
}

export default App;
