"""
Gradio 网页界面 — 多智能体协作平台
启动: python -m agent_platform.server.ui
前提: 先启动 API 服务
"""
import json
import html as html_mod

import requests

# ── Compatibility patch: Gradio 6 references starlette.status.HTTP_422_UNPROCESSABLE_CONTENT
#    which doesn't exist in starlette < 0.42. Alias it before Gradio imports it.
import starlette.status as _st
if not hasattr(_st, "HTTP_422_UNPROCESSABLE_CONTENT"):
    _st.HTTP_422_UNPROCESSABLE_CONTENT = _st.HTTP_422_UNPROCESSABLE_ENTITY

import gradio as gr

API_BASE = "http://127.0.0.1:8000"

from ..agents.definitions import AGENT_META

CSS = """
/* ═══════════════════════════════════════════════
   Dark — Indigo #6366f1 + Violet #8b5cf6
   ═══════════════════════════════════════════════ */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  /* Brand */
  --blue: #6366f1;
  --blue-hover: #4f46e5;
  --blue-light: rgba(99,102,241,0.12);
  --blue-ring: rgba(99,102,241,0.25);
  --blue-glow: rgba(99,102,241,0.35);
  --violet: #8b5cf6;
  --brand: #6366f1;
  --brand-2: #8b5cf6;
  --brand-glow: rgba(99,102,241,0.35);
  /* Surfaces */
  --white: #e8ecf4;
  --bg: #0a0e1a;
  --bg-elev: #121829;
  --bg-card: #1a2138;
  --bg-card-hi: #1f2842;
  --bg-sidebar: #121829;
  --bg-input: #1a2138;
  --bg-hover: #1f2842;
  /* Text */
  --text: #e8ecf4;
  --text-secondary: #9ba6bd;
  --text-muted: #5c6784;
  --txt: #e8ecf4;
  --txt-2: #9ba6bd;
  --txt-3: #5c6784;
  /* Borders */
  --border: rgba(255,255,255,0.07);
  --border-light: rgba(255,255,255,0.04);
  --border-hi: rgba(255,255,255,0.13);
  /* Agent colors */
  --green: #34d399;
  --green-bg: rgba(52,211,153,0.12);
  --green-glow: rgba(52,211,153,0.25);
  --red: #f87171;
  --red-bg: rgba(248,113,113,0.1);
  --amber: #f59e0b;
  --amber-bg: rgba(245,158,11,0.12);
  --amber-glow: rgba(245,158,11,0.25);
  --cyan: #22d3ee;
  --cyan-bg: rgba(34,211,238,0.12);
  --cyan-glow: rgba(34,211,238,0.25);
  --purple: #a78bfa;
  --purple-bg: rgba(167,139,250,0.12);
  /* Demo agent aliases */
  --pm: #f59e0b;
  --pm-bg: rgba(245,158,11,0.12);
  --pm-glow: rgba(245,158,11,0.25);
  --coder: #22d3ee;
  --coder-bg: rgba(34,211,238,0.12);
  --coder-glow: rgba(34,211,238,0.25);
  --qa: #34d399;
  --qa-bg: rgba(52,211,153,0.12);
  --qa-glow: rgba(52,211,153,0.25);
  /* Radius */
  --radius-sm: 10px;
  --radius: 14px;
  --radius-lg: 20px;
  --r-sm: 10px;
  --r: 14px;
  --r-lg: 20px;
  /* Shadows */
  --shadow-sm: 0 2px 8px rgba(0,0,0,0.3);
  --shadow: 0 8px 32px rgba(0,0,0,0.4);
  --sh: 0 2px 8px rgba(0,0,0,0.3);
  --sh-lg: 0 8px 32px rgba(0,0,0,0.4);
  /* Font */
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  --mono: "SF Mono", "Fira Code", ui-monospace, monospace;
  /* Layout */
  --sidebar-w: 240px;
  --header-h: 56px;
}

*, *::before, *::after { box-sizing: border-box; }

body {
  background:
    radial-gradient(ellipse 600px 500px at 25% 20%, rgba(99,102,241,0.12), transparent 60%),
    radial-gradient(ellipse 500px 400px at 80% 80%, rgba(139,92,246,0.10), transparent 60%),
    var(--bg) !important;
  background-attachment: fixed !important;
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.6;
  margin: 0; padding: 0;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}
/* Force all Gradio root wrappers transparent so body glow shows through */
.gradio-container, #component-0, .main, .gap, .app, .contain, .gr-group, .gr-box {
  background: transparent !important;
  max-width: 100% !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
}
footer { visibility: hidden; }
.progress-bar { display: none; }

::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }

/* ── Gradio Native Overrides ── */
.gr-button {
  font-family: var(--font) !important;
  font-weight: 500 !important;
  border-radius: var(--radius-sm) !important;
  transition: all 0.15s ease !important;
  border: none !important;
}
.gr-button-primary {
  background: linear-gradient(135deg, var(--blue), var(--violet)) !important;
  color: #fff !important;
  box-shadow: 0 4px 16px var(--blue-ring);
}
.gr-button-primary:hover { background: linear-gradient(135deg, var(--blue-hover), #7c3aed) !important; }
.gr-button-stop {
  background: transparent !important;
  color: var(--red) !important;
  border: 1px solid var(--red) !important;
}
.gr-button-stop:hover { background: var(--red-bg) !important; }
.gr-button-secondary {
  background: var(--bg-input) !important;
  color: var(--text) !important;
  border: 1px solid var(--border) !important;
}
.gr-button-secondary:hover {
  background: var(--bg-hover) !important;
  border-color: var(--blue) !important;
}
/* Gradio 6 native button selectors */
button.primary, button[variant="primary"] {
  background: linear-gradient(135deg, var(--blue), var(--violet)) !important;
  color: #fff !important;
  border: none !important;
  box-shadow: 0 4px 20px var(--blue-glow) !important;
}
button.primary:hover, button[variant="primary"]:hover {
  background: linear-gradient(135deg, var(--blue-hover), #7c3aed) !important;
  opacity: .9;
}
button.secondary, button[variant="secondary"] {
  background: var(--bg-card) !important;
  color: var(--text) !important;
  border: 1.5px solid var(--border) !important;
}
button.secondary:hover, button[variant="secondary"]:hover {
  background: var(--bg-card-hi) !important;
  border-color: var(--border-hi) !important;
}
button:active, .gr-button:active { transform: scale(.97) !important; }
button.stop, button[variant="stop"] {
  background: transparent !important;
  color: var(--red) !important;
  border: 1px solid var(--red) !important;
}
button.stop:hover, button[variant="stop"]:hover {
  background: var(--red-bg) !important;
}
.gr-button-sm { font-size: 12px !important; padding: 6px 14px !important; }
.gr-button-lg { font-size: 14px !important; padding: 10px 24px !important; }

.gr-input, textarea, input[type="text"], input[type="password"] {
  background: var(--bg-input) !important;
  border: 1.5px solid transparent !important;
  color: var(--text) !important;
  font-family: var(--font) !important;
  font-size: 14px !important;
  border-radius: var(--radius-sm) !important;
  padding: 10px 14px !important;
  transition: all 0.15s ease !important;
}
.gr-input:focus, textarea:focus, input:focus {
  border-color: var(--blue) !important;
  box-shadow: 0 0 0 3px var(--blue-ring) !important;
  outline: none !important;
  background: var(--bg-card-hi) !important;
}

.gr-input-label, label {
  color: var(--text-secondary) !important;
  font-weight: 500 !important;
  font-size: 13px !important;
}

.gr-dropdown, select {
  background: var(--bg-input) !important;
  border: 1.5px solid transparent !important;
  color: var(--text) !important;
  font-family: var(--font) !important;
  border-radius: var(--radius-sm) !important;
}

.gr-group { background: transparent !important; border: none !important; }
.gr-box { background: transparent !important; border: none !important; }
.gr-accordion {
  background: var(--bg-card) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  margin-bottom: 8px !important;
  box-shadow: var(--shadow-sm) !important;
}
.gr-accordion > .label-wrap {
  color: var(--text) !important;
  font-weight: 600 !important;
  font-size: 13px !important;
}
.gr-markdown { color: var(--text); }
.gr-markdown h3 { font-weight: 600; font-size: 16px; color: var(--text); }
.gr-markdown table { width: 100%; border-collapse: collapse; font-size: 13px; }
.gr-markdown th {
  text-align: left; padding: 8px 14px;
  color: var(--text-muted); font-weight: 500;
  border-bottom: 1px solid var(--border);
}
.gr-markdown td {
  padding: 8px 14px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-light);
}

/* ── Layout: Header ── */
.app-header {
  display: flex; align-items: center; justify-content: space-between;
  height: var(--header-h);
  padding: 0 24px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 30;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.header-logo {
  width: 32px; height: 32px;
  background: var(--blue);
  color: #fff;
  border-radius: var(--radius-sm);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 14px;
}
.header-name { font-weight: 700; font-size: 15px; color: var(--text); }
.header-ver {
  font-size: 11px; font-weight: 500;
  color: var(--blue); background: var(--blue-light);
  padding: 2px 8px; border-radius: 4px;
}

/* ── Layout: Sidebar Column (matches demo desktop-sidebar) ── */
.sidebar {
  width: var(--sidebar-w) !important;
  min-width: var(--sidebar-w) !important;
  max-width: var(--sidebar-w) !important;
  height: 100vh !important;
  background: var(--bg-elev) !important;
  border-right: 1px solid var(--border) !important;
  padding: 24px 16px !important;
  gap: 0 !important;
  position: sticky; top: 0;
  flex-shrink: 0 !important;
  display: flex !important;
  flex-direction: column !important;
}
/* Brand / Logo at top — like demo .sidebar-logo */
.sidebar-brand {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px 24px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}
.sidebar-logo {
  width: 40px; height: 40px;
  background: linear-gradient(135deg, var(--brand), var(--brand-2));
  color: #fff;
  border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 20px;
  flex-shrink: 0;
  box-shadow: 0 4px 16px var(--brand-glow);
}
.sidebar-brand-name {
  font-weight: 800; font-size: 15px; color: var(--text);
}
.sidebar-brand-ver {
  font-size: 11px; font-weight: 500;
  color: var(--txt-3);
}
/* User info — right below brand */
.sidebar-user {
  display: none;
}
.sidebar-avatar { display: none; }
.sidebar-username { display: none !important; }
.sidebar-logout { display: none !important; }
.sidebar-divider { display: none; }
.sidebar-section {
  font-size: 10px; font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 6px 8px;
}
/* Sidebar footer — user info at bottom like demo */
.sidebar-foot {
  padding: 16px 12px 8px;
  border-top: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
  margin-top: auto;
}
.sidebar-foot .sf-ava {
  width: 36px; height: 36px; border-radius: 50%;
  background: linear-gradient(135deg, var(--brand), var(--brand-2));
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 14px; color: #fff;
  flex-shrink: 0;
}
.sidebar-foot .sf-info { flex: 1; min-width: 0; }
.sidebar-foot .sf-name { font-size: 13px; font-weight: 700; color: var(--text); }
.sidebar-foot .sf-role { font-size: 11px; color: var(--txt-3); }
.sidebar-foot .sf-logout {
  background: none; border: none; color: var(--txt-3); cursor: pointer;
  font-size: 16px; padding: 4px 8px; border-radius: 6px;
  transition: all .15s;
}
.sidebar-foot .sf-logout:hover { color: var(--red); background: var(--red-bg); }
/* ── Sidebar Nav Radio (styled as buttons) ── */
.nav-radio {
  width: 100% !important;
}
.nav-radio .wrap {
  display: flex !important;
  flex-direction: column !important;
  gap: 2px !important;
}
.nav-radio label {
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  width: 100% !important;
  padding: 10px 12px !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--text-secondary) !important;
  background: transparent !important;
  border: none !important;
  border-radius: var(--radius-sm) !important;
  cursor: pointer !important;
  transition: all 0.12s ease !important;
  margin: 0 !important;
}
.nav-radio label:hover {
  background: var(--bg-hover) !important;
  color: var(--text) !important;
}
.nav-radio input[type="radio"] {
  display: none !important;
}
.nav-radio label.selected {
  background: linear-gradient(135deg, rgba(99,102,241,.15), rgba(139,92,246,.1)) !important;
  color: var(--blue) !important;
  font-weight: 600 !important;
  border: 1px solid rgba(99,102,241,.25) !important;
}

.nav-btn {
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  width: 100% !important;
  justify-content: flex-start !important;
  padding: 10px 12px !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--text-secondary) !important;
  background: transparent !important;
  border: none !important;
  border-radius: var(--radius-sm) !important;
  cursor: pointer !important;
  transition: all 0.12s ease !important;
}
.nav-btn:hover {
  background: var(--bg-hover) !important;
  color: var(--text) !important;
}
.nav-btn.active {
  background: var(--blue-light) !important;
  color: var(--blue) !important;
  font-weight: 600 !important;
}

/* ── Main Content Area ── */
.main-content {
  flex: 1;
  padding: 24px 32px 40px;
  min-height: calc(100vh - var(--header-h));
  overflow-y: auto;
  max-width: 1100px;
}
.main-content > .gr-group { gap: 12px !important; }

/* Workspace header */
.workspace-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0 16px;
}
.workspace-header .wh-greeting {
  display: flex; flex-direction: column; gap: 2px;
  font-size: 13px; color: var(--txt-2);
}
.workspace-header .wh-greeting .wh-hi { font-size: 13px; color: var(--txt-2); }
.workspace-header .wh-title {
  font-size: 20px; font-weight: 800; letter-spacing: -0.3px; color: var(--text);
}
.workspace-header .wh-avatar {
  width: 42px; height: 42px; border-radius: 50%;
  background: linear-gradient(135deg, var(--brand), var(--brand-2));
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 16px; color: #fff;
  flex-shrink: 0;
}
/* Section title (matches demo) */
.section-title {
  font-size: 13px; font-weight: 700; color: var(--txt-2);
  text-transform: uppercase; letter-spacing: 0.04em;
  padding: 8px 0 10px;
}

/* ── Task Input Card (matches demo) ── */
.task-input-card {
  background: linear-gradient(135deg, rgba(99,102,241,0.12), rgba(139,92,246,0.08)) !important;
  border: 1px solid rgba(99,102,241,0.2) !important;
  border-radius: var(--r-lg) !important;
  padding: 18px !important;
  margin-bottom: 16px !important;
}
.task-input-card .tic-label {
  font-size: 12px; font-weight: 700; color: var(--brand);
  text-transform: uppercase; letter-spacing: 0.08em;
  margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
}
.task-input-card .tic-textarea textarea {
  background: rgba(10,14,26,0.6) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--r) !important;
  padding: 12px 14px !important;
  color: var(--txt) !important;
  font-size: 14px !important;
  line-height: 1.6 !important;
  min-height: 72px !important;
  resize: none !important;
}
.task-input-card .tic-textarea textarea:focus {
  border-color: var(--brand) !important;
  box-shadow: none !important;
}
.task-input-card .tic-go-col {
  justify-content: flex-end !important;
  align-items: center !important;
  padding-left: 8px !important;
}
.task-input-card .tic-go {
  width: 48px !important;
  min-width: 48px !important;
  height: 48px !important;
  border-radius: 50% !important;
  padding: 0 !important;
  font-size: 18px !important;
  background: linear-gradient(135deg, var(--brand), var(--brand-2)) !important;
  box-shadow: 0 4px 16px var(--brand-glow) !important;
}
.task-input-card .tic-status input {
  background: transparent !important;
  border: none !important;
  color: var(--txt-2) !important;
  font-size: 12px !important;
  padding: 8px 0 0 !important;
  text-align: right !important;
}
.task-input-card .tic-status label { display: none !important; }

/* ── Cards ── */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  margin-bottom: 16px;
  box-shadow: var(--shadow-sm);
}
.card-title {
  font-size: 13px; font-weight: 600;
  color: var(--text-secondary);
  padding-bottom: 12px; margin-bottom: 16px;
  border-bottom: 1px solid var(--border-light);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* ── Status Bar ── */
.status-bar {
  display: flex; align-items: center; gap: 10px;
}
.status-dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--green);
}
.status-dot.off { background: var(--red); }
.status-dot.idle { background: #cbd5e1; }
.status-text { font-weight: 600; font-size: 14px; color: var(--text); }

/* ── Stepper ── */
.stepper {
  display: flex; align-items: center; justify-content: center; gap: 0;
  padding: 22px 32px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 16px;
  box-shadow: var(--shadow-sm);
}
.step-item { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.step-num {
  width: 40px; height: 40px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 15px;
  background: var(--bg-input); color: var(--text-muted);
  transition: all 0.3s ease;
}
.step-num.done { background: var(--green); color: #fff; }
.step-num.active {
  background: var(--blue); color: #fff;
  box-shadow: 0 0 0 4px var(--blue-ring);
}
.step-label {
  font-size: 11px; font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.04em;
  transition: color 0.3s ease;
}
.step-label.active { color: var(--blue); }
.step-label.done { color: var(--green); }
.step-line {
  width: 60px; height: 2px;
  background: var(--border); margin: 0 6px;
  transition: all 0.3s ease;
}
.step-line.done { background: var(--green); }
.step-line.active { background: linear-gradient(90deg, var(--green), var(--blue)); }

/* ── Agent Status (matches mobile-demo.html) ── */
.agents-row {
  display: flex; gap: 10px; flex-wrap: wrap;
  margin-bottom: 16px;
}
.agent-card {
  flex: 1 1 0; min-width: 0;
  padding: 14px; border-radius: var(--r);
  background: var(--bg-card); border: 1.5px solid var(--border);
  transition: all 0.3s; position: relative; overflow: hidden;
}
.agent-card.working {
  border-color: var(--c);
  background: var(--c-bg);
}
.agent-card.working::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, var(--c), transparent);
  animation: slideBar 1.5s ease infinite;
}
@keyframes slideBar { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
.agent-card.done {
  border-color: var(--ok, #34d399);
  background: rgba(52,211,153,0.06);
}
.agent-card .ic {
  width: 36px; height: 36px; border-radius: var(--r-sm);
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; background: var(--bg); margin-bottom: 8px;
}
.agent-card.working .ic { background: var(--c-bg); }
.agent-card .nm {
  font-size: 13px; font-weight: 700; margin-bottom: 2px; color: var(--txt);
}
.agent-card .rl {
  font-size: 11px; color: var(--txt-3);
}
.agent-card.working .rl { color: var(--c); }
.agent-card .ind {
  position: absolute; top: 12px; right: 12px;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--txt-3);
}
.agent-card.working .ind {
  background: var(--c);
  animation: blink 1s infinite;
}
.agent-card.done .ind { background: var(--ok, #34d399); }
@keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: .3; } }

/* ── Planning ── */
.plan-progress {
  height: 4px; background: var(--border-light); border-radius: 2px;
  margin-bottom: 14px; overflow: hidden;
}
.plan-progress-fill {
  height: 100%; background: var(--blue);
  border-radius: 2px; transition: width 0.5s ease;
}
.plan-abstract {
  font-size: 13px; color: var(--text-secondary);
  background: var(--bg);
  border-left: 3px solid var(--blue);
  padding: 10px 14px; margin-bottom: 10px;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.plan-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; border-radius: var(--radius-sm);
  background: var(--bg); margin-bottom: 3px;
  border: 1px solid transparent;
}
.plan-row.active { background: var(--blue-light); border-color: #bfdbfe; }
.plan-row-icon { font-size: 16px; }
.plan-row-title { font-weight: 600; font-size: 12px; color: var(--text); }
.plan-row-desc { font-size: 11px; color: var(--text-muted); }
.plan-tag {
  font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
  margin-left: auto; text-transform: uppercase; letter-spacing: 0.04em;
}
.plan-tag.waiting { background: var(--bg-input); color: var(--text-muted); }
.plan-tag.running { background: var(--blue-light); color: var(--blue); }
.plan-tag.done { background: var(--green-bg); color: var(--green); }
.plan-tag.redo { background: var(--red-bg); color: var(--red); }

/* ── Log ── */
.log-entry {
  margin-bottom: 4px;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.log-entry.working { border-left: 3px solid var(--blue); }
.log-entry.done { border-left: 3px solid var(--green); }
.log-entry summary {
  padding: 10px 14px; cursor: pointer;
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 500;
  color: var(--text-secondary); background: var(--bg);
  list-style: none;
}
.log-entry summary::-webkit-details-marker { display: none; }
.log-entry.working summary { color: var(--blue); }
.log-entry.done summary { color: var(--green); }
.log-badge {
  font-size: 9px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
  margin-left: auto; text-transform: uppercase; letter-spacing: 0.04em;
}
.log-badge.working { background: var(--blue); color: #fff; }
.log-badge.done { background: var(--green); color: #fff; }
.log-badge.idle { background: var(--bg-input); color: var(--text-muted); }
.log-body {
  padding: 14px 18px; background: var(--bg-card);
  border-top: 1px solid var(--border-light);
  font-size: 13px; line-height: 1.7;
  color: var(--text-secondary);
  white-space: pre-wrap; max-height: 240px; overflow-y: auto;
}

/* ── Code Block ── */
.code-block {
  background: #0d1220; color: #c9d1d9;
  padding: 18px; border-radius: var(--radius-sm);
  font-size: 13px; line-height: 1.7;
  overflow-x: auto; max-height: 480px;
  white-space: pre-wrap;
  font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
  border: 1px solid var(--border);
}

/* ── KB ── */
.kb-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.kb-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px 20px;
  min-width: 170px; flex: 1;
  box-shadow: var(--shadow-sm);
  transition: border-color 0.15s ease;
}
.kb-card:hover { border-color: var(--blue); }
.kb-card h4 { font-weight: 600; font-size: 13px; margin: 0 0 4px; color: var(--text); }
.kb-count { font-size: 30px; font-weight: 700; color: var(--blue); }
.kb-unit { font-size: 11px; color: var(--text-muted); }

/* ── Metrics ── */
.metrics-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.metric-card {
  flex: 1; min-width: 120px;
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px;
  text-align: center; box-shadow: var(--shadow-sm);
}
.metric-value { font-size: 28px; font-weight: 700; color: var(--blue); }
.metric-value.green { color: var(--green); }
.metric-value.red { color: var(--red); }
.metric-label { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

/* ── Data Table ── */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  text-align: left; padding: 8px 14px;
  color: var(--text-muted); font-weight: 500;
  border-bottom: 1px solid var(--border);
}
.data-table td {
  padding: 8px 14px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-light);
}

/* ── Empty State ── */
.empty-state {
  text-align: center; padding: 28px;
  color: var(--text-muted); font-size: 13px;
}

/* ── Auth Screen (matches mobile-demo.html exactly) ── */
.auth-screen {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  min-height: 100vh !important;
  background: var(--bg) !important;
  border: none !important;
  padding: 0 !important;
  gap: 0 !important;
  z-index: 999;
  overflow-y: auto;
  display: block !important;
}
.auth-screen > .form {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  width: 100% !important;
  height: 100% !important;
  max-width: none !important;
}
/* The Gradio column inside acts as login-wrap */
.auth-card {
  position: relative !important;
  z-index: 1;
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  padding: 60px 28px 40px !important;
  gap: 0 !important;
  width: 100% !important;
  max-width: 460px !important;
  margin: 0 auto !important;
  min-height: 100vh;
  display: flex !important;
  flex-direction: column !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
}
/* Decorative glow background — two radial circles like demo */
.auth-card::before {
  content: "";
  position: fixed;
  top: -20%; left: -30%;
  width: 400px; height: 400px;
  background: radial-gradient(circle, rgba(99,102,241,0.25), transparent 60%);
  border-radius: 50%;
  z-index: 0;
  pointer-events: none;
}
.auth-card::after {
  content: "";
  position: fixed;
  bottom: -10%; right: -30%;
  width: 350px; height: 350px;
  background: radial-gradient(circle, rgba(139,92,246,0.20), transparent 60%);
  border-radius: 50%;
  z-index: 0;
  pointer-events: none;
}
/* Logo */
.auth-logo {
  width: 72px !important; height: 72px !important;
  border-radius: 20px !important;
  background: linear-gradient(135deg, var(--brand), var(--brand-2)) !important;
  color: #fff !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
  font-size: 32px !important; font-weight: 800 !important;
  margin-bottom: 24px !important;
  box-shadow: 0 8px 32px var(--brand-glow) !important;
  position: relative; z-index: 1;
}
.auth-card-body { position: relative; z-index: 1; text-align: left; padding: 0; margin: 0; }
.auth-card-body h2 {
  font-size: 28px !important; font-weight: 800 !important;
  letter-spacing: -0.5px !important; margin: 0 0 8px !important;
  color: var(--text) !important; text-align: left !important; line-height: 1.25;
}
.auth-card-body .sub {
  font-size: 15px !important; color: var(--txt-2) !important;
  line-height: 1.6 !important; margin: 0 0 40px !important; text-align: left !important;
}
/* Fields */
.auth-card .gr-textbox {
  margin-bottom: 18px !important;
  position: relative; z-index: 1;
}
.auth-card label {
  display: block !important;
  font-size: 13px !important; font-weight: 600 !important;
  color: var(--txt-2) !important;
  margin-bottom: 8px !important;
  text-transform: none !important;
  letter-spacing: 0 !important;
}
.auth-card input[type="text"], .auth-card input[type="password"] {
  width: 100% !important;
  padding: 14px 16px !important;
  font-size: 15px !important;
  font-family: var(--font) !important;
  background: var(--bg-card) !important;
  border: 1.5px solid var(--border) !important;
  border-radius: var(--r) !important;
  color: var(--txt) !important;
  outline: none !important;
  transition: border-color .2s, box-shadow .2s !important;
}
.auth-card input[type="text"]:focus, .auth-card input[type="password"]:focus {
  border-color: var(--brand) !important;
  box-shadow: 0 0 0 4px var(--brand-glow) !important;
}
.auth-card input::placeholder { color: var(--txt-3) !important; }
/* Buttons */
.auth-card .gr-row {
  position: relative; z-index: 1; gap: 12px !important;
  margin-top: 0 !important;
}
.auth-card button[variant="primary"] {
  width: 100% !important;
  padding: 15px !important;
  font-size: 16px !important;
  font-weight: 700 !important;
  font-family: var(--font) !important;
  border: none !important;
  border-radius: var(--r) !important;
  background: linear-gradient(135deg, var(--brand), var(--brand-2)) !important;
  color: #fff !important;
  box-shadow: 0 4px 20px var(--brand-glow) !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 8px !important;
  transition: transform .15s, opacity .15s !important;
}
.auth-card button[variant="primary"]:hover { opacity: .9; }
.auth-card button[variant="primary"]:active { transform: scale(.97); }
.auth-card button[variant="primary"]::before {
  content: "→";
  font-size: 18px;
  margin-right: 4px;
}
.auth-card button[variant="secondary"] {
  width: 100% !important;
  padding: 12px !important;
  font-size: 14px !important;
  font-weight: 600 !important;
  font-family: var(--font) !important;
  background: var(--bg-card) !important;
  color: var(--txt) !important;
  border: 1.5px solid var(--border) !important;
  border-radius: var(--r) !important;
  margin-top: 12px !important;
}
.auth-card button[variant="secondary"]:hover {
  background: var(--bg-card-hi) !important;
  border-color: var(--border-hi) !important;
}
/* Login footer — agent dots */
.login-foot {
  position: relative; z-index: 1;
  margin-top: auto !important;
  text-align: center;
  color: var(--txt-3);
  font-size: 12px;
  padding-top: 40px;
}
.login-foot .lf-agents {
  display: flex; justify-content: center; gap: 20px; margin-bottom: 16px;
}
.login-foot .lf-agent {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  font-size: 11px; color: var(--txt-2);
}
.login-foot .lf-dot {
  width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
}
/* Hide the status textbox label area cleanly */
.auth-card .gr-textbox[label="状态"] { display: none !important; }

/* ── Dark theme Gradio overrides ── */
.gradio-container { background: var(--bg) !important; }
#component-0, .main, .gap { background: var(--bg) !important; }
.gr-box, .gr-group, .gr-form, .gr-panel {
  background: transparent !important;
  border-color: var(--border) !important;
  color: var(--text) !important;
}
.gr-accordion { background: var(--bg-card, #121829) !important; border-color: var(--border) !important; }
.gr-accordion > .label-wrap { color: var(--text) !important; }
.gr-input, textarea, input[type="text"], input[type="password"] {
  background: var(--bg-input) !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
}
.gr-input::placeholder, textarea::placeholder, input::placeholder { color: var(--text-muted) !important; }
.svelte-1iy1q5p, .gr-checkpoint-row, .gr-checkpoint-row span { color: var(--text-secondary) !important; }
footer { display: none !important; }

/* ══ Gradio 6 Svelte internals — force dark ══ */
.gradio-container { background: var(--bg) !important; color: var(--text) !important; }
#component-0, .app, .main, .gap, .contain, .block, .gradio-container .contain {
  background: transparent !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
}
/* All text inputs / textareas — include Gradio 6 internal classes */
input, textarea, .gr-input, .gr-textarea,
input[type="text"], input[type="password"], input[type="email"], input[type="search"],
.svelte-1pq5p4x, .svelte-1iy1q5p input, .svelte-1iy1q5p textarea {
  background: var(--bg-input) !important;
  color: var(--text) !important;
  border: 1.5px solid var(--border) !important;
  border-radius: var(--radius-sm) !important;
  caret-color: var(--brand) !important;
}
input:focus, textarea:focus, .gr-input:focus, .gr-textarea:focus {
  border-color: var(--brand) !important;
  box-shadow: 0 0 0 3px var(--brand-glow) !important;
  outline: none !important;
}
input::placeholder, textarea::placeholder { color: var(--text-muted) !important; }
/* Labels */
label, .gr-box label, .gr-form label, .svelte-1iy1q5p span,
.gr-input-label, .gr-checkbox label, .gr-radio label {
  color: var(--text-secondary) !important;
}
/* Headers / titles / paragraphs */
h1, h2, h3, h4, h5, h6, p, span, li, a { color: inherit; }
h1, h2, h3 { color: var(--text) !important; }
/* Panels / boxes / form wrappers */
.gr-box, .gr-form, .gr-panel, .gr-block,
.block.padded, .form, .gap, .gap.compact,
.panel, .panel_area, .chatbot, .chat_container {
  background: transparent !important;
  border-color: var(--border) !important;
  color: var(--text) !important;
}
/* Cards that should be dark card color */
.gr-card, .card {
  background: var(--bg-card) !important;
  border: 1px solid var(--border) !important;
  color: var(--text) !important;
}
/* Radio / checkbox */
.gr-radio, .gr-checkbox, .gr-dropdown {
  background: var(--bg-input) !important;
  color: var(--text) !important;
  border-color: var(--border) !important;
}
/* Svelte scroll region */
::-webkit-scrollbar-track { background: transparent !important; }
::-webkit-scrollbar-thumb { background: var(--text-muted) !important; }
/* Override body background once more in case Gradio injects light */
body, html, .gradio-container, #root, #app {
  background: var(--bg) !important;
  background-color: var(--bg) !important;
  color: var(--text) !important;
}
/* Tab bar */
.tab-nav, .tabitem, .tab-button, button[role="tab"] {
  background: transparent !important;
  color: var(--text-secondary) !important;
  border-color: var(--border) !important;
}
button[role="tab"][aria-selected="true"] {
  color: var(--text) !important;
  border-bottom-color: var(--brand) !important;
}
"""


