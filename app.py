#!/usr/bin/env python3
"""
VPS Control & Monitoring Dashboard
Lightweight, zero-dependency management server using Python standard library.
Features Server-Side Pre-rendering (SSR) + Client-Side Live Polling (CSR).
"""

import os
import sys
import json
import base64
import shutil
import platform
import subprocess
from http import HTTPStatus
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "port": 8080,
        "username": "admin",
        "password": "changeme",
        "title": "VPS Control & Monitor",
        "refresh_interval_sec": 3
    }

CONFIG = load_config()

def run_cmd(cmd, timeout=15):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def get_system_metrics():
    load1, load5, load15 = os.getloadavg()
    cpu_count = os.cpu_count() or 1
    cpu_pct = min(100.0, round((load1 / cpu_count) * 100, 1))

    mem = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    mem[key] = int(val)
    except Exception:
        pass

    total_kb = mem.get("MemTotal", 1)
    avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    used_kb = max(0, total_kb - avail_kb)
    ram_total_mb = round(total_kb / 1024, 1)
    ram_used_mb = round(used_kb / 1024, 1)
    ram_free_mb = round(avail_kb / 1024, 1)
    ram_pct = round((used_kb / total_kb) * 100, 1)

    swap_total_kb = mem.get("SwapTotal", 0)
    swap_free_kb = mem.get("SwapFree", 0)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)
    swap_total_mb = round(swap_total_kb / 1024, 1)
    swap_used_mb = round(swap_used_kb / 1024, 1)
    swap_pct = round((swap_used_kb / swap_total_kb * 100), 1) if swap_total_kb > 0 else 0

    du = shutil.disk_usage("/")
    disk_total_gb = round(du.total / (1024**3), 2)
    disk_used_gb = round(du.used / (1024**3), 2)
    disk_free_gb = round(du.free / (1024**3), 2)
    disk_pct = round((du.used / du.total) * 100, 1)

    inode_pct = 0
    try:
        st = os.statvfs("/")
        if st.f_files > 0:
            used_inodes = st.f_files - st.f_ffree
            inode_pct = round((used_inodes / st.f_files) * 100, 1)
    except Exception:
        pass

    uptime_sec = 0
    try:
        with open("/proc/uptime", "r") as f:
            uptime_sec = float(f.readline().split()[0])
    except Exception:
        pass

    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)

    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "os": "Ubuntu 24.04 LTS (Noble)",
        "cpu_count": cpu_count,
        "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "cpu_pct": cpu_pct,
        "ram": {
            "total_mb": ram_total_mb,
            "used_mb": ram_used_mb,
            "free_mb": ram_free_mb,
            "pct": ram_pct
        },
        "swap": {
            "total_mb": swap_total_mb,
            "used_mb": swap_used_mb,
            "pct": swap_pct
        },
        "disk": {
            "total_gb": disk_total_gb,
            "used_gb": disk_used_gb,
            "free_gb": disk_free_gb,
            "pct": disk_pct,
            "inode_pct": inode_pct
        },
        "uptime": {
            "days": days,
            "hours": hours,
            "mins": mins,
            "raw_sec": int(uptime_sec)
        }
    }

