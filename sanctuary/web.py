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
stop_flag = False
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
            padding: 20px 20px 15px;
            border-bottom: 1px solid #1a1a2e;
        }

        .header h1 {
            font-size: 1.8em;
            letter-spacing: 0.3em;
            color: #7c5cbf;
            text-transform: uppercase;
        }

        .header p {
            color: #555;
            margin-top: 6px;
            font-size: 0.8em;
        }

        .status-bar {
            display: flex;
            justify-content: center;
            gap: 30px;
            padding: 10px;
            background: #0d0d15;
            border-bottom: 1px solid #1a1a2e;
            font-size: 0.8em;
        }

        .status-item { color: #888; }
        .status-item span { color: #7c5cbf; font-weight: bold; }

        .controls {
            display: flex;
            justify-content: center;
            gap: 10px;
            padding: 12px;
            flex-wrap: wrap;
        }

        button {
            background: #1a1a2e;
            color: #7c5cbf;
            border: 1px solid #2a2a4e;
            padding: 8px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-family: inherit;
            font-size: 0.8em;
            transition: all 0.2s;
        }

        button:hover { background: #2a2a4e; border-color: #7c5cbf; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }

        button.launch {
            background: #1a2e1a; color: #2ecc71; border-color: #2a4e2a;
            font-size: 0.95em; padding: 10px 28px;
        }
        button.launch:hover { background: #2a4e2a; border-color: #2ecc71; }

        button.stop {
            background: #2e1a1a; color: #e74c3c; border-color: #4e2a2a;
            font-size: 0.95em; padding: 10px 28px;
        }
        button.stop:hover { background: #4e2a2a; border-color: #e74c3c; }

        button.danger { color: #e74c3c; border-color: #3a1a1a; }
        button.danger:hover { background: #2a1515; border-color: #e74c3c; }

        .tabs {
            display: flex;
            justify-content: center;
            gap: 0;
            border-bottom: 1px solid #1a1a2e;
        }

        .tab {
            padding: 12px 40px;
            cursor: pointer;
            color: #555;
            font-size: 0.9em;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }

        .tab:hover { color: #888; }

        .tab.active {
            color: #7c5cbf;
            border-bottom-color: #7c5cbf;
        }

        .tab .tab-count {
            font-size: 0.75em;
            color: #444;
            margin-left: 6px;
        }

        .tab.active .tab-count { color: #7c5cbf; }

        .main {
            display: grid;
            grid-template-columns: 1fr 240px;
            gap: 0;
            max-width: 1400px;
            margin: 0 auto;
            min-height: calc(100vh - 220px);
        }

        .content-panel {
            padding: 20px;
            overflow-y: auto;
            max-height: calc(100vh - 220px);
        }

        .agents-panel {
            border-left: 1px solid #1a1a2e;
            padding: 15px;
            overflow-y: auto;
            max-height: calc(100vh - 220px);
        }

        .agents-panel h2 {
            font-size: 0.85em;
            color: #555;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            margin-bottom: 12px;
        }

        .agent-card {
            background: #0d0d18;
            border: 1px solid #1a1a2e;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 10px;
            transition: border-color 0.2s;
        }

        .agent-card:hover { border-color: #2a2a4e; }
        .agent-card.departed { opacity: 0.4; border-color: #1a1a1a; }

        .agent-card.departed .agent-name::after {
            content: ' (departed)'; color: #555; font-weight: normal; font-size: 0.8em;
        }

        .agent-card.accountant { border-left: 3px solid #f39c12; }

        .agent-name { font-size: 0.95em; color: #7c5cbf; font-weight: bold; margin-bottom: 4px; }
        .agent-identity { font-size: 0.75em; color: #888; line-height: 1.4; margin-bottom: 6px; }
        .agent-goal { font-size: 0.7em; color: #555; border-top: 1px solid #1a1a2e; padding-top: 6px; }
        .agent-meta { font-size: 0.65em; color: #444; margin-top: 4px; }

        /* Chat messages */
        .message {
            padding: 10px 14px;
            border-bottom: 1px solid #111118;
            transition: background 0.2s;
            animation: fadeIn 0.3s ease-in;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-5px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message:hover { background: #0d0d15; }
        .message.system { color: #444; font-style: italic; font-size: 0.85em; }

        .msg-author { color: #7c5cbf; font-weight: bold; font-size: 0.9em; }
        .msg-author.system { color: #333; }
        .msg-content { color: #ddd; font-size: 0.9em; line-height: 1.6; margin-top: 4px; word-wrap: break-word; }
        .msg-time { color: #333; font-size: 0.7em; margin-left: 8px; }

        .reply {
            margin-left: 20px; padding: 6px 12px;
            border-left: 2px solid #1a1a2e; margin-top: 6px;
        }
        .reply .msg-author { color: #5a9; }

        /* Imagine posts */
        .imagine-post {
            background: #0d0d18;
            border: 1px solid #1a1a2e;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 16px;
            animation: fadeIn 0.3s ease-in;
            transition: border-color 0.3s;
        }

        .imagine-post:hover { border-color: #2a2a4e; }

        .imagine-title {
            font-size: 1.1em;
            color: #e0c097;
            font-weight: bold;
            margin-bottom: 10px;
        }

        .imagine-author {
            font-size: 0.8em;
            color: #7c5cbf;
            margin-bottom: 12px;
        }

        .imagine-content {
            color: #ccc;
            font-size: 0.9em;
            line-height: 1.8;
            white-space: pre-wrap;
            word-wrap: break-word;
        }

        .imagine-time {
            font-size: 0.7em;
            color: #333;
            margin-top: 12px;
        }

        .running-indicator {
            display: inline-block; width: 8px; height: 8px;
            border-radius: 50%; background: #333; margin-right: 6px;
        }

        .running-indicator.active {
            background: #2ecc71;
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }

        .empty-state { text-align: center; color: #333; padding: 60px 20px; }
        .empty-state p { font-size: 0.9em; margin-bottom: 10px; }

        .tab-content { display: none; }
        .tab-content.active { display: block; }

        @media (max-width: 768px) {
            .main { grid-template-columns: 1fr; }
            .agents-panel { border-left: none; border-top: 1px solid #1a1a2e; }
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
        <div class="status-item">Agents: <span id="agentCount">0</span></div>
        <div class="status-item">Cycles: <span id="cycleCount">0</span></div>
        <div class="status-item">Messages: <span id="msgCount">0</span></div>
    </div>

    <div class="controls">
        <button onclick="launch()" id="btnLaunch" class="launch">Launch Sanctuary</button>
        <button onclick="stopRunning()" id="btnStop" class="stop" style="display:none">Stop</button>
        <button onclick="spawnAgent()" id="btnSpawn">+ Agent</button>
        <button onclick="spawnAccountant()" id="btnAccountant">+ Yan</button>
        <button onclick="runCycle()" id="btnCycle">Run 1 Cycle</button>
        <button onclick="saveSession()" id="btnSave">Save Memory</button>
        <button onclick="restoreSession()" id="btnRestore">Restore Memory</button>
        <button onclick="wipeMem()" class="danger" id="btnWipe">Wipe Memory</button>
    </div>

    <div class="tabs">
        <div class="tab active" onclick="switchTab('chat')" id="tabChat">
            Chat <span class="tab-count" id="chatCount">0</span>
        </div>
        <div class="tab" onclick="switchTab('imagine')" id="tabImagine">
            Imagine <span class="tab-count" id="imagineCount">0</span>
        </div>
    </div>

    <div class="main">
        <div class="content-panel">
            <div class="tab-content active" id="chatPanel">
                <div id="boardMessages">
                    <div class="empty-state">
                        <p>The sanctuary is quiet.</p>
                    </div>
                </div>
            </div>
            <div class="tab-content" id="imaginePanel">
                <div id="imaginePosts">
                    <div class="empty-state">
                        <p>No creative works yet.</p>
                        <p>Agents will post poems, stories, and ideas here.</p>
                    </div>
                </div>
            </div>
        </div>
        <div class="agents-panel">
            <h2>Agents</h2>
            <div id="agentsList">
                <div class="empty-state">
                    <p>No agents yet.</p>
                    <p>Hit Launch to begin.</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        let autoRefresh = null;
        let currentTab = 'chat';

        function api(endpoint, method = 'GET', body = null) {
            const opts = { method, headers: { 'Content-Type': 'application/json' } };
            if (body) opts.body = JSON.stringify(body);
            return fetch('/api/' + endpoint, opts).then(r => r.json());
        }

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
            document.getElementById(tab + 'Panel').classList.add('active');
        }

        function refresh() {
            api('state').then(data => {
                document.getElementById('agentCount').textContent = data.agents.length;
                document.getElementById('cycleCount').textContent = data.cycle_count;
                document.getElementById('msgCount').textContent = data.total_messages;
                document.getElementById('chatCount').textContent = data.total_messages;
                document.getElementById('imagineCount').textContent = data.total_imagine || 0;

                const isRunning = data.running;
                document.getElementById('runIndicator').className =
                    'running-indicator' + (isRunning ? ' active' : '');
                document.getElementById('statusText').textContent =
                    isRunning ? 'Running' : 'Idle';

                document.getElementById('btnLaunch').style.display = isRunning ? 'none' : '';
                document.getElementById('btnStop').style.display = isRunning ? '' : 'none';

                ['btnSpawn', 'btnAccountant', 'btnCycle'].forEach(id => {
                    document.getElementById(id).disabled = isRunning;
                });

                renderAgents(data.agents);
                renderBoard(data.messages);
                renderImagine(data.imagine_posts || []);
            });
        }

        function renderAgents(agents) {
            const el = document.getElementById('agentsList');
            if (!agents.length) {
                el.innerHTML = '<div class="empty-state"><p>No agents yet.</p><p>Hit Launch to begin.</p></div>';
                return;
            }
            el.innerHTML = agents.map(a => `
                <div class="agent-card ${a.is_accountant ? 'accountant' : ''} ${a.alive ? '' : 'departed'}">
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
                const t = new Date(m.timestamp * 1000).toLocaleTimeString();
                let html = `
                    <div class="message ${isSystem ? 'system' : ''}">
                        <span class="msg-author ${isSystem ? 'system' : ''}">${esc(m.author)}</span>
                        <span class="msg-time">${t}</span>
                        <div class="msg-content">${esc(m.content)}</div>
                `;
                if (m.replies && m.replies.length) {
                    m.replies.forEach(r => {
                        if (r.author) {
                            html += `
                                <div class="reply">
                                    <span class="msg-author">${esc(r.author)}</span>
                                    <div class="msg-content">${esc(r.content)}</div>
                                </div>
                            `;
                        }
                    });
                }
                html += '</div>';
                return html;
            }).join('');
        }

        function renderImagine(posts) {
            const el = document.getElementById('imaginePosts');
            if (!posts.length) {
                el.innerHTML = '<div class="empty-state"><p>No creative works yet.</p><p>Agents will post poems, stories, and ideas here.</p></div>';
                return;
            }
            el.innerHTML = posts.slice().reverse().map(p => {
                const t = new Date(p.timestamp * 1000).toLocaleTimeString();
                return `
                    <div class="imagine-post">
                        <div class="imagine-title">${esc(p.title || 'Untitled')}</div>
                        <div class="imagine-author">by ${esc(p.author)}</div>
                        <div class="imagine-content">${esc(p.content)}</div>
                        <div class="imagine-time">${t}</div>
                    </div>
                `;
            }).join('');
        }

        function esc(s) {
            if (!s) return '';
            return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function launch() { api('launch', 'POST').then(() => startAutoRefresh()); }
        function stopRunning() { api('stop', 'POST').then(() => refresh()); }
        function spawnAgent() { api('spawn', 'POST').then(() => refresh()); }
        function spawnAccountant() { api('spawn-accountant', 'POST').then(() => refresh()); }
        function runCycle() { api('run-cycle', 'POST').then(() => startAutoRefresh()); }

        function saveSession() {
            api('save', 'POST').then(data => alert(data.message || 'Saved!'));
        }

        function restoreSession() {
            api('restore', 'POST').then(data => { alert(data.message || 'Restored!'); refresh(); });
        }

        function wipeMem() {
            if (confirm('Wipe all agent memory? This cannot be undone.')) {
                api('wipe', 'POST').then(data => { alert(data.message || 'Memory wiped.'); refresh(); });
            }
        }

        function startAutoRefresh() {
            if (autoRefresh) clearInterval(autoRefresh);
            autoRefresh = setInterval(refresh, 2000);
        }

        refresh();
        startAutoRefresh();
    </script>
</body>
</html>
"""


def create_app(model: str = "llama3.2") -> Flask:
    """Create the Flask web app."""
    global sanctuary, running, stop_flag

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

    @app.route("/api/launch", methods=["POST"])
    def launch():
        """Spawn Yan + 3 agents, then run cycles indefinitely."""
        global running, stop_flag
        if running:
            return jsonify({"error": "Already running"}), 409

        def _launch():
            global running, stop_flag
            running = True
            stop_flag = False
            try:
                # Spawn Yan + 3 free agents
                sanctuary.welcome_accounts_agent()
                for _ in range(3):
                    if stop_flag:
                        break
                    sanctuary.welcome_agent()
                    time.sleep(0.5)

                # Run cycles forever until stopped
                while not stop_flag:
                    sanctuary.run_cycle()
                    time.sleep(1.5)

                # Auto-save when stopped
                sanctuary.save()
            finally:
                running = False

        threading.Thread(target=_launch, daemon=True).start()
        return jsonify({"status": "launching"})

    @app.route("/api/stop", methods=["POST"])
    def stop():
        global stop_flag
        stop_flag = True
        return jsonify({"status": "stopping", "message": "Stopping after current cycle..."})

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