def _e(text: str) -> str:
    return html_mod.escape(str(text)) if text else ""


def _req(method: str, path: str, token: str = "", **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{API_BASE}{path}"
    return getattr(requests, method)(url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)


# ═══════════════════════════════════════════
# 渲染函数
# ═══════════════════════════════════════════

def render_stepper(phase: int) -> str:
    steps = [("1", "产品经理"), ("2", "程序员"), ("3", "测试员")]
    parts = ['<div class="stepper">']
    for i, (num, label) in enumerate(steps):
        if phase == 5: s = "done"
        elif i + 1 < phase: s = "done"
        elif i + 1 == phase: s = "active"
        elif phase == 4 and i == 2: s = "active"
        else: s = ""
        chk = "&#10003;"
        parts.append(
            f'<div class="step-item">'
            f'<div class="step-num {s}">{chk if s == "done" else num}</div>'
            f'<div class="step-label {s}">{label}</div></div>'
        )
        if i < len(steps) - 1:
            if i + 1 < phase or phase == 5: cs = "done"
            elif i + 1 == phase: cs = "active"
            else: cs = ""
            parts.append(f'<div class="step-line {cs}"></div>')
    return "".join(parts) + "</div>"


def render_planning(task_plan: dict) -> str:
    steps = task_plan.get("steps", []) if task_plan else []
    if not steps:
        return '<div class="card"><div class="card-title">执行规划</div><div class="empty-state">等待主 Agent 制定计划...</div></div>'
    done_count = sum(1 for s in steps if s["status"] == "已完成")
    total = len(steps)
    pct = int(done_count / total * 100) if total else 0
    chips = {"等待中": ("waiting", "等待"), "执行中": ("running", "进行中"), "已完成": ("done", "完成"), "退回重做": ("redo", "重做")}

    html = '<div class="card"><div class="card-title">执行规划</div>'
    html += f'<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">{done_count}/{total} 已完成 · {pct}%</div>'
    html += f'<div class="plan-progress"><div class="plan-progress-fill" style="width:{pct}%"></div></div>'

    s = task_plan.get("summary", "")
    if s: html += f'<div class="plan-abstract">{_e(s[:500])}</div>'

    for i, step in enumerate(steps):
        st = step.get("status", "等待中")
        bc, bl = chips.get(st, ("waiting", st))
        ac = "active" if st == "执行中" else ""
        html += (
            f'<div class="plan-row {ac}">'
            f'<span class="plan-row-icon">{step.get("icon", "◆")}</span>'
            f'<div><div class="plan-row-title">{i+1}. {_e(step.get("name",""))} → {_e(step.get("agent",""))}</div>'
            f'<div class="plan-row-desc">{_e(step.get("description",""))}</div></div>'
            f'<span class="plan-tag {bc}">{bl}</span></div>'
        )
    return html + "</div>"


def render_agents(active_agent: str, agent_outputs: dict) -> str:
    done_names = [a["name"] for a in AGENT_META if agent_outputs.get(a["name"])]
    master_done = agent_outputs.get("主Agent") or agent_outputs.get("Master")
    if master_done:
        master_cc, master_st = "done", "已完成"
    elif active_agent in ("主Agent", "Master"):
        master_cc, master_st = "working", "工作中..."
    else:
        master_cc, master_st = "", "待命"
    cards = [
        f'<div class="agent-card {master_cc}" style="--c:var(--purple);--c-bg:var(--purple-bg)">'
        f'<div class="ind"></div>'
        f'<div class="ic">⬡</div>'
        f'<div class="nm">主 Agent</div>'
        f'<div class="rl">调度中心 · {master_st}</div></div>'
    ]
    agent_colors = {"产品经理": ("var(--pm)", "var(--pm-bg)"),
                    "程序员": ("var(--coder)", "var(--coder-bg)"),
                    "测试员": ("var(--qa)", "var(--qa-bg)")}
    agent_roles = {"产品经理": "需求分析", "程序员": "代码实现", "测试员": "质量验证"}
    for a in AGENT_META:
        name = a["name"]
        if name in done_names: cc, st = "done", "已完成"
        elif name == active_agent: cc, st = "working", "工作中..."
        else: cc, st = "", "待命"
        color, bg = agent_colors.get(name, ("var(--brand)", "var(--blue-light)"))
        role = agent_roles.get(name, a.get("role", ""))
        cards.append(
            f'<div class="agent-card {cc}" style="--c:{color};--c-bg:{bg}">'
            f'<div class="ind"></div>'
            f'<div class="ic">{a["icon"]}</div>'
            f'<div class="nm">{name}</div>'
            f'<div class="rl">{role} · {st}</div></div>'
        )
    return '<div class="agents-row">' + "".join(cards) + "</div>"


def render_log(agent_outputs: dict, active_agent: str, phase: int) -> str:
    if phase == 0:
        return '<div class="card"><div class="empty-state">点击 ▶ 开始任务，查看 Agent 协作过程</div></div>'
    entries = []
    for a in AGENT_META:
        name = a["name"]
        content = agent_outputs.get(name)
        if content:
            safe = _e(str(content)[:2000])
            if len(str(content)) > 2000: safe += "\n\n... (已截断)"
            entries.append(
                f'<div class="log-entry done"><details open>'
                f'<summary>{a["icon"]} {name}<span class="log-badge done">完成</span></summary>'
                f'<div class="log-body">{safe}</div></details></div>'
            )
        elif name == active_agent:
            entries.append(
                f'<div class="log-entry working"><details open>'
                f'<summary>{a["icon"]} {name}<span class="log-badge working">工作中</span></summary>'
                f'<div class="log-body">等待输出...</div></details></div>'
            )
        else:
            entries.append(
                f'<div class="log-entry"><details>'
                f'<summary>{a["icon"]} {name}<span class="log-badge idle">等待</span></summary>'
                f'<div class="log-body">尚未开始</div></details></div>'
            )
    return '<div class="card">' + "".join(entries) + "</div>"


def render_deliverable(code: str) -> str:
    if not code or code == "# 等待任务提交...":
        return '<div class="card"><div class="card-title">交付物</div><div class="empty-state">暂无交付物</div></div>'
    safe = _e(str(code)[:3000])
    if len(str(code)) > 3000: safe += "\n\n... (已截断)"
    return f'<div class="card"><div class="card-title">交付物 — 代码.py</div><div class="code-block">{safe}</div></div>'


# ═══════════════════════════════════════════
# 任务执行生成器 (SSE)
# ═══════════════════════════════════════════

def run_task_stream(user_request: str, token: str):
    empty_plan = render_planning({})
    empty_deliv = render_deliverable("")
    if not user_request.strip():
        yield render_stepper(0), empty_plan, render_agents("", {}), render_log({}, "", 0), "请输入需求", empty_deliv
        return
    if not token:
        yield render_stepper(0), empty_plan, render_agents("", {}), render_log({}, "", 0), "请先登录", empty_deliv
        return

    try:
        _req("get", "/status", token, timeout=5)
    except requests.RequestException:
        yield render_stepper(0), empty_plan, render_agents("", {}), render_log({}, "", 0), "无法连接 API 服务器", empty_deliv
        return

    try:
        resp = _req("get", f"/run/stream?task={requests.utils.quote(user_request)}", token, stream=True, timeout=600)
        if resp.status_code != 200:
            detail = ""
            try: detail = resp.json().get("detail", resp.text)
            except Exception: detail = resp.text
            yield render_stepper(0), empty_plan, render_agents("", {}), render_log({}, "", 0), f"错误: {detail}", empty_deliv
            return
    except requests.RequestException as e:
        yield render_stepper(0), empty_plan, render_agents("", {}), render_log({}, "", 0), f"请求失败: {e}", empty_deliv
        return

    last_phase = 0; last_plan = empty_plan; agent_outputs = {}
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "): continue
        try: event = json.loads(line[len("data: "):])
        except json.JSONDecodeError: continue

        et = event.get("event", "")
        if et == "progress":
            phase = event.get("phase", last_phase)
            active = event.get("active_agent", "")
            state = event.get("state", {})
            plan = state.get("任务规划", {})
            if isinstance(plan, str):
                try: plan = json.loads(plan)
                except (json.JSONDecodeError, TypeError): plan = {}
            cur_plan = render_planning(plan)
            if phase != last_phase or cur_plan != last_plan:
                yield (render_stepper(phase), cur_plan,
                       render_agents(active, agent_outputs),
                       render_log(agent_outputs, active, phase),
                       f"{active + ' 工作中...' if active else '执行中...'}", empty_deliv)
                last_phase = phase; last_plan = cur_plan
        elif et == "error":
            yield render_stepper(0), empty_plan, render_agents("", {}), render_log({}, "", 0), f"错误: {event.get('message','')}", empty_deliv
            return
        elif et == "complete":
            data = event.get("result", {})
            agent_outputs["产品经理"] = data.get("需求分析", "")
            code = data.get("代码", "")
            agent_outputs["程序员"] = code
            agent_outputs["测试员"] = data.get("测试报告", "")
            plan = data.get("任务规划", {})
            yield (render_stepper(5), render_planning(plan),
                   render_agents("", agent_outputs),
                   render_log(agent_outputs, "", 5),
                   f"完成 — 测试: {data.get('测试状态','N/A')} | 轮次: {data.get('主Agent调度轮数',0)}",
                   render_deliverable(code))
            return


# ═══════════════════════════════════════════
# 知识库操作
# ═══════════════════════════════════════════

def kb_list_collections(token: str) -> str:
    try:
        r = _req("get", "/kb/collections", token)
        if r.status_code != 200: return f"加载失败: {r.text[:200]}"
        cols = r.json().get("collections", [])
        if not cols: return '<div class="empty-state">暂无知识库</div>'
        cards = ['<div class="kb-grid">']
        for c in cols:
            cards.append(f'<div class="kb-card"><h4>{_e(c["name"])}</h4><div class="kb-count">{c["count"]}</div><div class="kb-unit">文本块</div></div>')
        cards.append("</div>")
        return "".join(cards)
    except Exception as e:
        return f"加载失败: {e}"


def kb_upload_text(collection: str, title: str, text: str, token: str) -> str:
    if not collection.strip() or not text.strip(): return "请填写知识库名称和内容"
    try:
        r = _req("post", f"/kb/collections/{collection.strip()}/upload", token, json={"text": text, "title": title or "untitled"})
        data = r.json()
        if r.status_code == 200: return f"已导入 {data.get('chunks', 0)} 个文本块到 '{collection.strip()}'"
        return f"失败: {data.get('detail', r.text)}"
    except Exception as e:
        return f"请求失败: {e}"


def kb_import_path(collection: str, path: str, token: str) -> str:
    if not collection.strip() or not path.strip(): return "请填写知识库名称和路径"
    try:
        r = _req("post", f"/kb/collections/{collection.strip()}/import", token, json={"path": path, "title": ""})
        data = r.json()
        if r.status_code == 200: return f"已导入 {data.get('chunks', 0)} 个文本块到 '{collection.strip()}'"
        return f"失败: {data.get('detail', r.text)}"
    except Exception as e:
        return f"请求失败: {e}"


def kb_search(collection: str, query: str, top_k: int, token: str) -> str:
    if not query.strip(): return "请输入搜索关键词"
    try:
        r = _req("get", f"/kb/collections/{collection.strip()}/search", token, params={"q": query, "top_k": top_k})
        if r.status_code != 200: return f"搜索失败: {r.text[:200]}"
        return _e(str(r.json().get("result", "无结果"))[:3000])
    except Exception as e:
        return f"请求失败: {e}"


def kb_delete(collection: str, token: str) -> str:
    if not collection.strip(): return "请填写知识库名称"
    try:
        r = _req("delete", f"/kb/collections/{collection.strip()}", token)
        if r.status_code == 200: return f"已删除 '{collection.strip()}'"
        return f"失败: {r.json().get('detail', r.text)}"
    except Exception as e:
        return f"请求失败: {e}"


# ═══════════════════════════════════════════
# 对话操作
# ═══════════════════════════════════════════

def chat_create_session(agent: str, token: str) -> tuple:
    try:
        r = _req("post", f"/chat/session?agent={requests.utils.quote(agent)}", token)
        if r.status_code == 200:
            data = r.json()
            return data.get("session_id", ""), f"已创建与 {agent} 的对话 (ID: {data.get('session_id','')})"
        return "", f"失败: {r.json().get('detail', '')}"
    except Exception as e:
        return "", f"请求失败: {e}"


def chat_send(session_id: str, agent: str, message: str, token: str) -> tuple:
    if not session_id or not message.strip(): return "", "", "请先创建会话并输入消息"
    try:
        r = _req("post", f"/chat/session/{session_id}/message", token,
                 json={"agent": agent, "message": message, "session_id": session_id})
        if r.status_code == 200:
            data = r.json()
            reply = data.get("reply", "")
            return session_id, reply, f"回复 ({len(reply)} 字符)"
        return session_id, "", f"失败: {r.json().get('detail', '')}"
    except Exception as e:
        return session_id, "", f"请求失败: {e}"


def chat_list_sessions(token: str) -> str:
    try:
        r = _req("get", "/chat/sessions", token)
        if r.status_code != 200: return "加载失败"
        sessions = r.json().get("sessions", [])
        if not sessions: return '<div class="empty-state">暂无活跃对话</div>'
        rows = ["| Agent | 会话 ID | 消息数 | 创建时间 |", "|------|------|------|------|"]
        for s in sessions:
            rows.append(f"| {_e(s['agent'])} | {s['id']} | {s['messages']} | {_e(s.get('created_at','')[:19])} |")
        return "\n".join(rows)
    except Exception as e:
        return f"加载失败: {e}"


# ═══════════════════════════════════════════
# 统计 & 工具
# ═══════════════════════════════════════════

def metrics_html(token: str) -> str:
    try:
        r1 = _req("get", "/metrics", token)
        r2 = _req("get", "/stats", token)
        m = r1.json().get("data", {}) if r1.status_code == 200 else {}
        s = r2.json().get("data", {}) if r2.status_code == 200 else {}
    except Exception:
        return '<div class="empty-state">无法获取统计</div>'

    parts = ['<div class="metrics-row">']
    parts.append(f'<div class="metric-card"><div class="metric-value">{s.get("total_tasks",0)}</div><div class="metric-label">总任务数</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value green">{s.get("completed_tasks",0)}</div><div class="metric-label">已完成</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value red">{s.get("failed_tasks",0)}</div><div class="metric-label">失败</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{s.get("total_memories",0)}</div><div class="metric-label">记忆条目</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{s.get("total_users",0)}</div><div class="metric-label">注册用户</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{m.get("total_calls",0)}</div><div class="metric-label">LLM 调用</div></div>')
    parts.append("</div>")

    pt = m.get("total_prompt_tokens", 0); ct = m.get("total_completion_tokens", 0)
    parts.append('<div class="metrics-row">')
    parts.append(f'<div class="metric-card"><div class="metric-value">{pt:,}</div><div class="metric-label">Prompt Tokens</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{ct:,}</div><div class="metric-label">Completion Tokens</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{pt+ct:,}</div><div class="metric-label">总计 Tokens</div></div>')
    parts.append("</div>")

    by_model = m.get("by_model", {})
    if by_model:
        parts.append('<div class="card"><div class="card-title">按模型统计</div><table class="data-table"><thead><tr><th>模型</th><th>调用次数</th><th>Prompt Tokens</th><th>Completion Tokens</th></tr></thead><tbody>')
        for model, info in by_model.items():
            parts.append(f"<tr><td>{_e(model)}</td><td>{info.get('calls',0)}</td><td>{info.get('prompt_tokens',0):,}</td><td>{info.get('completion_tokens',0):,}</td></tr>")
        parts.append("</tbody></table></div>")
    return "".join(parts)


def tools_html(token: str) -> str:
    try:
        r = _req("get", "/tools", token)
        tools = r.json().get("tools", []) if r.status_code == 200 else []
    except Exception: tools = []
    if not tools: return '<div class="empty-state">无已注册工具</div>'
    rows = ["| 工具名 | 描述 |", "|------|------|"]
    for t in tools: rows.append(f"| **{_e(t['name'])}** | {_e(t.get('description',''))} |")
    return "\n".join(rows)


def system_status_html() -> str:
    try:
        r = _req("get", "/status")
        if r.status_code == 200:
            data = r.json()
            s = data.get("status", "unknown")
            cls = {"active": "", "completed": "idle", "idle": "idle"}.get(s, "off")
            label = {"active": "运行中", "completed": "最近已完成", "idle": "空闲"}.get(s, s)
            return f'<div class="status-bar"><div class="status-dot {cls}"></div><span class="status-text">{label}</span></div>'
    except Exception: pass
    return '<div class="status-bar"><div class="status-dot off"></div><span class="status-text" style="color:var(--red)">API 未连接</span></div>'


# ═══════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════

def do_login(username: str, password: str):
    if not username.strip() or not password.strip():
        return "", "", gr.Textbox(visible=True, value="请输入用户名和密码"), gr.Group(visible=True), gr.Group(visible=False)
    try:
        r = _req("post", "/login", json={"username": username.strip(), "password": password})
        data = r.json()
        if r.status_code == 200:
            return data.get("token", ""), username.strip(), gr.Textbox(visible=False, value=""), gr.Group(visible=False), gr.Group(visible=True)
        return "", "", gr.Textbox(visible=True, value=data.get("detail", "登录失败")), gr.Group(visible=True), gr.Group(visible=False)
    except requests.RequestException as e:
        return "", "", gr.Textbox(visible=True, value=f"无法连接服务器: {e}"), gr.Group(visible=True), gr.Group(visible=False)


def do_register(username: str, password: str):
    if len(username.strip()) < 2: return "", "", gr.Textbox(visible=True, value="用户名至少 2 个字符"), gr.Group(visible=True), gr.Group(visible=False)
    if len(password) < 4: return "", "", gr.Textbox(visible=True, value="密码至少 4 个字符"), gr.Group(visible=True), gr.Group(visible=False)
    try:
        r = _req("post", "/register", json={"username": username.strip(), "password": password})
        data = r.json()
        if r.status_code == 200:
            return data.get("token", ""), username.strip(), gr.Textbox(visible=False, value=""), gr.Group(visible=False), gr.Group(visible=True)
        return "", "", gr.Textbox(visible=True, value=data.get("detail", "注册失败")), gr.Group(visible=True), gr.Group(visible=False)
    except requests.RequestException as e:
        return "", "", gr.Textbox(visible=True, value=f"无法连接服务器: {e}"), gr.Group(visible=True), gr.Group(visible=False)


# ═══════════════════════════════════════════
# Build UI
# ═══════════════════════════════════════════

_dark_font = [
    "-apple-system", "BlinkMacSystemFont", "SF Pro Display",
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "sans-serif",
]

_dark_theme = gr.themes.Base(
    primary_hue="indigo",
    secondary_hue="violet",
    neutral_hue="slate",
    font=_dark_font,
    radius_size=gr.themes.sizes.radius_md,
).set(
    # Force ALL background fills to dark (override Gradio 6 light defaults)
    body_background_fill="#0a0e1a",
    body_background_fill_dark="#0a0e1a",
    background_fill_primary="#0a0e1a",
    background_fill_primary_dark="#0a0e1a",
    background_fill_secondary="#121829",
    background_fill_secondary_dark="#121829",
    block_background_fill="transparent",
    block_background_fill_dark="transparent",
    block_border_color="rgba(255,255,255,0.07)",
    block_border_color_dark="rgba(255,255,255,0.07)",
    block_label_background_fill="transparent",
    block_label_background_fill_dark="transparent",
    block_label_border_color="rgba(255,255,255,0.07)",
    block_label_border_color_dark="rgba(255,255,255,0.07)",
    block_label_text_color="#9ba6bd",
    block_label_text_color_dark="#9ba6bd",
    block_title_text_color="#e8ecf4",
    block_title_text_color_dark="#e8ecf4",
    body_text_color="#e8ecf4",
    body_text_color_dark="#e8ecf4",
    body_text_color_subdued="#9ba6bd",
    body_text_color_subdued_dark="#9ba6bd",
    border_color_primary="rgba(255,255,255,0.07)",
    border_color_primary_dark="rgba(255,255,255,0.07)",
    input_background_fill="#1a2138",
    input_background_fill_dark="#1a2138",
    input_border_color="rgba(255,255,255,0.07)",
    input_border_color_dark="rgba(255,255,255,0.07)",
    button_primary_background_fill="*primary_500",
    button_primary_background_fill_dark="*primary_500",
    button_primary_text_color="#ffffff",
    button_primary_text_color_dark="#ffffff",
    button_secondary_background_fill="#1a2138",
    button_secondary_background_fill_dark="#1a2138",
    button_secondary_text_color="#e8ecf4",
    button_secondary_text_color_dark="#e8ecf4",
)

with gr.Blocks(
    title="多智能体协作平台",
    theme=_dark_theme,
    css=CSS,
) as demo:
    token_state = gr.State("")
    user_state = gr.State("")
    nav_page = gr.State("workbench")

    # ── Login Screen (matches mobile-demo.html) ──
    with gr.Group(visible=True, elem_classes=["auth-screen"]) as login_group:
        with gr.Column(elem_classes=["auth-card"], scale=1, min_width=0):
            gr.HTML(
                '<div class="auth-logo">⬡</div>'
                '<div class="auth-card-body">'
                '<h2>多智能体<br>协作平台</h2>'
                '<p class="sub">主 Agent 智能调度 · 产品经理 / 程序员 / 测试员协同完成开发任务</p>'
                '</div>'
            )
            login_user = gr.Textbox(label="用户名", placeholder="输入用户名")
            login_pass = gr.Textbox(label="密码", placeholder="输入密码", type="password")
            login_msg = gr.Textbox(label="状态", interactive=False, container=True, visible=False)
            login_btn = gr.Button("进入工作台", variant="primary", size="lg")
            reg_btn = gr.Button("注册", variant="secondary", size="lg")
            gr.HTML(
                '<div class="login-foot">'
                '<div class="lf-agents">'
                '<div class="lf-agent"><div class="lf-dot" style="background:var(--pm-bg);color:var(--pm)">📋</div>产品经理</div>'
                '<div class="lf-agent"><div class="lf-dot" style="background:var(--coder-bg);color:var(--coder)">⌨️</div>程序员</div>'
                '<div class="lf-agent"><div class="lf-dot" style="background:var(--qa-bg);color:var(--qa)">✓</div>测试员</div>'
                '</div>'
                'v0.2.0 · LangGraph ReAct Agent'
                '</div>'
            )

    # ── Workspace ──
    with gr.Group(visible=False) as workspace:
        with gr.Row(equal_height=False):
            # ── Sidebar: Brand → User → Nav ──
            with gr.Column(scale=0, min_width=224, elem_classes="sidebar"):
                gr.HTML(
                    '<div class="sidebar-brand">'
                    '<div class="sidebar-logo">⬡</div>'
                    '<div>'
                    '<div class="sidebar-brand-name">AgentPlatform</div>'
                    '<div class="sidebar-brand-ver">v0.2</div>'
                    '</div></div>'
                    '<div class="sidebar-user">'
                    '<div class="sidebar-avatar">BD</div>'
                    '</div>'
                )
                current_user_display = gr.Textbox(label="当前用户", interactive=False, elem_classes="sidebar-username")
                logout_btn = gr.Button("退出登录", variant="stop", size="sm", elem_classes="sidebar-logout")
                gr.HTML(
                    '<div class="sidebar-divider"></div>'
                    '<div class="sidebar-section">功能导航</div>'
                )
                nav_radio = gr.Radio(
                    choices=[("▣ 工作台", "workbench"), ("◈ 知识库", "kb"), ("◉ 对话", "chat"), ("▦ 统计", "metrics")],
                    value="workbench",
                    label=None,
                    interactive=True,
                    elem_classes="nav-radio",
                )

            # ── Main Content ──
            with gr.Column(scale=1, elem_classes="main-content"):
                gr.HTML(
                    '<div class="workspace-header">'
                    '<div class="wh-greeting"><span class="wh-hi">下午好 👋</span>'
                    '<span class="wh-title">BD</span></div>'
                    '<div class="wh-avatar">B</div></div>'
                )
                # Workbench Panel (matches mobile-demo.html layout)
                with gr.Group(visible=True) as workbench_panel:
                    # Task input card — gradient border like demo
                    with gr.Group(elem_classes=["task-input-card"]):
                        gr.HTML('<div class="tic-label">⚡ 新任务</div>')
                        with gr.Row():
                            task_input = gr.Textbox(
                                label=None,
                                placeholder="例如：写一个判断回文字符串的 Python 函数，忽略大小写和非字母数字字符…",
                                lines=3, max_lines=5, scale=5,
                                elem_classes=["tic-textarea"],
                                show_label=False,
                            )
                            with gr.Column(scale=0, min_width=64, elem_classes=["tic-go-col"]):
                                submit_btn = gr.Button("▶", variant="primary", size="lg", elem_classes=["tic-go"])
                        task_status = gr.Textbox(
                            label=None, value="就绪", interactive=False,
                            show_label=False, elem_classes=["tic-status"],
                        )

                    stepper_html = gr.HTML(value=render_stepper(0))

                    gr.HTML('<div class="section-title">Agent 状态</div>')
                    agents_html = gr.HTML(value=render_agents("", {}))

                    gr.HTML('<div class="section-title">协作日志</div>')
                    log_html = gr.HTML(value=render_log({}, "", 0))

                    planning_html = gr.HTML(value=render_planning({}), visible=False)
                    deliverable_html = gr.HTML(value=render_deliverable(""), visible=False)

                # Knowledge Base Panel
                with gr.Group(visible=False) as kb_panel:
                    gr.HTML(
                        '<div class="workspace-header" style="padding-bottom:12px">'
                        '<div class="wh-greeting"><span class="wh-hi">RAG 知识库</span>'
                        '<span class="wh-title">文档管理 & 检索</span></div></div>'
                    )
                    gr.Markdown("Agent 可通过 `search_knowledge` 工具检索以下知识库中的内容。BM25 + ChromaDB 双路检索 + RRF 融合 + LLM 重排序。")
                    kb_list_html = gr.HTML(value='<div class="empty-state">点击刷新加载知识库列表</div>')
                    kb_refresh_btn = gr.Button("刷新列表", size="sm")

                    with gr.Accordion("上传文本", open=True):
                        with gr.Row():
                            kb_col_name = gr.Textbox(label="知识库名称", placeholder="my-knowledge", scale=1)
                            kb_title = gr.Textbox(label="文档标题", placeholder="标题（可选）", scale=1)
                        kb_text = gr.Textbox(label="文本内容", lines=6, placeholder="粘贴或输入文本内容...")
                        kb_upload_btn = gr.Button("上传到知识库", variant="primary")
                        kb_upload_msg = gr.Textbox(label="结果", interactive=False)

                    with gr.Accordion("从文件/目录导入"):
                        with gr.Row():
                            kb_imp_col = gr.Textbox(label="知识库名称", placeholder="my-knowledge", scale=1)
                            kb_imp_path = gr.Textbox(label="文件或目录路径", placeholder="/path/to/file_or_dir", scale=3)
                        kb_import_btn = gr.Button("导入", variant="primary")
                        kb_import_msg = gr.Textbox(label="结果", interactive=False)

                    with gr.Accordion("搜索"):
                        with gr.Row():
                            kb_search_col = gr.Textbox(label="知识库名称", placeholder="collection-name", scale=1)
                            kb_search_q = gr.Textbox(label="搜索关键词", placeholder="Python函数设计", scale=2)
                            kb_search_k = gr.Slider(minimum=1, maximum=10, value=3, step=1, label="返回数量", scale=0)
                        kb_search_btn = gr.Button("搜索", variant="primary")
                        kb_search_result = gr.Textbox(label="搜索结果", interactive=False, lines=10)

                    with gr.Accordion("删除知识库"):
                        with gr.Row():
                            kb_del_name = gr.Textbox(label="知识库名称", placeholder="要删除的知识库名称", scale=2)
                        kb_delete_btn = gr.Button("删除", variant="stop")
                        kb_delete_msg = gr.Textbox(label="结果", interactive=False)

                # Chat Panel
                with gr.Group(visible=False) as chat_panel:
                    gr.HTML(
                        '<div class="workspace-header" style="padding-bottom:12px">'
                        '<div class="wh-greeting"><span class="wh-hi">多轮对话</span>'
                        '<span class="wh-title">与 Agent 对话</span></div></div>'
                    )
                    gr.Markdown("选择角色，创建会话后即可多轮对话。Agent 会保持上下文记忆（最近 20 条消息）。")
                    with gr.Row():
                        chat_agent = gr.Dropdown(
                            label="Agent 角色",
                            choices=["主Agent", "产品经理", "程序员", "测试员"],
                            value="主Agent", scale=1)
                        chat_create_btn = gr.Button("+ 新建会话", variant="primary", scale=0)
                    chat_session_id = gr.Textbox(label="会话 ID", interactive=False)
                    chat_create_msg = gr.Textbox(label="状态", interactive=False)

                    with gr.Row():
                        chat_input = gr.Textbox(label="消息", placeholder="输入消息...", lines=3, scale=4)
                        chat_send_btn = gr.Button("发送", variant="primary", scale=0)
                    chat_reply = gr.Textbox(label="Agent 回复", interactive=False, lines=12)
                    chat_send_status = gr.Textbox(label="发送状态", interactive=False)

                    with gr.Accordion("活跃会话列表"):
                        chat_list_btn = gr.Button("刷新")
                        chat_list_html = gr.HTML(value='<div class="empty-state">点击刷新查看活跃会话</div>')

                # Metrics Panel
                with gr.Group(visible=False) as metrics_panel:
                    gr.HTML(
                        '<div class="workspace-header" style="padding-bottom:12px">'
                        '<div class="wh-greeting"><span class="wh-hi">平台数据</span>'
                        '<span class="wh-title">运行统计</span></div>'
                        '<div class="wh-avatar">📊</div></div>'
                    )
                    metrics_refresh_btn = gr.Button("刷新统计", size="sm")
                    metrics_html_out = gr.HTML(value='<div class="empty-state">点击刷新加载统计</div>')

                    gr.Markdown("### 已注册工具")
                    tools_html_out = gr.HTML(value="点击刷新加载工具列表")

    # ═══════════════════════════════════════════
    # Navigation Logic
    # ═══════════════════════════════════════════

    def switch_page(page: str):
        return (
            gr.update(visible=(page == "workbench")),
            gr.update(visible=(page == "kb")),
            gr.update(visible=(page == "chat")),
            gr.update(visible=(page == "metrics")),
        )

    nav_page.change(
        switch_page, [nav_page],
        [workbench_panel, kb_panel, chat_panel, metrics_panel],
    )

    nav_radio.change(lambda v: v, [nav_radio], [nav_page])

    demo.load(switch_page, [nav_page], [workbench_panel, kb_panel, chat_panel, metrics_panel])

    # ═══════════════════════════════════════════
    # Event Bindings
    # ═══════════════════════════════════════════

    login_btn.click(do_login, [login_user, login_pass], [token_state, user_state, login_msg, login_group, workspace])
    reg_btn.click(do_register, [login_user, login_pass], [token_state, user_state, login_msg, login_group, workspace])

    def on_auth_change(token, username):
        if token:
            return gr.Group(visible=False), gr.Group(visible=True), username
        return gr.Group(visible=True), gr.Group(visible=False), ""

    token_state.change(on_auth_change, [token_state, user_state], [login_group, workspace, current_user_display])

    logout_btn.click(
        lambda: ("", "", "", render_stepper(0), render_planning({}), render_agents("", {}),
                 render_log({}, "", 0), "已退出", render_deliverable(""),
                 gr.Group(visible=True), gr.Group(visible=False), "workbench"),
        [], [token_state, user_state, task_input, stepper_html, planning_html, agents_html,
             log_html, task_status, deliverable_html, login_group, workspace, nav_page],
    )

    # Workbench
    submit_btn.click(
        run_task_stream, [task_input, token_state],
        [stepper_html, planning_html, agents_html, log_html, task_status, deliverable_html],
    )

    # Knowledge Base
    kb_refresh_btn.click(kb_list_collections, [token_state], [kb_list_html])
    kb_upload_btn.click(kb_upload_text, [kb_col_name, kb_title, kb_text, token_state], [kb_upload_msg])
    kb_import_btn.click(kb_import_path, [kb_imp_col, kb_imp_path, token_state], [kb_import_msg])
    kb_search_btn.click(kb_search, [kb_search_col, kb_search_q, kb_search_k, token_state], [kb_search_result])
    kb_delete_btn.click(kb_delete, [kb_del_name, token_state], [kb_delete_msg])

    # Chat
    chat_create_btn.click(chat_create_session, [chat_agent, token_state], [chat_session_id, chat_create_msg])
    chat_send_btn.click(chat_send, [chat_session_id, chat_agent, chat_input, token_state], [chat_session_id, chat_reply, chat_send_status])
    chat_list_btn.click(chat_list_sessions, [token_state], [chat_list_html])

    # Metrics
    def refresh_all(token): return metrics_html(token), tools_html(token)
    metrics_refresh_btn.click(refresh_all, [token_state], [metrics_html_out, tools_html_out])


if __name__ == "__main__":
    print("=" * 50)
    print("  多智能体协作平台")
    print("  请确保 API 服务器已启动:")
    print("  python -m uvicorn agent_platform.server.api:app --host 127.0.0.1 --port 8000")
    print("=" * 50)
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False, show_error=True)