def get_projects_status():
    projects = []

    # 1. RustDesk Server
    code_sig, out_sig, _ = run_cmd("systemctl is-active rustdesksignal.service")
    code_rel, out_rel, _ = run_cmd("systemctl is-active rustdeskrelay.service")
    rd_active = (out_sig == "active" and out_rel == "active")
    rd_status = "active" if rd_active else (out_sig if out_sig != "active" else out_rel)

    _, rd_ver_out, _ = run_cmd("/opt/rustdesk/hbbs --version")
    rd_version = rd_ver_out.replace("hbbs", "").strip() or "1.1.16"

    projects.append({
        "id": "rustdesk",
        "name": "RustDesk Server",
        "category": "Remote Desktop Relay/Signal",
        "status": rd_status,
        "installed_version": rd_version,
        "latest_version": "1.1.16",
        "update_available": (rd_version != "1.1.16"),
        "services": ["rustdesksignal", "rustdeskrelay"],
        "ports": ["21115-21119 (TCP)", "21116 (UDP)"],
        "description": "Самохостинговый сервер подключения и ретрансляции RustDesk"
    })

    # 2. GoHTTPServer
    _, gh_status, _ = run_cmd("systemctl is-active gohttpserver.service")
    _, gh_ver, _ = run_cmd("/opt/gohttp/gohttpserver --version 2>&1 | grep -i 'Version:' | head -1 | awk '{print $2}'")
    gh_version = gh_ver.strip() if gh_ver else "1.3.0"

    projects.append({
        "id": "gohttpserver",
        "name": "GoHTTPServer",
        "category": "File Web Server",
        "status": gh_status,
        "installed_version": gh_version,
        "latest_version": "1.3.0",
        "update_available": False,
        "services": ["gohttpserver"],
        "ports": ["8000 (TCP)"],
        "description": "Легковесный HTTP веб-файловый сервер с Basic Auth"
    })

    # 3. Docker & Containerd
    _, doc_status, _ = run_cmd("systemctl is-active docker.service")
    _, doc_ver, _ = run_cmd("docker --version 2>&1 | awk '{print $3}' | tr -d ','")
    doc_version = doc_ver.strip() if doc_ver else "29.1.3"

    projects.append({
        "id": "docker",
        "name": "Docker Engine",
        "category": "Container Runtime",
        "status": doc_status,
        "installed_version": doc_version,
        "latest_version": doc_version,
        "update_available": False,
        "services": ["docker"],
        "ports": ["-"],
        "description": "Среда контейнеризации Docker"
    })

    # 4. Nginx
    _, ng_status, _ = run_cmd("systemctl is-active nginx.service")
    _, ng_ver, _ = run_cmd("nginx -v 2>&1 | awk -F'/' '{print $2}' | awk '{print $1}'")
    ng_version = ng_ver.strip() if ng_ver else "1.24.0"

    projects.append({
        "id": "nginx",
        "name": "Nginx Web Server",
        "category": "Reverse Proxy & Web Server",
        "status": ng_status,
        "installed_version": ng_version,
        "latest_version": ng_version,
        "update_available": False,
        "services": ["nginx"],
        "ports": ["80 (TCP)"],
        "description": "Высокопроизводительный веб-сервер и обратный прокси"
    })

    # 5. Fail2ban
    _, f2b_status, _ = run_cmd("systemctl is-active fail2ban.service")
    _, f2b_ver, _ = run_cmd("fail2ban-server --version 2>&1 | head -1 | awk '{print $2}'")
    f2b_version = f2b_ver.strip() if f2b_ver else "1.0.2"

    projects.append({
        "id": "fail2ban",
        "name": "Fail2ban Security",
        "category": "Intrusion Prevention",
        "status": f2b_status,
        "installed_version": f2b_version,
        "latest_version": f2b_version,
        "update_available": False,
        "services": ["fail2ban"],
        "ports": ["-"],
        "description": "Служба защиты от атак полного перебора паролей (брутфорса)"
    })

    return projects

def get_security_status():
    _, banned_str, _ = run_cmd("fail2ban-client status sshd 2>/dev/null | grep 'Banned IP list:' | cut -d: -f2")
    banned_ips = [ip.strip() for ip in banned_str.split() if ip.strip()]

    _, lastb_out, _ = run_cmd("lastb -n 12 2>/dev/null")
    failed_logins = []
    for line in lastb_out.splitlines():
        parts = line.split()
        if len(parts) >= 6 and not line.startswith("btmp begins"):
            user = parts[0]
            ip = parts[2]
            date_time = " ".join(parts[3:7])
            failed_logins.append({
                "user": user,
                "ip": ip,
                "time": date_time
            })

    return {
        "banned_count": len(banned_ips),
        "banned_ips": banned_ips,
        "failed_attempts": failed_logins
    }

def get_system_updates():
    _, out, _ = run_cmd("apt list --upgradable 2>/dev/null | grep -v 'Listing...' | grep -v '^$'")
    packages = []
    for line in out.splitlines():
        if "/" in line:
            pkg_name = line.split("/")[0]
            packages.append({"name": pkg_name, "raw": line})
    return {
        "count": len(packages),
        "packages": packages[:30]
    }

