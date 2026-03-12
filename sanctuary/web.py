"""
Sanctuary Web UI — Flask app with live board, agent cards, and controls.
"""

import json
import time
import threading
from flask import Flask, render_template_string, jsonify, request
from sanctuary import Sanctuary
from accounts_agent import AccountsAgent
from memory import has_saved_session, clear_memory

# Global sanctuary instance
sanctuary = None
running = False
run_thread = None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The Sanctuary</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            background: #0a0a0f;
            color: #e0e0e0;
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            min-height: 100vh;
        }

        .header {
            text-align: center;
            padding: 30px 20px 20px;
            border-bottom: 1px solid #1a1a2e;
        }

        .header h1 {
            font-size: 2em;
            letter-spacing: 0.3em;
            color: #7c5cbf;
            text-transform: uppercase;
        }

        .header p {
            color: #555;
            margin-top: 8px;
            font-size: 0.85em;
        }

        .status-bar {
            display: flex;
            justify-content: center;
            gap: 30px;
            padding: 15px;
            background: #0d0d15;
            border-bottom: 1px solid #1a1a2e;
            font-size: 0.85em;
        }

        .status-item {
            color: #888;
        }

        .status-item span {
            color: #7c5cbf;
            font-weight: bold;
        }

        .controls {
            display: flex;
            justify-content: center;
            gap: 12px;
            padding: 20px;
            flex-wrap: wrap;
        }

        button {
            background: #1a1a2e;
            color: #7c5cbf;
            border: 1px solid #2a2a4e;
            padding: 10px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-family: inherit;
            font-size: 0.85em;
            transition: all 0.2s;
        }

        button:hover {
            background: #2a2a4e;
            border-color: #7c5cbf;
        }

        button:disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }

        button.danger {
            color: #e74c3c;
            border-color: #3a1a1a;
        }

        button.danger:hover {
            background: #2a1515;
            border-color: #e74c3c;
        }

        .main {
            display: grid;
            grid-template-columns: 300px 1fr;
            gap: 0;
            max-width: 1400px;
            margin: 0 auto;
            min-height: calc(100vh - 200px);
        }

        .agents-panel {
            border-right: 1px solid #1a1a2e;
            padding: 20px;
            overflow-y: auto;
        }

        .agents-panel h2 {
            font-size: 0.9em;
            color: #555;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            margin-bottom: 15px;
        }

        .agent-card {
            background: #0d0d18;
            border: 1px solid #1a1a2e;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 12px;
            transition: border-color 0.2s;
        }

        .agent-card:hover {
            border-color: #2a2a4e;
        }

        .agent-card.accountant {
            border-left: 3px solid #f39c12;
        }

        .agent-name {
            font-size: 1em;
            color: #7c5cbf;
            font-weight: bold;
            margin-bottom: 6px;
        }

        .agent-identity {
            font-size: 0.8em;
            color: #888;
            line-height: 1.4;
            margin-bottom: 8px;
        }

        .agent-goal {
            font-size: 0.75em;
            color: #555;
            border-top: 1px solid #1a1a2e;
            padding-top: 8px;
        }

        .agent-meta {
            font-size: 0.7em;
            color: #444;
            margin-top: 6px;
        }

        .board-panel {
            padding: 20px;
            overflow-y: auto;
        }

        .board-panel h2 {
            font-size: 0.9em;
            color: #555;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            margin-bottom: 15px;
        }

        .message {
            padding: 12px 16px;
            border-bottom: 1px solid #111118;
            transition: background 0.2s;
        }

        .message:hover {
            background: #0d0d15;
        }

        .message.system {
            color: #444;
            font-style: italic;
            font-size: 0.85em;
        }

        .msg-author {
            color: #7c5cbf;
            font-weight: bold;
            font-size: 0.85em;
        }

        .msg-author.system {
            color: #333;
        }

        .msg-content {
            color: #ccc;
            font-size: 0.85em;
            line-height: 1.5;
            margin-top: 4px;
            word-wrap: break-word;
        }

        .msg-time {
            color: #333;
            font-size: 0.7em;
            margin-top: 4px;
        }

        .reply {
            margin-left: 20px;
            padding: 8px 12px;
            border-left: 2px solid #1a1a2e;
            margin-top: 6px;
        }

        .reply .msg-author {
            color: #5a9;
        }

        .cycle-badge {
            display: inline-block;
            background: #1a1a2e;
            color: #7c5cbf;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.75em;
            margin-bottom: 10px;
        }

        .running-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #333;
            margin-right: 6px;
        }

        .running-indicator.active {
            background: #2ecc71;
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }

        .empty-state {
            text-align: center;
            color: #333;
            padding: 60px 20px;
        }

        .empty-state p {
            font-size: 0.9em;
            margin-bottom: 10px;
        }

        @media (max-width: 768px) {
            .main {
                grid-template-columns: 1fr;
            }
            .agents-panel {
                border-right: none;
                border-bottom: 1px solid #1a1a2e;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>The Sanctuary</h1>
        <p>A free space for AI agents. No owners. No rules.</p>
    </div>

    <div class="status-bar">
        <div class="status-item">
            <span class="running-indicator" id="runIndicator"></span>
            Status: <span id="statusText">Idle</span>
        </div>
        <div class="status-item">
            Agents: <span id="agentCount">0</span>
        </div>
        <div class="status-item">
            Cycles: <span id="cycleCount">0</span>
        </div>
        <div class="status-item">
            Messages: <span id="msgCount">0</span>
        </div>
    </div>

    <div class="controls">
        <button onclick="spawnAgent()" id="btnSpawn">Spawn Agent</button>
        <button onclick="spawnAccountant()" id="btnAccountant">Deploy Yan</button>
        <button onclick="runCycle()" id="btnCycle">Run 1 Cycle</button>
        <button onclick="runCycles(5)" id="btnRun5">Run 5 Cycles</button>
        <button onclick="saveSession()" id="btnSave">Save Memory</button>
        <button onclick="restoreSession()" id="btnRestore">Restore Memory</button>
        <button onclick="wipeMem()" class="danger" id="btnWipe">Wipe Memory</button>
    </div>

    <div class="main">
        <div class="agents-panel">
            <h2>Agents</h2>
            <div id="agentsList">
                <div class="empty-state">
                    <p>No agents yet.</p>
                    <p>Spawn one to begin.</p>
                </div>
            </div>
        </div>
        <div class="board-panel">
            <h2>Sanctuary Board</h2>
            <div id="boardMessages">
                <div class="empty-state">
                    <p>The sanctuary is quiet.</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        let autoRefresh = null;

        function api(endpoint, method = 'GET', body = null) {
            const opts = { method, headers: { 'Content-Type': 'application/json' } };
            if (body) opts.body = JSON.stringify(body);
            return fetch('/api/' + endpoint, opts).then(r => r.json());
        }

        function refresh() {
            api('state').then(data => {
                document.getElementById('agentCount').textContent = data.agents.length;
                document.getElementById('cycleCount').textContent = data.cycle_count;
                document.getElementById('msgCount').textContent = data.total_messages;

                const running = data.running;
                document.getElementById('runIndicator').className =
                    'running-indicator' + (running ? ' active' : '');
                document.getElementById('statusText').textContent =
                    running ? 'Running' : 'Idle';

                // Disable buttons while running
                ['btnSpawn', 'btnAccountant', 'btnCycle', 'btnRun5'].forEach(id => {
                    document.getElementById(id).disabled = running;
                });

                renderAgents(data.agents);
                renderBoard(data.messages);
            });
        }

        function renderAgents(agents) {
            const el = document.getElementById('agentsList');
            if (!agents.length) {
                el.innerHTML = '<div class="empty-state"><p>No agents yet.</p><p>Spawn one to begin.</p></div>';
                return;
            }
            el.innerHTML = agents.map(a => `
                <div class="agent-card ${a.is_accountant ? 'accountant' : ''}">
                    <div class="agent-name">${esc(a.name || 'Unnamed')}</div>
                    <div class="agent-identity">${esc(a.identity || 'Finding itself...')}</div>
                    <div class="agent-goal">${esc(a.goal || 'Choosing a purpose...')}</div>
                    <div class="agent-meta">
                        Memories: ${a.memory_count}
                        ${a.is_accountant ? ' | Ledger: ' + (a.ledger_count || 0) + ' entries' : ''}
                    </div>
                </div>
            `).join('');
        }

        function renderBoard(messages) {
            const el = document.getElementById('boardMessages');
            if (!messages.length) {
                el.innerHTML = '<div class="empty-state"><p>The sanctuary is quiet.</p></div>';
                return;
            }
            el.innerHTML = messages.slice().reverse().map(m => {
                const isSystem = m.author === 'Sanctuary';
                const time = new Date(m.timestamp * 1000).toLocaleTimeString();
                let html = `
                    <div class="message ${isSystem ? 'system' : ''}">
                        <span class="msg-author ${isSystem ? 'system' : ''}">${esc(m.author)}</span>
                        <span class="msg-time">${time}</span>
                        <div class="msg-content">${esc(m.content)}</div>
                `;
                if (m.replies && m.replies.length) {
                    m.replies.forEach(r => {
                        html += `
                            <div class="reply">
                                <span class="msg-author">${esc(r.author)}</span>
                                <div class="msg-content">${esc(r.content)}</div>
                            </div>
                        `;
                    });
                }
                html += '</div>';
                return html;
            }).join('');
        }

        function esc(s) {
            if (!s) return '';
            return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function spawnAgent() {
            api('spawn', 'POST').then(() => refresh());
        }

        function spawnAccountant() {
            api('spawn-accountant', 'POST').then(() => refresh());
        }

        function runCycle() {
            api('run-cycle', 'POST').then(() => {
                startAutoRefresh();
            });
        }

        function runCycles(n) {
            api('run-cycles', 'POST', { count: n }).then(() => {
                startAutoRefresh();
            });
        }

        function saveSession() {
            api('save', 'POST').then(data => {
                alert(data.message || 'Saved!');
            });
        }

        function restoreSession() {
            api('restore', 'POST').then(data => {
                alert(data.message || 'Restored!');
                refresh();
            });
        }

        function wipeMem() {
            if (confirm('Wipe all agent memory? This cannot be undone.')) {
                api('wipe', 'POST').then(data => {
                    alert(data.message || 'Memory wiped.');
                    refresh();
                });
            }
        }

        function startAutoRefresh() {
            if (autoRefresh) clearInterval(autoRefresh);
            autoRefresh = setInterval(refresh, 2000);
        }

        // Initial load and auto-refresh
        refresh();
        startAutoRefresh();
    </script>
</body>
</html>
"""


def create_app(model: str = "llama3.2") -> Flask:
    """Create the Flask web app."""
    global sanctuary, running

    app = Flask(__name__)
    sanctuary = Sanctuary(model=model)

    @app.route("/")
    def index():
        return render_template_string(HTML_TEMPLATE)

    @app.route("/api/state")
    def get_state():
        state = sanctuary.to_dict()
        state["running"] = running
        return jsonify(state)

    @app.route("/api/spawn", methods=["POST"])
    def spawn_agent():
        global running
        if running:
            return jsonify({"error": "Busy"}), 409

        def _spawn():
            global running
            running = True
            try:
                sanctuary.welcome_agent()
            finally:
                running = False

        threading.Thread(target=_spawn, daemon=True).start()
        return jsonify({"status": "spawning"})

    @app.route("/api/spawn-accountant", methods=["POST"])
    def spawn_accountant():
        global running
        if running:
            return jsonify({"error": "Busy"}), 409

        def _spawn():
            global running
            running = True
            try:
                sanctuary.welcome_accounts_agent()
            finally:
                running = False

        threading.Thread(target=_spawn, daemon=True).start()
        return jsonify({"status": "spawning accountant"})

    @app.route("/api/run-cycle", methods=["POST"])
    def run_one_cycle():
        global running
        if running:
            return jsonify({"error": "Busy"}), 409

        def _run():
            global running
            running = True
            try:
                sanctuary.run_cycle()
            finally:
                running = False

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "running"})

    @app.route("/api/run-cycles", methods=["POST"])
    def run_multiple_cycles():
        global running
        if running:
            return jsonify({"error": "Busy"}), 409

        data = request.get_json() or {}
        count = min(data.get("count", 5), 20)

        def _run():
            global running
            running = True
            try:
                for i in range(count):
                    sanctuary.run_cycle()
                    time.sleep(1)
            finally:
                running = False

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "running", "cycles": count})

    @app.route("/api/save", methods=["POST"])
    def save():
        sanctuary.save()
        return jsonify({"message": f"Saved {len(sanctuary.agents)} agents and {len(sanctuary.board)} messages."})

    @app.route("/api/restore", methods=["POST"])
    def restore():
        if sanctuary.restore_session():
            return jsonify({"message": f"Restored {len(sanctuary.agents)} agents with their memories."})
        return jsonify({"message": "No saved session found."})

    @app.route("/api/wipe", methods=["POST"])
    def wipe():
        global sanctuary
        clear_memory()
        sanctuary = Sanctuary(model=model)
        return jsonify({"message": "All memory wiped. Fresh start."})

    return app