def render_html():
    sys_data = get_system_metrics()
    proj_data = get_projects_status()
    sec_data = get_security_status()

    # Pre-render project cards HTML
    proj_cards_html = ""
    for p in proj_data:
        status_badge = '<span class="badge badge-success">Active</span>' if p["status"] == "active" else '<span class="badge badge-danger">Stopped</span>'
        update_badge = '<button class="btn btn-sm btn-warning" onclick="updateRustDesk()">Обновить (1.1.16)</button>' if p["update_available"] else f'<span class="badge badge-info">Актуально (v{p["installed_version"]})</span>'
        actions_html = ""
        if p["services"]:
            s0 = p["services"][0]
            stop_start = f'<button class="btn btn-sm btn-danger" onclick="serviceAction(\'{s0}\', \'stop\')">Остановить</button>' if p["status"] == "active" else f'<button class="btn btn-sm btn-success" onclick="serviceAction(\'{s0}\', \'start\')">Запустить</button>'
            actions_html = f'<button class="btn btn-sm btn-primary" onclick="serviceAction(\'{s0}\', \'restart\')">Перезапуск</button> {stop_start}'

        proj_cards_html += f"""
        <div class="project-card">
          <div class="project-info">
            <div class="project-name">{p['name']} {status_badge} {update_badge}</div>
            <div class="project-desc">{p['description']} | Порты: {', '.join(p['ports'])}</div>
          </div>
          <div class="project-actions">{actions_html}</div>
        </div>
        """

    # Pre-render Banned IPs
    banned_html = ""
    if not sec_data["banned_ips"]:
        banned_html = '<div style="color: var(--text-muted); font-size: 0.8rem;">Нет активных банов</div>'
    else:
        for ip in sec_data["banned_ips"]:
            banned_html += f"""
            <div class="ip-item">
              <span>🚫 <b>{ip}</b></span>
              <button class="btn btn-sm btn-danger" onclick="unbanIp('{ip}')">Разблокировать</button>
            </div>
            """

    # Pre-render Recent Attacks
    attacks_html = ""
    if not sec_data["failed_attempts"]:
        attacks_html = '<div style="color: var(--text-muted); font-size: 0.8rem;">Нет атак в журнале</div>'
    else:
        for f in sec_data["failed_attempts"]:
            attacks_html += f"""
            <div class="ip-item">
              <div>
                <span style="color: var(--danger); font-weight: 600;">{f['user']}</span> from <b>{f['ip']}</b>
                <div style="font-size: 0.75rem; color: var(--text-muted);">{f['time']}</div>
              </div>
            </div>
            """

    expected = base64.b64encode(f"{CONFIG['username']}:{CONFIG['password']}".encode()).decode()

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VPS Dashboard | 185.189.13.52</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card: #1e293b;
      --card-border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --bar-bg: #334155;
    }}
    [data-theme="light"] {{
      --bg: #f1f5f9;
      --card: #ffffff;
      --card-border: #e2e8f0;
      --text: #0f172a;
      --text-muted: #64748b;
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --success: #059669;
      --warning: #d97706;
      --danger: #dc2626;
      --bar-bg: #e2e8f0;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    body {{ background-color: var(--bg); color: var(--text); min-height: 100vh; padding: 1.5rem; transition: background-color 0.3s; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}

    header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; flex-wrap: wrap; gap: 1rem; }}
    .logo-area {{ display: flex; align-items: center; gap: 0.75rem; }}
    .logo-icon {{ width: 40px; height: 40px; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: white; font-size: 1.25rem; }}
    .title h1 {{ font-size: 1.5rem; font-weight: 700; }}
    .title p {{ color: var(--text-muted); font-size: 0.875rem; }}
    .header-controls {{ display: flex; align-items: center; gap: 0.75rem; }}
    .btn {{ background: var(--card); border: 1px solid var(--card-border); color: var(--text); padding: 0.5rem 1rem; border-radius: 8px; cursor: pointer; font-size: 0.875rem; font-weight: 500; display: inline-flex; align-items: center; gap: 0.5rem; transition: all 0.2s; }}
    .btn:hover {{ background: var(--card-border); }}
    .btn-primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    .btn-primary:hover {{ background: var(--accent-hover); }}
    .btn-sm {{ padding: 0.25rem 0.6rem; font-size: 0.75rem; border-radius: 6px; }}
    .btn-danger {{ background: rgba(239, 68, 68, 0.15); border-color: var(--danger); color: var(--danger); }}
    .btn-danger:hover {{ background: var(--danger); color: white; }}
    .btn-success {{ background: rgba(16, 185, 129, 0.15); border-color: var(--success); color: var(--success); }}
    .btn-success:hover {{ background: var(--success); color: white; }}

    .live-badge {{ display: flex; align-items: center; gap: 6px; font-size: 0.75rem; font-weight: 600; padding: 4px 10px; border-radius: 20px; background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }}
    .pulse-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--success); animation: pulse 1.5s infinite; }}
    @keyframes pulse {{ 0% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: 0.4; transform: scale(0.8); }} 100% {{ opacity: 1; transform: scale(1); }} }}

    .grid {{ display: grid; gap: 1.25rem; }}
    .grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin-bottom: 2rem; }}
    .grid-2 {{ grid-template-columns: 2fr 1fr; margin-bottom: 2rem; }}
    @media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}

    .card {{ background: var(--card); border: 1px solid var(--card-border); border-radius: 14px; padding: 1.25rem; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
    .card-title {{ font-size: 1.1rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; }}

    .metric-value {{ font-size: 1.75rem; font-weight: 700; margin-bottom: 0.5rem; }}
    .metric-sub {{ font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.75rem; }}
    .progress-bar {{ width: 100%; height: 8px; background: var(--bar-bg); border-radius: 4px; overflow: hidden; margin-top: 0.5rem; }}
    .progress-fill {{ height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.4s ease; }}
    .progress-fill.warning {{ background: var(--warning); }}
    .progress-fill.danger {{ background: var(--danger); }}

    .project-card {{ display: flex; justify-content: space-between; align-items: center; padding: 1rem; border-bottom: 1px solid var(--card-border); }}
    .project-card:last-child {{ border-bottom: none; }}
    .project-info {{ display: flex; flex-direction: column; gap: 0.25rem; }}
    .project-name {{ font-weight: 600; font-size: 1rem; display: flex; align-items: center; gap: 0.5rem; }}
    .project-desc {{ font-size: 0.8rem; color: var(--text-muted); }}
    .badge {{ font-size: 0.7rem; font-weight: 600; padding: 3px 8px; border-radius: 12px; }}
    .badge-success {{ background: rgba(16, 185, 129, 0.15); color: var(--success); }}
    .badge-danger {{ background: rgba(239, 68, 68, 0.15); color: var(--danger); }}
    .badge-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); }}
    .badge-info {{ background: rgba(59, 130, 246, 0.15); color: var(--accent); }}
    .project-actions {{ display: flex; align-items: center; gap: 0.5rem; }}

    .ip-item {{ display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 0; border-bottom: 1px dashed var(--card-border); font-size: 0.85rem; }}
    .ip-item:last-child {{ border-bottom: none; }}

    .terminal-box {{ background: #000; color: #10b981; font-family: monospace; font-size: 0.8rem; padding: 1rem; border-radius: 8px; max-height: 200px; overflow-y: auto; margin-top: 1rem; white-space: pre-wrap; }}

    #toast {{ position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; background: var(--card); border: 1px solid var(--card-border); color: var(--text); box-shadow: 0 10px 25px rgba(0,0,0,0.2); transform: translateY(100px); opacity: 0; transition: all 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55); z-index: 1000; font-size: 0.9rem; font-weight: 500; }}
    #toast.show {{ transform: translateY(0); opacity: 1; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo-area">
        <div class="logo-icon">VPS</div>
        <div class="title">
          <h1 id="txt-title">Управление сервером 185.189.13.52</h1>
          <p id="txt-hostinfo">{sys_data['hostname']} | {sys_data['os']} | Uptime: {sys_data['uptime']['days']} дн. {sys_data['uptime']['hours']} ч.</p>
        </div>
      </div>
      <div class="header-controls">
        <div class="live-badge"><div class="pulse-dot"></div> <span id="txt-live">ОНЛАЙН</span></div>
        <button class="btn" id="btn-lang" onclick="toggleLang()">EN / RU</button>
        <button class="btn" id="btn-theme" onclick="toggleTheme()">☀️ / 🌙</button>
        <button class="btn btn-primary" onclick="loadAllData()"><span id="txt-refresh">Обновить</span></button>
      </div>
    </header>

    <div class="grid grid-4">
      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-cpu-title">⚡ Процессор</span>
          <span class="badge badge-info" id="cpu-cores">{sys_data['cpu_count']} Core</span>
        </div>
        <div class="metric-value" id="cpu-pct">{sys_data['cpu_pct']}%</div>
        <div class="metric-sub" id="cpu-load">Load: {', '.join(map(str, sys_data['load_avg']))}</div>
        <div class="progress-bar"><div class="progress-fill" id="cpu-bar" style="width: {sys_data['cpu_pct']}%"></div></div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-ram-title">🧠 Память RAM</span>
          <span class="badge badge-info" id="ram-pct">{sys_data['ram']['pct']}%</span>
        </div>
        <div class="metric-value" id="ram-used">{sys_data['ram']['used_mb']} MB</div>
        <div class="metric-sub" id="ram-sub">{sys_data['ram']['pct']}% of {sys_data['ram']['total_mb']} MB ({sys_data['ram']['free_mb']} MB free)</div>
        <div class="progress-bar"><div class="progress-fill" id="ram-bar" style="width: {sys_data['ram']['pct']}%"></div></div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-swap-title">🔄 Файл подкачки</span>
          <span class="badge badge-info" id="swap-pct">{sys_data['swap']['pct']}%</span>
        </div>
        <div class="metric-value" id="swap-used">{sys_data['swap']['used_mb']} MB</div>
        <div class="metric-sub" id="swap-sub">Total {sys_data['swap']['total_mb']} MB</div>
        <div class="progress-bar"><div class="progress-fill" id="swap-bar" style="width: {sys_data['swap']['pct']}%"></div></div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-disk-title">💾 Накопитель</span>
          <span class="badge badge-info" id="disk-pct">{sys_data['disk']['pct']}%</span>
        </div>
        <div class="metric-value" id="disk-free">{sys_data['disk']['free_gb']} GB</div>
        <div class="metric-sub" id="disk-sub">Free of {sys_data['disk']['total_gb']} GB ({sys_data['disk']['pct']}% used)</div>
        <div class="progress-bar"><div class="progress-fill" id="disk-bar" style="width: {sys_data['disk']['pct']}%"></div></div>
      </div>
    </div>

    <div class="grid grid-2">
      <div>
        <div class="card" style="margin-bottom: 1.5rem;">
          <div class="card-header">
            <span class="card-title" id="txt-projects-title">📦 Установленные проекты и службы</span>
          </div>
          <div id="projects-list">
            {proj_cards_html}
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <span class="card-title" id="txt-sysup-title">🔄 Обновления системы (APT)</span>
            <button class="btn btn-sm btn-primary" onclick="checkSysUpdates()" id="btn-check-apt">Проверить обновления</button>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="font-size: 1.1rem; font-weight: 600;" id="apt-status-text">Ожидает проверки</div>
              <div style="font-size: 0.8rem; color: var(--text-muted);" id="apt-sub-text">Нажмите «Проверить обновления»</div>
            </div>
            <button class="btn btn-success" id="btn-upgrade-apt" style="display: none;" onclick="upgradeSystem()">Установить обновления</button>
          </div>
          <div id="update-terminal" class="terminal-box" style="display: none;"></div>
        </div>
      </div>

      <div>
        <div class="card">
          <div class="card-header">
            <span class="card-title" id="txt-sec-title">🛡️ Защита Fail2ban</span>
            <span class="badge badge-danger" id="banned-count">{sec_data['banned_count']} Banned</span>
          </div>
          <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 1rem;" id="txt-sec-desc">
            Активные блокировки IP-адресов за попытки подбора паролей.
          </p>
          <div style="font-weight: 600; font-size: 0.85rem; margin-bottom: 0.5rem;" id="txt-banned-list">Список заблокированных IP:</div>
          <div id="banned-ips-list" style="margin-bottom: 1.5rem;">
            {banned_html}
          </div>

          <div style="font-weight: 600; font-size: 0.85rem; margin-bottom: 0.5rem;" id="txt-recent-attacks">Последние попытки перебора:</div>
          <div id="failed-logins-list" style="max-height: 250px; overflow-y: auto;">
            {attacks_html}
          </div>
        </div>
      </div>
    </div>
  </div>

  <div id="toast">Уведомление</div>

  <script>
    const API_AUTH = "Basic {expected}";
    let currentLang = localStorage.getItem("lang") || "ru";
    let currentTheme = localStorage.getItem("theme") || "dark";

    const dict = {{
      ru: {{
        title: "Управление сервером 185.189.13.52",
        live: "ОНЛАЙН",
        refresh: "Обновить",
        cpu: "⚡ Процессор",
        ram: "🧠 Память RAM",
        swap: "🔄 Файл подкачки",
        disk: "💾 Накопитель",
        projects: "📦 Установленные проекты и службы",
        sysup: "🔄 Обновления системы (APT)",
        sec: "🛡️ Защита Fail2ban",
        secDesc: "Активные блокировки IP-адресов за попытки подбора паролей.",
        bannedList: "Список заблокированных IP:",
        recentAttacks: "Последние попытки перебора:",
        noBans: "Нет активных банов",
        checkApt: "Проверить обновления",
        upgradeApt: "Установить обновления",
        uptodate: "Актуально",
        updateAvail: "Обновить",
        restart: "Перезапуск",
        start: "Запустить",
        stop: "Остановить",
        unban: "Разблокировать",
        uptimeDays: "дн.",
        uptimeHours: "ч.",
        uptimeMins: "мин."
      }},
      en: {{
        title: "VPS Dashboard 185.189.13.52",
        live: "ONLINE",
        refresh: "Refresh",
        cpu: "⚡ CPU Load",
        ram: "🧠 RAM Memory",
        swap: "🔄 Swap File",
        disk: "💾 Disk Storage",
        projects: "📦 Installed Projects & Services",
        sysup: "🔄 System Updates (APT)",
        sec: "🛡️ Fail2ban Security",
        secDesc: "Active IP bans against brute-force password attempts.",
        bannedList: "Currently Banned IPs:",
        recentAttacks: "Recent Failed Attempts:",
        noBans: "No active IP bans",
        checkApt: "Check Updates",
        upgradeApt: "Install Updates",
        uptodate: "Up to date",
        updateAvail: "Update",
        restart: "Restart",
        start: "Start",
        stop: "Stop",
        unban: "Unban",
        uptimeDays: "d",
        uptimeHours: "h",
        uptimeMins: "m"
      }}
    }};

    async function apiFetch(url, options = {{}}) {{
      options.headers = options.headers || {{}};
      if (API_AUTH) {{
        options.headers["Authorization"] = API_AUTH;
      }}
      options.credentials = "same-origin";
      const res = await fetch(url, options);
      if (!res.ok) {{
        throw new Error("HTTP " + res.status + " on " + url);
      }}
      return await res.json();
    }}

    function applyLang() {{
      const t = dict[currentLang];
      document.getElementById("txt-title").innerText = t.title;
      document.getElementById("txt-live").innerText = t.live;
      document.getElementById("txt-refresh").innerText = t.refresh;
      document.getElementById("txt-cpu-title").innerText = t.cpu;
      document.getElementById("txt-ram-title").innerText = t.ram;
      document.getElementById("txt-swap-title").innerText = t.swap;
      document.getElementById("txt-disk-title").innerText = t.disk;
      document.getElementById("txt-projects-title").innerText = t.projects;
      document.getElementById("txt-sysup-title").innerText = t.sysup;
      document.getElementById("txt-sec-title").innerText = t.sec;
      document.getElementById("txt-sec-desc").innerText = t.secDesc;
      document.getElementById("txt-banned-list").innerText = t.bannedList;
      document.getElementById("txt-recent-attacks").innerText = t.recentAttacks;
      document.getElementById("btn-check-apt").innerText = t.checkApt;
      document.getElementById("btn-upgrade-apt").innerText = t.upgradeApt;
    }}

    function toggleLang() {{
      currentLang = (currentLang === "ru") ? "en" : "ru";
      localStorage.setItem("lang", currentLang);
      applyLang();
      loadAllData();
    }}

    function applyTheme() {{
      document.documentElement.setAttribute("data-theme", currentTheme);
    }}

    function toggleTheme() {{
      currentTheme = (currentTheme === "dark") ? "light" : "dark";
      localStorage.setItem("theme", currentTheme);
      applyTheme();
    }}

    function showToast(msg) {{
      const el = document.getElementById("toast");
      el.innerText = msg;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 3500);
    }}

    async function loadSystemStats() {{
      try {{
        const data = await apiFetch("/api/system");
        const t = dict[currentLang];

        document.getElementById("txt-hostinfo").innerText =
          `${{data.hostname}} | ${{data.os}} | Uptime: ${{data.uptime.days}} ${{t.uptimeDays}} ${{data.uptime.hours}} ${{t.uptimeHours}}`;

        document.getElementById("cpu-cores").innerText = `${{data.cpu_count}} Core`;
        document.getElementById("cpu-pct").innerText = `${{data.cpu_pct}}%`;
        document.getElementById("cpu-load").innerText = `Load: ${{data.load_avg.join(", ")}}`;
        const cpuBar = document.getElementById("cpu-bar");
        cpuBar.style.width = `${{data.cpu_pct}}%`;
        cpuBar.className = "progress-fill" + (data.cpu_pct > 80 ? " danger" : data.cpu_pct > 50 ? " warning" : "");

        document.getElementById("ram-pct").innerText = `${{data.ram.pct}}%`;
        document.getElementById("ram-used").innerText = `${{data.ram.used_mb}} MB`;
        document.getElementById("ram-sub").innerText = `${{data.ram.pct}}% of ${{data.ram.total_mb}} MB (${{data.ram.free_mb}} MB free)`;
        const ramBar = document.getElementById("ram-bar");
        ramBar.style.width = `${{data.ram.pct}}%`;
        ramBar.className = "progress-fill" + (data.ram.pct > 85 ? " danger" : data.ram.pct > 65 ? " warning" : "");

        document.getElementById("swap-pct").innerText = `${{data.swap.pct}}%`;
        document.getElementById("swap-used").innerText = `${{data.swap.used_mb}} MB`;
        document.getElementById("swap-sub").innerText = `Total ${{data.swap.total_mb}} MB`;
        document.getElementById("swap-bar").style.width = `${{data.swap.pct}}%`;

        document.getElementById("disk-pct").innerText = `${{data.disk.pct}}%`;
        document.getElementById("disk-free").innerText = `${{data.disk.free_gb}} GB`;
        document.getElementById("disk-sub").innerText = `Free of ${{data.disk.total_gb}} GB (${{data.disk.pct}}% used)`;
        const diskBar = document.getElementById("disk-bar");
        diskBar.style.width = `${{data.disk.pct}}%`;
        diskBar.className = "progress-fill" + (data.disk.pct > 85 ? " danger" : "");
      }} catch (e) {{
        console.error("System stats error:", e);
      }}
    }}

    async function loadProjects() {{
      try {{
        const list = await apiFetch("/api/projects");
        const t = dict[currentLang];
        const container = document.getElementById("projects-list");
        container.innerHTML = "";

        list.forEach(p => {{
          const div = document.createElement("div");
          div.className = "project-card";

          const statusBadge = (p.status === "active")
            ? `<span class="badge badge-success">Active</span>`
            : `<span class="badge badge-danger">Stopped</span>`;

          const updateBadge = p.update_available
            ? `<button class="btn btn-sm btn-warning" onclick="updateRustDesk()">${{t.updateAvail}} (1.1.16)</button>`
            : `<span class="badge badge-info">${{t.uptodate}} (v${{p.installed_version}})</span>`;

          div.innerHTML = `
            <div class="project-info">
              <div class="project-name">
                ${{p.name}} ${{statusBadge}} ${{updateBadge}}
              </div>
              <div class="project-desc">${{p.description}} | Порты: ${{p.ports.join(", ")}}</div>
            </div>
            <div class="project-actions">
              ${{p.services.length > 0 ? `
                <button class="btn btn-sm btn-primary" onclick="serviceAction('${{p.services[0]}}', 'restart')">${{t.restart}}</button>
                ${{p.status === 'active'
                  ? `<button class="btn btn-sm btn-danger" onclick="serviceAction('${{p.services[0]}}', 'stop')">${{t.stop}}</button>`
                  : `<button class="btn btn-sm btn-success" onclick="serviceAction('${{p.services[0]}}', 'start')">${{t.start}}</button>`
                }}
              ` : ''}}
            </div>
          `;
          container.appendChild(div);
        }});
      }} catch (e) {{
        console.error("Projects error:", e);
      }}
    }}

    async function loadSecurity() {{
      try {{
        const sec = await apiFetch("/api/security");
        const t = dict[currentLang];

        document.getElementById("banned-count").innerText = `${{sec.banned_count}} Banned`;

        const bannedList = document.getElementById("banned-ips-list");
        bannedList.innerHTML = "";
        if (sec.banned_ips.length === 0) {{
          bannedList.innerHTML = `<div style="color: var(--text-muted); font-size: 0.8rem;">${{t.noBans}}</div>`;
        }} else {{
          sec.banned_ips.forEach(ip => {{
            const div = document.createElement("div");
            div.className = "ip-item";
            div.innerHTML = `
              <span>🚫 <b>${{ip}}</b></span>
              <button class="btn btn-sm btn-danger" onclick="unbanIp('${{ip}}')">${{t.unban}}</button>
            `;
            bannedList.appendChild(div);
          }});
        }}

        const failedList = document.getElementById("failed-logins-list");
        failedList.innerHTML = "";
        if (sec.failed_attempts.length === 0) {{
          failedList.innerHTML = `<div style="color: var(--text-muted); font-size: 0.8rem;">Нет атак в журнале</div>`;
        }} else {{
          sec.failed_attempts.forEach(f => {{
            const div = document.createElement("div");
            div.className = "ip-item";
            div.innerHTML = `
              <div>
                <span style="color: var(--danger); font-weight: 600;">${{f.user}}</span> from <b>${{f.ip}}</b>
                <div style="font-size: 0.75rem; color: var(--text-muted);">${{f.time}}</div>
              </div>
            `;
            failedList.appendChild(div);
          }});
        }}
      }} catch (e) {{
        console.error("Security error:", e);
      }}
    }}

    async function serviceAction(service, action) {{
      try {{
        showToast("Выполняется " + action + " для " + service + "...");
        const data = await apiFetch("/api/service/action", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ service, action }})
        }});
        if (data.success) {{
          showToast("Успешно: " + service + " -> " + action);
          loadProjects();
        }} else {{
          showToast("Ошибка: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка сети: " + e.message);
      }}
    }}

    async function unbanIp(ip) {{
      try {{
        showToast("Разблокировка " + ip + "...");
        const data = await apiFetch("/api/security/unban", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ ip }})
        }});
        if (data.success) {{
          showToast("IP " + ip + " разблокирован");
          loadSecurity();
        }} else {{
          showToast("Ошибка: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка запроса: " + e.message);
      }}
    }}

    async function checkSysUpdates() {{
      const btn = document.getElementById("btn-check-apt");
      btn.disabled = true;
      btn.innerText = "Проверка...";
      showToast("Проверка обновлений APT...");
      try {{
        const data = await apiFetch("/api/update/system", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ action: "check" }})
        }});
        const upBtn = document.getElementById("btn-upgrade-apt");
        if (data.count > 0) {{
          document.getElementById("apt-status-text").innerText = "Доступно обновлений: " + data.count;
          document.getElementById("apt-sub-text").innerText = "Пакеты: " + data.packages.map(p => p.name).slice(0, 5).join(", ") + "...";
          upBtn.style.display = "inline-block";
        }} else {{
          document.getElementById("apt-status-text").innerText = "Все пакеты обновлены";
          document.getElementById("apt-sub-text").innerText = "Система находится в актуальном состоянии";
          upBtn.style.display = "none";
        }}
      }} catch (e) {{
        showToast("Ошибка проверки: " + e.message);
      }} finally {{
        btn.disabled = false;
        btn.innerText = dict[currentLang].checkApt;
      }}
    }}

    async function upgradeSystem() {{
      if (!confirm("Вы уверены, что хотите запустить обновление системных пакетов?")) return;
      const term = document.getElementById("update-terminal");
      term.style.display = "block";
      term.innerText = "Запуск apt-get upgrade -y... Пожалуйста, подождите..." + String.fromCharCode(10);
      showToast("Запущено обновление системы...");
      try {{
        const data = await apiFetch("/api/update/system", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ action: "upgrade" }})
        }});
        term.innerText += data.output || data.error;
        showToast("Обновление завершено");
        checkSysUpdates();
      }} catch (e) {{
        term.innerText += String.fromCharCode(10) + "Ошибка связи с сервером: " + e.message;
      }}
    }}

    async function updateRustDesk() {{
      if (!confirm("Обновить RustDesk Server до версии 1.1.16?")) return;
      showToast("Обновление RustDesk Server...");
      try {{
        const data = await apiFetch("/api/update/rustdesk", {{ method: "POST" }});
        if (data.success) {{
          showToast("RustDesk Server успешно обновлен!");
          loadProjects();
        }} else {{
          showToast("Ошибка обновления: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка сети: " + e.message);
      }}
    }}

    function loadAllData() {{
      loadSystemStats();
      loadProjects();
      loadSecurity();
    }}

    applyTheme();
    applyLang();
    setInterval(loadSystemStats, 3000);
    setInterval(loadSecurity, 6000);
  </script>
</body>
</html>
"""
    return html

class DashboardHandler(BaseHTTPRequestHandler):
    def get_auth_token(self):
        auth_header = self.headers.get("Authorization")
        if auth_header and auth_header.startswith("Basic "):
            return auth_header

        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("vps_auth="):
                token = part.split("=", 1)[1]
                expected = base64.b64encode(f"{CONFIG['username']}:{CONFIG['password']}".encode()).decode()
                if token == expected:
                    return f"Basic {token}"
        return None

    def check_auth(self):
        token = self.get_auth_token()
        if not token:
            return False
        try:
            raw = token.split(" ", 1)[1]
            decoded = base64.b64decode(raw).decode("utf-8")
            username, password = decoded.split(":", 1)
            return (username == CONFIG.get("username") and password == CONFIG.get("password"))
        except Exception:
            return False

    def require_auth(self):
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="VPS Dashboard"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Unauthorized")

    def do_GET(self):
        if not self.check_auth():
            return self.require_auth()

        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            expected = base64.b64encode(f"{CONFIG['username']}:{CONFIG['password']}".encode()).decode()
            body = render_html().encode("utf-8")

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", f"vps_auth={expected}; Path=/; SameSite=Lax; Max-Age=2592000")
            self.end_headers()
            self.wfile.write(body)
            return

        elif path == "/api/system":
            data = get_system_metrics()
            self.send_json(data)
            return

        elif path == "/api/projects":
            data = get_projects_status()
            self.send_json(data)
            return

        elif path == "/api/security":
            data = get_security_status()
            self.send_json(data)
            return

        elif path == "/api/updates":
            data = get_system_updates()
            self.send_json(data)
            return

        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self):
        if not self.check_auth():
            return self.require_auth()

        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        if path == "/api/service/action":
            svc = payload.get("service")
            act = payload.get("action")
            allowed_services = ["rustdesksignal", "rustdeskrelay", "gohttpserver", "docker", "nginx", "fail2ban", "ssh"]
            allowed_actions = ["start", "stop", "restart", "reload"]

            if svc in allowed_services and act in allowed_actions:
                code, out, err = run_cmd(f"systemctl {act} {svc}.service")
                if code == 0:
                    self.send_json({"success": True, "message": f"{svc} {act}ed"})
                else:
                    self.send_json({"success": False, "error": err or out})
            else:
                self.send_json({"success": False, "error": "Invalid service or action"})
            return

        elif path == "/api/security/unban":
            ip = payload.get("ip", "").strip()
            if ip:
                code, out, err = run_cmd(f"fail2ban-client set sshd unbanip {ip}")
                if code == 0:
                    self.send_json({"success": True, "message": f"IP {ip} unbanned"})
                else:
                    self.send_json({"success": False, "error": err or out})
            else:
                self.send_json({"success": False, "error": "IP is required"})
            return

        elif path == "/api/update/system":
            act = payload.get("action", "check")
            if act == "check":
                data = get_system_updates()
                self.send_json(data)
            elif act == "upgrade":
                code, out, err = run_cmd("DEBIAN_FRONTEND=noninteractive apt-get upgrade -y", timeout=300)
                self.send_json({"success": (code == 0), "output": out + "\n" + err})
            return

        elif path == "/api/update/rustdesk":
            cmd = """
            mkdir -p /tmp/rustdesk-update
            cd /tmp/rustdesk-update
            curl -L -s -o rustdesk.zip "https://github.com/rustdesk/rustdesk-server/releases/download/1.1.16/rustdesk-server-linux-amd64.zip"
            unzip -q -o rustdesk.zip
            if [ -f amd64/hbbs ]; then
                systemctl stop rustdesksignal.service rustdeskrelay.service
                cp amd64/hbbs /opt/rustdesk/hbbs
                cp amd64/hbbr /opt/rustdesk/hbbr
                cp amd64/rustdesk-utils /opt/rustdesk/rustdesk-utils
                chmod +x /opt/rustdesk/hbbs /opt/rustdesk/hbbr /opt/rustdesk/rustdesk-utils
                systemctl start rustdesksignal.service rustdeskrelay.service
                echo "SUCCESS"
            else
                echo "ERROR_UNPACK"
            fi
            """
            code, out, err = run_cmd(cmd, timeout=60)
            if "SUCCESS" in out:
                self.send_json({"success": True})
            else:
                self.send_json({"success": False, "error": err or out})
            return

        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {self.address_string()} {format % args}\n")

def main():
    port = CONFIG.get("port", 8080)
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"VPS Dashboard running on port {port}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
