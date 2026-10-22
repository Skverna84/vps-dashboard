#!/usr/bin/env python3
"""
VPS Control & Monitoring Dashboard v1.3.0
Lightweight, zero-dependency management server using Python standard library.
Features Server-Side Pre-rendering (SSR) + Client-Side Live Polling (CSR).
GitHub: https://github.com/Skverna84/vps-dashboard
"""

import os
import sys
import time
import json
import base64
import shutil
import sqlite3
import tarfile
import tempfile
import datetime
import platform
import subprocess
import urllib.request
from urllib.parse import urlparse, parse_qs
from http import HTTPStatus
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler

VERSION = "1.3.0"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "port": 8080,
        "username": "admin",
        "password": "changeme",
        "title": "VPS Control & Monitor",
        "refresh_interval_sec": 3,
        "custom_services": []
    }

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

CONFIG = load_config()

# Global state for Network I/O calculation
NET_STATE = {
    "last_time": 0.0,
    "last_rx": 0,
    "last_tx": 0,
    "rx_rate_kb": 0.0,
    "tx_rate_kb": 0.0,
    "total_rx_mb": 0.0,
    "total_tx_mb": 0.0,
}

# Global in-memory GeoIP cache to avoid repeated external lookups
GEOIP_CACHE = {}

def country_code_to_flag(code):
    if not code or len(code) != 2:
        return "🌐"
    code = code.upper()
    try:
        return chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))
    except Exception:
        return "🌐"

def resolve_geoip(ip):
    if not ip or ip in ("127.0.0.1", "localhost", "::1"):
        return {"country": "Local", "country_code": "LOC", "flag": "🏠"}
    if ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16.") or ip.startswith("172.17."):
        return {"country": "LAN / Private", "country_code": "LAN", "flag": "🔒"}

    if ip in GEOIP_CACHE:
        return GEOIP_CACHE[ip]

    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode"
        req = urllib.request.Request(url, headers={"User-Agent": "VPS-Dashboard/1.2.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "success":
                cname = data.get("country", "Unknown")
                ccode = data.get("countryCode", "")
                res = {
                    "country": cname,
                    "country_code": ccode,
                    "flag": country_code_to_flag(ccode)
                }
                GEOIP_CACHE[ip] = res
                return res
    except Exception:
        pass

    fallback = {"country": "Unknown", "country_code": "UN", "flag": "🌐"}
    GEOIP_CACHE[ip] = fallback
    return fallback

def get_docker_containers():
    code, out, _ = run_cmd("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.State}}|{{.Ports}}'")
    containers = []
    if code == 0 and out:
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) >= 6:
                cid, name, image, status_str, state, ports = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                containers.append({
                    "id": cid,
                    "name": name,
                    "image": image,
                    "status_str": status_str,
                    "state": state.lower(),
                    "ports": ports or "-",
                    "is_running": (state.lower() == "running")
                })
    return containers

def get_rustdesk_details():
    pub_key_path = "/opt/rustdesk/id_ed25519.pub"
    pub_key = ""
    if os.path.exists(pub_key_path):
        try:
            with open(pub_key_path, "r", encoding="utf-8") as f:
                pub_key = f.read().strip()
        except Exception:
            pass

    server_ip = "185.189.13.52"

    # Services status
    code_sig, out_sig, _ = run_cmd("systemctl is-active rustdesksignal.service")
    code_rel, out_rel, _ = run_cmd("systemctl is-active rustdeskrelay.service")

    peers = []
    db_path = "/opt/rustdesk/db_v2.sqlite3"
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(db_path, timeout=3.0)
            cur = con.cursor()
            cur.execute("SELECT id, uuid, info, created_at FROM peer ORDER BY created_at DESC")
            rows = cur.fetchall()
            for r in rows:
                pid, uuid_bytes, info_raw, created_at = r[0], r[1], r[2], r[3]
                uuid_str = "-"
                if isinstance(uuid_bytes, (bytes, bytearray)):
                    try:
                        uuid_str = uuid_bytes.decode("utf-8")
                    except Exception:
                        uuid_str = uuid_bytes.hex()
                elif uuid_bytes:
                    uuid_str = str(uuid_bytes)

                ip_addr = "-"
                if info_raw:
                    try:
                        info_data = json.loads(info_raw)
                        ip_addr = info_data.get("ip", "-")
                        if ip_addr.startswith("::ffff:"):
                            ip_addr = ip_addr.replace("::ffff:", "")
                    except Exception:
                        pass

                geo = resolve_geoip(ip_addr) if ip_addr != "-" else {"country": "", "flag": "💻"}
                peers.append({
                    "id": pid or "-",
                    "uuid": uuid_str,
                    "ip": ip_addr,
                    "created_at": created_at or "-",
                    "country": geo.get("country", ""),
                    "flag": geo.get("flag", "💻")
                })
            con.close()
        except Exception:
            pass

    config_string = f"host={server_ip},key={pub_key}"

    return {
        "server_ip": server_ip,
        "public_key": pub_key,
        "config_string": config_string,
        "id_server": f"{server_ip}:21116",
        "relay_server": f"{server_ip}:21117",
        "api_server": f"http://{server_ip}:21114",
        "signal_status": out_sig,
        "relay_status": out_rel,
        "peers_count": len(peers),
        "peers": peers
    }

def run_cmd(cmd, timeout=15):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="replace", timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def get_network_metrics():
    now = time.time()
    rx_total = 0
    tx_total = 0
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
        for line in lines[2:]:
            parts = line.split(":")
            if len(parts) == 2:
                iface = parts[0].strip()
                if iface == "lo":
                    continue
                fields = parts[1].split()
                if len(fields) >= 9:
                    rx_total += int(fields[0])
                    tx_total += int(fields[8])
    except Exception:
        pass

    diff_time = now - NET_STATE["last_time"]
    if NET_STATE["last_time"] > 0 and diff_time > 0:
        diff_rx = max(0, rx_total - NET_STATE["last_rx"])
        diff_tx = max(0, tx_total - NET_STATE["last_tx"])
        NET_STATE["rx_rate_kb"] = round((diff_rx / diff_time) / 1024, 1)
        NET_STATE["tx_rate_kb"] = round((diff_tx / diff_time) / 1024, 1)

    NET_STATE["last_time"] = now
    NET_STATE["last_rx"] = rx_total
    NET_STATE["last_tx"] = tx_total
    NET_STATE["total_rx_mb"] = round(rx_total / (1024 * 1024), 1)
    NET_STATE["total_tx_mb"] = round(tx_total / (1024 * 1024), 1)

    return {
        "rx_rate_kb": NET_STATE["rx_rate_kb"],
        "tx_rate_kb": NET_STATE["tx_rate_kb"],
        "total_rx_mb": NET_STATE["total_rx_mb"],
        "total_tx_mb": NET_STATE["total_tx_mb"],
        "total_rx_gb": round(NET_STATE["total_rx_mb"] / 1024, 2),
        "total_tx_gb": round(NET_STATE["total_tx_mb"] / 1024, 2)
    }

def get_top_processes():
    _, out, _ = run_cmd("ps -eo pid,user,comm,%cpu,%mem --sort=-%cpu | head -n 6")
    processes = []
    lines = out.splitlines()
    if len(lines) > 1:
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 5:
                processes.append({
                    "pid": parts[0],
                    "user": parts[1],
                    "name": parts[2],
                    "cpu": parts[3],
                    "mem": parts[4]
                })
    return processes

def get_listening_ports():
    _, out, _ = run_cmd("ss -tulpn | grep LISTEN")
    ports = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            proto = parts[0]
            local_addr = parts[4]
            port = local_addr.split(":")[-1]
            proc = "-"
            if len(parts) >= 7:
                raw_proc = parts[6]
                if '("' in raw_proc:
                    try:
                        pname = raw_proc.split('("')[1].split('"')[0]
                        ppid = raw_proc.split('pid=')[1].split(',')[0]
                        proc = f"{pname} (PID: {ppid})"
                    except Exception:
                        proc = raw_proc
                else:
                    proc = raw_proc
            ports.append({"proto": proto.upper(), "port": port, "address": local_addr, "process": proc})
    return ports

def get_audit_log():
    _, out, _ = run_cmd("last -n 8")
    logins = []
    for line in out.splitlines():
        if line.startswith("reboot") or line.startswith("wtmp begins") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 6:
            user = parts[0]
            ip = parts[2]
            time_str = " ".join(parts[3:7])
            duration = parts[-1] if "(" in parts[-1] else "active"
            logins.append({"user": user, "ip": ip, "time": time_str, "duration": duration})
    return logins

def get_system_metrics():
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1, load5, load15 = 0.0, 0.0, 0.0
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

    net_metrics = get_network_metrics()

    return {
        "version": VERSION,
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
        "network": net_metrics,
        "top_processes": get_top_processes(),
        "listening_ports": get_listening_ports(),
        "audit_log": get_audit_log(),
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
        "primary_service": "rustdesksignal",
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
        "primary_service": "gohttpserver",
        "ports": ["8000 (TCP)"],
        "description": "Легковесный HTTP веб-файловый сервер с Basic Auth"
    })

    # 3. Docker Engine & Containerd
    _, doc_status, _ = run_cmd("systemctl is-active docker.service")
    _, doc_ver, _ = run_cmd("docker --version 2>&1 | awk '{print $3}' | tr -d ','")
    doc_version = doc_ver.strip() if doc_ver else "29.1.3"

    containers = get_docker_containers()
    doc_running = len([c for c in containers if c["is_running"]])
    doc_total = len(containers)

    projects.append({
        "id": "docker",
        "name": "Docker Engine",
        "category": "Container Runtime",
        "status": doc_status,
        "installed_version": doc_version,
        "latest_version": doc_version,
        "update_available": False,
        "services": ["docker"],
        "primary_service": "docker",
        "ports": [f"{doc_running}/{doc_total} активных контейнеров"],
        "containers": containers,
        "description": "Среда контейнеризации Docker"
    })

    # 4. Nginx Web Server
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
        "primary_service": "nginx",
        "ports": ["80 (TCP)"],
        "description": "Высокопроизводительный веб-сервер и обратный прокси"
    })

    # 5. Fail2ban Security
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
        "primary_service": "fail2ban",
        "ports": ["-"],
        "description": "Служба защиты от атак полного перебора паролей (брутфорса)"
    })

    # 6. Custom Added Services
    custom_svcs = CONFIG.get("custom_services", [])
    for svc in custom_svcs:
        _, s_status, _ = run_cmd(f"systemctl is-active {svc}.service")
        projects.append({
            "id": svc,
            "name": f"Service: {svc}",
            "category": "Custom Systemd Service",
            "status": s_status,
            "installed_version": "systemd",
            "latest_version": "systemd",
            "update_available": False,
            "services": [svc],
            "primary_service": svc,
            "ports": ["custom"],
            "description": f"Пользовательская служба systemd ({svc})"
        })

    return projects

def get_security_status():
    _, banned_str, _ = run_cmd("fail2ban-client status sshd 2>/dev/null | grep 'Banned IP list:' | cut -d: -f2")
    raw_banned = [ip.strip() for ip in banned_str.split() if ip.strip()]

    banned_ips = []
    for ip in raw_banned:
        geo = resolve_geoip(ip)
        banned_ips.append({
            "ip": ip,
            "country": geo.get("country", "Unknown"),
            "country_code": geo.get("country_code", "UN"),
            "flag": geo.get("flag", "🌐")
        })

    _, lastb_out, _ = run_cmd("lastb -n 12 2>/dev/null")
    failed_logins = []
    for line in lastb_out.splitlines():
        parts = line.split()
        if len(parts) >= 6 and not line.startswith("btmp begins"):
            user = parts[0]
            ip = parts[2]
            date_time = " ".join(parts[3:7])
            geo = resolve_geoip(ip)
            failed_logins.append({
                "user": user,
                "ip": ip,
                "time": date_time,
                "country": geo.get("country", "Unknown"),
                "country_code": geo.get("country_code", "UN"),
                "flag": geo.get("flag", "🌐")
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

        # Special button for Docker to open containers manager
        if p["id"] == "docker":
            actions_html += '<button class="btn btn-sm btn-primary" onclick="openDockerModal()">🐳 Контейнеры</button> '

        # Special button for RustDesk to open RustDesk GUI manager
        if p["id"] == "rustdesk":
            actions_html += '<button class="btn btn-sm btn-primary" onclick="openRustDeskModal()">🖥️ RustDesk</button> '

        if p["services"]:
            s0 = p["primary_service"]
            stop_start = f'<button class="btn btn-sm btn-danger" onclick="serviceAction(\'{s0}\', \'stop\')">Остановить</button>' if p["status"] == "active" else f'<button class="btn btn-sm btn-success" onclick="serviceAction(\'{s0}\', \'start\')">Запустить</button>'
            actions_html += f"""
            <button class="btn btn-sm btn-outline" onclick="openLogsModal('{s0}')">📜 Логи</button>
            <button class="btn btn-sm btn-primary" onclick="serviceAction('{s0}', 'restart')">Перезапуск</button>
            {stop_start}
            """

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
        for b in sec_data["banned_ips"]:
            ip = b["ip"]
            flag = b.get("flag", "🌐")
            country = b.get("country", "")
            banned_html += f"""
            <div class="ip-item">
              <span>{flag} <b>{ip}</b> <span style="font-size: 0.72rem; color: var(--text-muted);">({country})</span></span>
              <button class="btn btn-sm btn-danger" onclick="unbanIp('{ip}')">Разблокировать</button>
            </div>
            """

    # Pre-render Recent Attacks
    attacks_html = ""
    if not sec_data["failed_attempts"]:
        attacks_html = '<div style="color: var(--text-muted); font-size: 0.8rem;">Нет атак в журнале</div>'
    else:
        for f in sec_data["failed_attempts"]:
            flag = f.get("flag", "🌐")
            country = f.get("country", "")
            attacks_html += f"""
            <div class="ip-item">
              <div>
                <span style="color: var(--danger); font-weight: 600;">{f['user']}</span> from <b>{f['ip']}</b> {flag} <span style="font-size: 0.72rem; color: var(--text-muted);">({country})</span>
                <div style="font-size: 0.75rem; color: var(--text-muted);">{f['time']}</div>
              </div>
            </div>
            """

    # Pre-render Top Processes
    top_proc_html = ""
    for pr in sys_data["top_processes"]:
        top_proc_html += f"""
        <tr style="border-bottom: 1px solid var(--card-border);">
          <td style="padding: 6px 8px; font-family: monospace;">{pr['pid']}</td>
          <td style="padding: 6px 8px; font-weight: 600;">{pr['name']}</td>
          <td style="padding: 6px 8px; color: var(--text-muted);">{pr['user']}</td>
          <td style="padding: 6px 8px; font-weight: 600; color: var(--accent);">{pr['cpu']}%</td>
          <td style="padding: 6px 8px; font-weight: 600; color: var(--warning);">{pr['mem']}%</td>
        </tr>
        """

    # Pre-render Audit Log
    audit_html = ""
    for a in sys_data["audit_log"]:
        audit_html += f"""
        <div class="ip-item">
          <div>
            <span style="color: var(--success); font-weight: 600;">👤 {a['user']}</span> from <b>{a['ip']}</b>
            <div style="font-size: 0.75rem; color: var(--text-muted);">{a['time']} ({a['duration']})</div>
          </div>
        </div>
        """

    expected = base64.b64encode(f"{CONFIG['username']}:{CONFIG['password']}".encode()).decode()

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VPS Dashboard v{VERSION} | 185.189.13.52</title>
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
      --modal-bg: rgba(15, 23, 42, 0.85);
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
      --modal-bg: rgba(241, 245, 249, 0.85);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Color Emoji", "Apple Color Emoji", "Segoe UI Emoji", sans-serif; }}
    body {{ background-color: var(--bg); color: var(--text); min-height: 100vh; padding: 1.5rem; transition: background-color 0.3s; }}
    .container {{ max-width: 1280px; margin: 0 auto; }}

    .icon {{ display: inline-block; vertical-align: middle; line-height: 1; font-family: "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif; font-size: 1.1em; }}

    header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem; }}
    .logo-area {{ display: flex; align-items: center; gap: 0.75rem; }}
    .logo-icon {{ width: 42px; height: 42px; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: white; font-size: 1.25rem; box-shadow: 0 4px 10px rgba(59, 130, 246, 0.3); }}
    .title h1 {{ font-size: 1.4rem; font-weight: 700; display: flex; align-items: center; gap: 0.5rem; }}
    .title p {{ color: var(--text-muted); font-size: 0.85rem; }}
    .header-controls {{ display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }}

    .btn {{ background: var(--card); border: 1px solid var(--card-border); color: var(--text); padding: 0.45rem 0.9rem; border-radius: 8px; cursor: pointer; font-size: 0.85rem; font-weight: 500; display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem; transition: all 0.2s; user-select: none; line-height: 1.2; text-decoration: none; }}
    .btn:hover {{ background: var(--card-border); }}
    .btn-primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    .btn-primary:hover {{ background: var(--accent-hover); }}
    .btn-outline {{ background: transparent; border-color: var(--card-border); }}
    .btn-sm {{ padding: 0.35rem 0.65rem; font-size: 0.78rem; border-radius: 6px; }}
    .btn-danger {{ background: rgba(239, 68, 68, 0.15); border-color: var(--danger); color: var(--danger); }}
    .btn-danger:hover {{ background: var(--danger); color: white; }}
    .btn-success {{ background: rgba(16, 185, 129, 0.15); border-color: var(--success); color: var(--success); }}
    .btn-success:hover {{ background: var(--success); color: white; }}

    .badge {{ font-size: 0.75rem; font-weight: 600; padding: 3px 8px; border-radius: 8px; display: inline-flex; align-items: center; gap: 4px; line-height: 1.2; }}
    .project-actions {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; justify-content: flex-end; }}
    .project-card {{ display: flex; justify-content: space-between; align-items: center; padding: 1rem; border-bottom: 1px solid var(--card-border); gap: 1rem; }}

    .select-input {{ background: var(--card); border: 1px solid var(--card-border); color: var(--text); padding: 0.45rem 0.6rem; border-radius: 8px; font-size: 0.85rem; outline: none; cursor: pointer; }}
    .text-input {{ background: var(--card); border: 1px solid var(--card-border); color: var(--text); padding: 0.4rem 0.6rem; border-radius: 6px; font-size: 0.8rem; outline: none; }}

    .live-badge {{ display: flex; align-items: center; gap: 6px; font-size: 0.75rem; font-weight: 600; padding: 4px 10px; border-radius: 20px; background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }}
    .pulse-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--success); animation: pulse 1.5s infinite; }}
    @keyframes pulse {{ 0% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: 0.4; transform: scale(0.8); }} 100% {{ opacity: 1; transform: scale(1); }} }}

    .grid {{ display: grid; gap: 1.25rem; }}
    .grid-5 {{ grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); margin-bottom: 1.5rem; }}
    .grid-2 {{ grid-template-columns: 2fr 1.1fr; margin-bottom: 1.5rem; }}
    @media (max-width: 960px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}

    .card {{ background: var(--card); border: 1px solid var(--card-border); border-radius: 14px; padding: 1.2rem; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem; }}
    .card-title {{ font-size: 1.05rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; }}

    .metric-value {{ font-size: 1.6rem; font-weight: 700; margin-bottom: 0.3rem; }}
    .metric-sub {{ font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.5rem; }}
    .progress-bar {{ width: 100%; height: 7px; background: var(--bar-bg); border-radius: 4px; overflow: hidden; margin-top: 0.4rem; }}
    .progress-fill {{ height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.4s ease; }}
    .progress-fill.warning {{ background: var(--warning); }}
    .progress-fill.danger {{ background: var(--danger); }}

    .sparkline-canvas {{ width: 100%; height: 42px; display: block; margin-top: 6px; border-radius: 6px; }}

    .project-card {{ display: flex; justify-content: space-between; align-items: center; padding: 0.9rem; border-bottom: 1px solid var(--card-border); gap: 0.5rem; }}
    .project-card:last-child {{ border-bottom: none; }}
    .project-info {{ display: flex; flex-direction: column; gap: 0.25rem; }}
    .project-name {{ font-weight: 600; font-size: 0.95rem; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
    .project-desc {{ font-size: 0.78rem; color: var(--text-muted); }}
    .badge {{ font-size: 0.68rem; font-weight: 600; padding: 2px 7px; border-radius: 10px; }}
    .badge-success {{ background: rgba(16, 185, 129, 0.15); color: var(--success); }}
    .badge-danger {{ background: rgba(239, 68, 68, 0.15); color: var(--danger); }}
    .badge-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); }}
    .badge-info {{ background: rgba(59, 130, 246, 0.15); color: var(--accent); }}
    .project-actions {{ display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }}

    .ip-item {{ display: flex; justify-content: space-between; align-items: center; padding: 0.45rem 0; border-bottom: 1px dashed var(--card-border); font-size: 0.82rem; }}
    .ip-item:last-child {{ border-bottom: none; }}

    .terminal-box {{ background: #000; color: #10b981; font-family: monospace; font-size: 0.78rem; padding: 0.8rem; border-radius: 8px; max-height: 200px; overflow-y: auto; margin-top: 0.8rem; white-space: pre-wrap; }}

    /* Modal dialog */
    .modal-overlay {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: var(--modal-bg); backdrop-filter: blur(4px); display: none; align-items: center; justify-content: center; z-index: 2000; padding: 1rem; }}
    .modal-overlay.active {{ display: flex; }}
    .modal-content {{ background: var(--card); border: 1px solid var(--card-border); border-radius: 16px; width: 100%; max-width: 800px; max-height: 85vh; display: flex; flex-direction: column; box-shadow: 0 20px 40px rgba(0,0,0,0.3); overflow: hidden; }}
    .modal-header {{ display: flex; justify-content: space-between; align-items: center; padding: 1rem 1.2rem; border-bottom: 1px solid var(--card-border); }}
    .modal-body {{ padding: 1.2rem; overflow-y: auto; flex: 1; }}
    .modal-footer {{ padding: 0.8rem 1.2rem; border-top: 1px solid var(--card-border); display: flex; justify-content: flex-end; gap: 0.5rem; }}

    #toast {{ position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; background: var(--card); border: 1px solid var(--card-border); color: var(--text); box-shadow: 0 10px 25px rgba(0,0,0,0.2); transform: translateY(100px); opacity: 0; transition: all 0.3s cubic-bezier(0.68, -0.55, 0.265, 1.55); z-index: 3000; font-size: 0.85rem; font-weight: 500; }}
    #toast.show {{ transform: translateY(0); opacity: 1; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo-area">
        <div class="logo-icon">VPS</div>
        <div class="title">
          <h1 id="txt-title">Управление сервером 185.189.13.52 <span class="badge badge-info">v{VERSION}</span></h1>
          <p id="txt-hostinfo">{sys_data['hostname']} | {sys_data['os']} | Uptime: {sys_data['uptime']['days']} дн. {sys_data['uptime']['hours']} ч.</p>
        </div>
      </div>
      <div class="header-controls">
        <div class="live-badge"><div class="pulse-dot"></div> <span id="txt-live">ОНЛАЙН</span></div>
        <select class="select-input" id="refresh-rate" onchange="changeInterval()">
          <option value="3000">⏱️ 3 сек</option>
          <option value="1000">⚡ 1 сек</option>
          <option value="5000">⏳ 5 сек</option>
          <option value="0">⏸️ Пауза</option>
        </select>
        <button class="btn btn-outline" onclick="downloadBackup()">💾 <span id="txt-backup">Бэкап</span></button>
        <button class="btn btn-outline" onclick="updateSelf()">🔄 <span id="txt-update-self">Обновить панель</span></button>
        <button class="btn" id="btn-lang" onclick="toggleLang()">EN / RU</button>
        <button class="btn" id="btn-theme" onclick="toggleTheme()">☀️ / 🌙</button>
        <button class="btn btn-primary" onclick="loadAllData()"><span id="txt-refresh">Обновить</span></button>
      </div>
    </header>

    <!-- 5 Resources Cards Grid -->
    <div class="grid grid-5">
      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-cpu-title">⚡ Процессор</span>
          <span class="badge badge-info" id="cpu-cores">{sys_data['cpu_count']} Core</span>
        </div>
        <div class="metric-value" id="cpu-pct">{sys_data['cpu_pct']}%</div>
        <div class="metric-sub" id="cpu-load">Load: {', '.join(map(str, sys_data['load_avg']))}</div>
        <div class="progress-bar"><div class="progress-fill" id="cpu-bar" style="width: {sys_data['cpu_pct']}%"></div></div>
        <canvas id="cpu-chart" class="sparkline-canvas" width="220" height="42"></canvas>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-ram-title">🧠 Память RAM</span>
          <span class="badge badge-info" id="ram-pct">{sys_data['ram']['pct']}%</span>
        </div>
        <div class="metric-value" id="ram-used">{sys_data['ram']['used_mb']} MB</div>
        <div class="metric-sub" id="ram-sub">{sys_data['ram']['pct']}% of {sys_data['ram']['total_mb']} MB</div>
        <div class="progress-bar"><div class="progress-fill" id="ram-bar" style="width: {sys_data['ram']['pct']}%"></div></div>
        <canvas id="ram-chart" class="sparkline-canvas" width="220" height="42"></canvas>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-net-title">🌐 Сеть (I/O)</span>
          <span class="badge badge-success" id="net-speed-badge">Live</span>
        </div>
        <div class="metric-value" id="net-speed">⬇️ {sys_data['network']['rx_rate_kb']} KB/s</div>
        <div class="metric-sub" id="net-tx-speed">⬆️ {sys_data['network']['tx_rate_kb']} KB/s</div>
        <div class="metric-sub" id="net-totals" style="margin-top: 0.4rem; font-size: 0.72rem;">Всего: ⬇️ {sys_data['network']['total_rx_gb']} GB | ⬆️ {sys_data['network']['total_tx_gb']} GB</div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-disk-title">💾 Накопитель</span>
          <span class="badge badge-info" id="disk-pct">{sys_data['disk']['pct']}%</span>
        </div>
        <div class="metric-value" id="disk-free">{sys_data['disk']['free_gb']} GB</div>
        <div class="metric-sub" id="disk-sub">Свободно из {sys_data['disk']['total_gb']} GB ({sys_data['disk']['pct']}% used)</div>
        <div class="progress-bar"><div class="progress-fill" id="disk-bar" style="width: {sys_data['disk']['pct']}%"></div></div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title" id="txt-swap-title">🔄 Файл подкачки</span>
          <span class="badge badge-info" id="swap-pct">{sys_data['swap']['pct']}%</span>
        </div>
        <div class="metric-value" id="swap-used">{sys_data['swap']['used_mb']} MB</div>
        <div class="metric-sub" id="swap-sub">Всего {sys_data['swap']['total_mb']} MB</div>
        <div class="progress-bar"><div class="progress-fill" id="swap-bar" style="width: {sys_data['swap']['pct']}%"></div></div>
      </div>
    </div>

    <!-- Main Content Layout -->
    <div class="grid grid-2">
      <!-- Left Column: Projects + Top Processes + APT -->
      <div>
        <div class="card" style="margin-bottom: 1.25rem;">
          <div class="card-header">
            <span class="card-title" id="txt-projects-title">📦 Установленные проекты и службы</span>
            <div style="display: flex; gap: 0.4rem;">
              <button class="btn btn-sm btn-outline" onclick="openPortsModal()">🔌 <span id="txt-btn-ports">Порты</span></button>
              <button class="btn btn-sm btn-primary" onclick="addCustomService()">+ <span id="txt-btn-addsvc">Служба</span></button>
            </div>
          </div>
          <div id="projects-list">
            {proj_cards_html}
          </div>
        </div>

        <!-- Top 5 Processes Card -->
        <div class="card" style="margin-bottom: 1.25rem;">
          <div class="card-header">
            <span class="card-title" id="txt-top-proc-title">🔥 Топ-5 процессов (CPU / RAM)</span>
          </div>
          <div style="overflow-x: auto;">
            <table style="width: 100%; text-align: left; font-size: 0.8rem; border-collapse: collapse;">
              <thead>
                <tr style="border-bottom: 1px solid var(--card-border); color: var(--text-muted);">
                  <th style="padding: 6px 8px;">PID</th>
                  <th style="padding: 6px 8px;" id="th-proc-name">Процесс</th>
                  <th style="padding: 6px 8px;" id="th-proc-user">Пользователь</th>
                  <th style="padding: 6px 8px;">% CPU</th>
                  <th style="padding: 6px 8px;">% RAM</th>
                </tr>
              </thead>
              <tbody id="top-processes-tbody">
                {top_proc_html}
              </tbody>
            </table>
          </div>
        </div>

        <!-- System Updates Card -->
        <div class="card">
          <div class="card-header">
            <span class="card-title" id="txt-sysup-title">🔄 Обновления системы (APT)</span>
            <button class="btn btn-sm btn-primary" onclick="checkSysUpdates()" id="btn-check-apt">Проверить обновления</button>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="font-size: 1.05rem; font-weight: 600;" id="apt-status-text">Ожидает проверки</div>
              <div style="font-size: 0.78rem; color: var(--text-muted);" id="apt-sub-text">Нажмите «Проверить обновления»</div>
            </div>
            <button class="btn btn-success" id="btn-upgrade-apt" style="display: none;" onclick="upgradeSystem()">Установить обновления</button>
          </div>
          <div id="update-terminal" class="terminal-box" style="display: none;"></div>
        </div>
      </div>

      <!-- Right Column: Security & Audit -->
      <div>
        <div class="card" style="margin-bottom: 1.25rem;">
          <div class="card-header">
            <span class="card-title" id="txt-sec-title">🛡️ Защита Fail2ban</span>
            <span class="badge badge-danger" id="banned-count">{sec_data['banned_count']} Banned</span>
          </div>
          <p style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.8rem;" id="txt-sec-desc">
            Блокировка ботов и брутфорс атак по SSH.
          </p>

          <!-- Custom Ban input -->
          <div style="display: flex; gap: 0.4rem; margin-bottom: 1rem;">
            <input type="text" id="custom-ban-ip" class="text-input" placeholder="IP для бана (e.g. 1.2.3.4)" style="flex: 1;">
            <button class="btn btn-sm btn-danger" onclick="customBan()"><span id="txt-btn-ban">Забанить IP</span></button>
          </div>

          <div style="font-weight: 600; font-size: 0.82rem; margin-bottom: 0.4rem;" id="txt-banned-list">Список заблокированных IP:</div>
          <div id="banned-ips-list" style="margin-bottom: 1.2rem; max-height: 180px; overflow-y: auto;">
            {banned_html}
          </div>

          <div style="font-weight: 600; font-size: 0.82rem; margin-bottom: 0.4rem;" id="txt-recent-attacks">Последние попытки перебора:</div>
          <div id="failed-logins-list" style="max-height: 200px; overflow-y: auto;">
            {attacks_html}
          </div>
        </div>

        <!-- Audit Log Card -->
        <div class="card">
          <div class="card-header">
            <span class="card-title" id="txt-audit-title">🔑 Успешные входы на сервер</span>
          </div>
          <div id="audit-logins-list" style="max-height: 220px; overflow-y: auto;">
            {audit_html}
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Modal: Live Logs Viewer -->
  <div class="modal-overlay" id="logs-modal">
    <div class="modal-content">
      <div class="modal-header">
        <h3 style="font-size: 1.1rem;" id="logs-modal-title">📜 Логи службы</h3>
        <button class="btn btn-sm btn-outline" onclick="closeLogsModal()">✕</button>
      </div>
      <div class="modal-body">
        <div id="logs-modal-terminal" class="terminal-box" style="max-height: 55vh; height: 55vh; margin: 0;">Загрузка журнала...</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-outline" onclick="refreshCurrentLogs()">🔄 Обновить</button>
        <button class="btn btn-primary" onclick="closeLogsModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <!-- Modal: Listening Ports -->
  <div class="modal-overlay" id="ports-modal">
    <div class="modal-content">
      <div class="modal-header">
        <h3 style="font-size: 1.1rem;" id="txt-ports-modal-title">🔌 Слушающие сетевые порты (Listening Ports)</h3>
        <button class="btn btn-sm btn-outline" onclick="closePortsModal()">✕</button>
      </div>
      <div class="modal-body">
        <table style="width: 100%; text-align: left; font-size: 0.85rem; border-collapse: collapse;">
          <thead>
            <tr style="border-bottom: 1px solid var(--card-border); color: var(--text-muted);">
              <th style="padding: 8px;">Протокол</th>
              <th style="padding: 8px;">Порт</th>
              <th style="padding: 8px;">Адрес</th>
              <th style="padding: 8px;">Процесс / Служба</th>
            </tr>
          </thead>
          <tbody id="ports-modal-tbody">
          </tbody>
        </table>
      </div>
      <div class="modal-footer">
        <button class="btn btn-primary" onclick="closePortsModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <!-- Modal: Docker Containers Manager -->
  <div class="modal-overlay" id="docker-modal">
    <div class="modal-content" style="max-width: 860px;">
      <div class="modal-header">
        <h3 style="font-size: 1.1rem;" id="txt-docker-modal-title">🐳 Управление контейнерами Docker</h3>
        <button class="btn btn-sm btn-outline" onclick="closeDockerModal()">✕</button>
      </div>
      <div class="modal-body">
        <div id="docker-modal-content">
          <table style="width: 100%; text-align: left; font-size: 0.82rem; border-collapse: collapse;">
            <thead>
              <tr style="border-bottom: 1px solid var(--card-border); color: var(--text-muted);">
                <th style="padding: 8px;">Имя</th>
                <th style="padding: 8px;">Образ</th>
                <th style="padding: 8px;">Статус</th>
                <th style="padding: 8px;">Порты</th>
                <th style="padding: 8px; text-align: right;">Действия</th>
              </tr>
            </thead>
            <tbody id="docker-modal-tbody">
            </tbody>
          </table>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-outline" onclick="refreshDockerModal()">🔄 Обновить</button>
        <button class="btn btn-primary" onclick="closeDockerModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <!-- Modal: RustDesk Server GUI & Peers -->
  <div class="modal-overlay" id="rustdesk-modal">
    <div class="modal-content" style="max-width: 900px;">
      <div class="modal-header">
        <h3 style="font-size: 1.1rem;" id="txt-rd-modal-title">🖥️ Управление RustDesk Server</h3>
        <button class="btn btn-sm btn-outline" onclick="closeRustDeskModal()">✕</button>
      </div>
      <div class="modal-body">
        <!-- Client Setup & Keys Block -->
        <div style="background: var(--bar-bg); padding: 1rem; border-radius: 10px; margin-bottom: 1.2rem;">
          <div style="font-weight: 600; font-size: 0.95rem; margin-bottom: 0.6rem; display: flex; align-items: center; justify-content: space-between;">
            <span id="txt-rd-cfg-title">📋 Параметры подключения клиентов (Client Setup)</span>
            <button class="btn btn-sm btn-primary" onclick="copyConfigString()">📋 Скопировать готовую строку</button>
          </div>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0.8rem; font-size: 0.82rem;">
            <div>
              <div style="color: var(--text-muted); margin-bottom: 2px;">ID / Rendezvous Server:</div>
              <div style="display: flex; gap: 4px;">
                <input type="text" id="rd-id-server" class="text-input" readonly style="flex: 1; font-family: monospace;">
                <button class="btn btn-sm btn-outline" onclick="copyField('rd-id-server')">📋</button>
              </div>
            </div>
            <div>
              <div style="color: var(--text-muted); margin-bottom: 2px;">Relay Server:</div>
              <div style="display: flex; gap: 4px;">
                <input type="text" id="rd-relay-server" class="text-input" readonly style="flex: 1; font-family: monospace;">
                <button class="btn btn-sm btn-outline" onclick="copyField('rd-relay-server')">📋</button>
              </div>
            </div>
          </div>
          <div style="margin-top: 0.8rem; font-size: 0.82rem;">
            <div style="color: var(--text-muted); margin-bottom: 2px;">Public Key (Шифрование):</div>
            <div style="display: flex; gap: 4px;">
              <input type="text" id="rd-pub-key" class="text-input" readonly style="flex: 1; font-family: monospace;">
              <button class="btn btn-sm btn-outline" onclick="copyField('rd-pub-key')">📋</button>
            </div>
          </div>
        </div>

        <!-- Peers List Table -->
        <div>
          <div style="font-weight: 600; font-size: 0.95rem; margin-bottom: 0.6rem; display: flex; align-items: center; justify-content: space-between;">
            <span id="txt-rd-peers-title">👥 Зарегистрированные устройства (Peers)</span>
            <span class="badge badge-info" id="rd-peers-count">0 устройств</span>
          </div>
          <div style="max-height: 280px; overflow-y: auto;">
            <table style="width: 100%; text-align: left; font-size: 0.82rem; border-collapse: collapse;">
              <thead>
                <tr style="border-bottom: 1px solid var(--card-border); color: var(--text-muted);">
                  <th style="padding: 8px;">Device ID</th>
                  <th style="padding: 8px;">IP / Локация</th>
                  <th style="padding: 8px;">UUID</th>
                  <th style="padding: 8px; text-align: right;">Действие</th>
                </tr>
              </thead>
              <tbody id="rd-peers-tbody">
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-outline" onclick="openLogsModal('rustdesksignal')">📜 Логи Signal</button>
        <button class="btn btn-outline" onclick="openLogsModal('rustdeskrelay')">📜 Логи Relay</button>
        <button class="btn btn-primary" onclick="restartRustDeskBoth()">🔄 Перезапустить оба</button>
        <button class="btn btn-outline" onclick="refreshRustDeskData()">🔄 Обновить</button>
        <button class="btn btn-primary" onclick="closeRustDeskModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <div id="toast">Уведомление</div>

  <script>
    const API_AUTH = "Basic {expected}";
    let currentLang = localStorage.getItem("lang") || "ru";
    let currentTheme = localStorage.getItem("theme") || "dark";
    let refreshTimer = null;
    let currentInterval = 3000;
    let activeLogService = "";

    const cpuHistory = [{sys_data['cpu_pct']}];
    const ramHistory = [{sys_data['ram']['pct']}];

    const dict = {{
      ru: {{
        title: "Управление сервером 185.189.13.52",
        live: "ОНЛАЙН",
        refresh: "Обновить",
        backup: "Бэкап",
        updateSelf: "Обновить панель",
        cpu: "⚡ Процессор",
        ram: "🧠 Память RAM",
        net: "🌐 Сеть (I/O)",
        swap: "🔄 Файл подкачки",
        disk: "💾 Накопитель",
        projects: "📦 Установленные проекты и службы",
        topProc: "🔥 Топ-5 процессов (CPU / RAM)",
        sysup: "🔄 Обновления системы (APT)",
        sec: "🛡️ Защита Fail2ban",
        secDesc: "Блокировка ботов и брутфорс атак по SSH.",
        audit: "🔑 Успешные входы на сервер",
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
        banBtn: "Забанить IP",
        portsBtn: "Порты",
        addSvcBtn: "Служба",
        thProcName: "Процесс",
        thProcUser: "Пользователь",
        uptimeDays: "дн.",
        uptimeHours: "ч.",
        uptimeMins: "мин."
      }},
      en: {{
        title: "VPS Dashboard 185.189.13.52",
        live: "ONLINE",
        refresh: "Refresh",
        backup: "Backup",
        updateSelf: "Update Dashboard",
        cpu: "⚡ CPU Load",
        ram: "🧠 RAM Memory",
        net: "🌐 Network (I/O)",
        swap: "🔄 Swap File",
        disk: "💾 Disk Storage",
        projects: "📦 Installed Projects & Services",
        topProc: "🔥 Top 5 Processes (CPU / RAM)",
        sysup: "🔄 System Updates (APT)",
        sec: "🛡️ Fail2ban Security",
        secDesc: "Active IP bans against brute-force password attempts.",
        audit: "🔑 Successful Logins",
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
        banBtn: "Ban IP",
        portsBtn: "Ports",
        addSvcBtn: "Service",
        thProcName: "Process",
        thProcUser: "User",
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

    function drawSparkline(canvasId, data, color) {{
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      if (data.length < 2) return;

      const step = w / (data.length - 1);
      ctx.beginPath();
      ctx.moveTo(0, h - (data[0] / 100) * (h - 6) - 3);

      for (let i = 1; i < data.length; i++) {{
        const x = i * step;
        const y = h - (data[i] / 100) * (h - 6) - 3;
        ctx.lineTo(x, y);
      }}

      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Gradient Fill
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, color + "55");
      grad.addColorStop(1, color + "05");
      ctx.fillStyle = grad;
      ctx.fill();
    }}

    function applyLang() {{
      const t = dict[currentLang];
      document.getElementById("txt-title").innerHTML = `${{t.title}} <span class="badge badge-info">v{VERSION}</span>`;
      document.getElementById("txt-live").innerText = t.live;
      document.getElementById("txt-refresh").innerText = t.refresh;
      document.getElementById("txt-backup").innerText = t.backup;
      document.getElementById("txt-update-self").innerText = t.updateSelf;
      document.getElementById("txt-cpu-title").innerText = t.cpu;
      document.getElementById("txt-ram-title").innerText = t.ram;
      document.getElementById("txt-net-title").innerText = t.net;
      document.getElementById("txt-swap-title").innerText = t.swap;
      document.getElementById("txt-disk-title").innerText = t.disk;
      document.getElementById("txt-projects-title").innerText = t.projects;
      document.getElementById("txt-top-proc-title").innerText = t.topProc;
      document.getElementById("txt-sysup-title").innerText = t.sysup;
      document.getElementById("txt-sec-title").innerText = t.sec;
      document.getElementById("txt-sec-desc").innerText = t.secDesc;
      document.getElementById("txt-audit-title").innerText = t.audit;
      document.getElementById("txt-banned-list").innerText = t.bannedList;
      document.getElementById("txt-recent-attacks").innerText = t.recentAttacks;
      document.getElementById("btn-check-apt").innerText = t.checkApt;
      document.getElementById("btn-upgrade-apt").innerText = t.upgradeApt;
      document.getElementById("txt-btn-ban").innerText = t.banBtn;
      document.getElementById("txt-btn-ports").innerText = t.portsBtn;
      document.getElementById("txt-btn-addsvc").innerText = t.addSvcBtn;
      document.getElementById("th-proc-name").innerText = t.thProcName;
      document.getElementById("th-proc-user").innerText = t.thProcUser;
      const dTitle = document.getElementById("txt-docker-modal-title");
      if (dTitle) dTitle.innerText = (currentLang === "ru") ? "🐳 Управление контейнерами Docker" : "🐳 Docker Containers Manager";
    }}

    function toggleLang() {{
      currentLang = (currentLang === "ru") ? "en" : "ru";
      localStorage.setItem("lang", currentLang);
      applyLang();
      loadAllData();
    }}

    function applyTheme() {{
      document.documentElement.setAttribute("data-theme", currentTheme);
      drawSparkline("cpu-chart", cpuHistory, "#3b82f6");
      drawSparkline("ram-chart", ramHistory, "#10b981");
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

    function changeInterval() {{
      const val = parseInt(document.getElementById("refresh-rate").value);
      currentInterval = val;
      if (refreshTimer) clearInterval(refreshTimer);
      if (val > 0) {{
        refreshTimer = setInterval(loadAllData, val);
        showToast("Автообновление: " + (val / 1000) + " сек");
      }} else {{
        showToast("Автообновление приостановлено");
      }}
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

        cpuHistory.push(data.cpu_pct);
        if (cpuHistory.length > 25) cpuHistory.shift();
        drawSparkline("cpu-chart", cpuHistory, "#3b82f6");

        document.getElementById("ram-pct").innerText = `${{data.ram.pct}}%`;
        document.getElementById("ram-used").innerText = `${{data.ram.used_mb}} MB`;
        document.getElementById("ram-sub").innerText = `${{data.ram.pct}}% of ${{data.ram.total_mb}} MB (${{data.ram.free_mb}} MB free)`;
        const ramBar = document.getElementById("ram-bar");
        ramBar.style.width = `${{data.ram.pct}}%`;
        ramBar.className = "progress-fill" + (data.ram.pct > 85 ? " danger" : data.ram.pct > 65 ? " warning" : "");

        ramHistory.push(data.ram.pct);
        if (ramHistory.length > 25) ramHistory.shift();
        drawSparkline("ram-chart", ramHistory, "#10b981");

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

        // Network
        if (data.network) {{
          document.getElementById("net-speed").innerText = `⬇️ ${{data.network.rx_rate_kb}} KB/s`;
          document.getElementById("net-tx-speed").innerText = `⬆️ ${{data.network.tx_rate_kb}} KB/s`;
          document.getElementById("net-totals").innerText = `Всего: ⬇️ ${{data.network.total_rx_gb}} GB | ⬆️ ${{data.network.total_tx_gb}} GB`;
        }}

        // Top Processes
        if (data.top_processes) {{
          const tbody = document.getElementById("top-processes-tbody");
          tbody.innerHTML = "";
          data.top_processes.forEach(pr => {{
            const tr = document.createElement("tr");
            tr.style.borderBottom = "1px solid var(--card-border)";
            tr.innerHTML = `
              <td style="padding: 6px 8px; font-family: monospace;">${{pr.pid}}</td>
              <td style="padding: 6px 8px; font-weight: 600;">${{pr.name}}</td>
              <td style="padding: 6px 8px; color: var(--text-muted);">${{pr.user}}</td>
              <td style="padding: 6px 8px; font-weight: 600; color: var(--accent);">${{pr.cpu}}%</td>
              <td style="padding: 6px 8px; font-weight: 600; color: var(--warning);">${{pr.mem}}%</td>
            `;
            tbody.appendChild(tr);
          }});
        }}

        // Audit Log
        if (data.audit_log) {{
          const auditList = document.getElementById("audit-logins-list");
          auditList.innerHTML = "";
          data.audit_log.forEach(a => {{
            const div = document.createElement("div");
            div.className = "ip-item";
            div.innerHTML = `
              <div>
                <span style="color: var(--success); font-weight: 600;">👤 ${{a.user}}</span> from <b>${{a.ip}}</b>
                <div style="font-size: 0.75rem; color: var(--text-muted);">${{a.time}} (${{a.duration}})</div>
              </div>
            `;
            auditList.appendChild(div);
          }});
        }}
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

          let extraBtns = "";
          if (p.id === "docker") {{
            extraBtns = `<button class="btn btn-sm btn-primary" onclick="openDockerModal()">🐳 Контейнеры</button> `;
          }}
          if (p.id === "rustdesk") {{
            extraBtns = `<button class="btn btn-sm btn-primary" onclick="openRustDeskModal()">🖥️ RustDesk</button> `;
          }}

          div.innerHTML = `
            <div class="project-info">
              <div class="project-name">
                ${{p.name}} ${{statusBadge}} ${{updateBadge}}
              </div>
              <div class="project-desc">${{p.description}} | Порты: ${{p.ports.join(", ")}}</div>
            </div>
            <div class="project-actions">
              ${{extraBtns}}
              ${{p.services.length > 0 ? `
                <button class="btn btn-sm btn-outline" onclick="openLogsModal('${{p.primary_service}}')">📜 Логи</button>
                <button class="btn btn-sm btn-primary" onclick="serviceAction('${{p.primary_service}}', 'restart')">${{t.restart}}</button>
                ${{p.status === 'active'
                  ? `<button class="btn btn-sm btn-danger" onclick="serviceAction('${{p.primary_service}}', 'stop')">${{t.stop}}</button>`
                  : `<button class="btn btn-sm btn-success" onclick="serviceAction('${{p.primary_service}}', 'start')">${{t.start}}</button>`
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
          sec.banned_ips.forEach(b => {{
            const ip = b.ip || b;
            const flag = b.flag || "🌐";
            const country = b.country || "";
            const div = document.createElement("div");
            div.className = "ip-item";
            div.innerHTML = `
              <span>${{flag}} <b>${{ip}}</b> <span style="font-size: 0.72rem; color: var(--text-muted);">(${{country}})</span></span>
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
            const flag = f.flag || "🌐";
            const country = f.country || "";
            const div = document.createElement("div");
            div.className = "ip-item";
            div.innerHTML = `
              <div>
                <span style="color: var(--danger); font-weight: 600;">${{f.user}}</span> from <b>${{f.ip}}</b> ${{flag}} <span style="font-size: 0.72rem; color: var(--text-muted);">(${{country}})</span>
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

    async function customBan() {{
      const ip = document.getElementById("custom-ban-ip").value.trim();
      if (!ip) {{
        showToast("Введите корректный IP-адрес");
        return;
      }}
      try {{
        showToast("Блокировка IP " + ip + "...");
        const data = await apiFetch("/api/security/ban", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ ip }})
        }});
        if (data.success) {{
          showToast("IP " + ip + " успешно заблокирован");
          document.getElementById("custom-ban-ip").value = "";
          loadSecurity();
        }} else {{
          showToast("Ошибка: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка сети");
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

    async function updateSelf() {{
      if (!confirm("Обновить саму веб-панель из GitHub репозитория Skverna84/vps-dashboard?")) return;
      showToast("Загрузка обновления панели...");
      try {{
        const data = await apiFetch("/api/update/self", {{ method: "POST" }});
        if (data.success) {{
          showToast("Панель обновлена! Перезагрузка страницы...");
          setTimeout(() => location.reload(), 2500);
        }} else {{
          showToast("Ошибка обновления: " + data.error);
        }}
      }} catch (e) {{
        showToast("Панель перезапускается...");
        setTimeout(() => location.reload(), 2500);
      }}
    }}

    function downloadBackup() {{
      showToast("Формирование бэкапа...");
      window.location.href = "/api/backup";
    }}

    async function openLogsModal(svc) {{
      activeLogService = svc;
      document.getElementById("logs-modal-title").innerText = "📜 Логи службы: " + svc;
      document.getElementById("logs-modal").classList.add("active");
      const term = document.getElementById("logs-modal-terminal");
      term.innerText = "Загрузка журнала " + svc + "...";
      try {{
        const data = await apiFetch("/api/service/logs?service=" + svc);
        term.innerText = data.logs;
        term.scrollTop = term.scrollHeight;
      }} catch (e) {{
        term.innerText = "Ошибка загрузки логов: " + e.message;
      }}
    }}

    function closeLogsModal() {{
      document.getElementById("logs-modal").classList.remove("active");
    }}

    function refreshCurrentLogs() {{
      if (activeLogService) openLogsModal(activeLogService);
    }}

    async function openPortsModal() {{
      document.getElementById("ports-modal").classList.add("active");
      const tbody = document.getElementById("ports-modal-tbody");
      tbody.innerHTML = "<tr><td colspan='4' style='padding: 1rem; text-align: center;'>Загрузка портов...</td></tr>";
      try {{
        const data = await apiFetch("/api/system");
        tbody.innerHTML = "";
        if (data.listening_ports) {{
          data.listening_ports.forEach(p => {{
            const tr = document.createElement("tr");
            tr.style.borderBottom = "1px solid var(--card-border)";
            tr.innerHTML = `
              <td style="padding: 6px 8px;"><span class="badge badge-info">${{p.proto}}</span></td>
              <td style="padding: 6px 8px; font-weight: 700; color: var(--accent);">${{p.port}}</td>
              <td style="padding: 6px 8px; font-family: monospace;">${{p.address}}</td>
              <td style="padding: 6px 8px; font-weight: 600;">${{p.process}}</td>
            `;
            tbody.appendChild(tr);
          }});
        }}
      }} catch (e) {{
        tbody.innerHTML = "<tr><td colspan='4' style='color: var(--danger); padding: 1rem;'>Ошибка загрузки</td></tr>";
      }}
    }}

    function closePortsModal() {{
      document.getElementById("ports-modal").classList.remove("active");
    }}

    async function openDockerModal() {{
      document.getElementById("docker-modal").classList.add("active");
      refreshDockerModal();
    }}

    function closeDockerModal() {{
      document.getElementById("docker-modal").classList.remove("active");
    }}

    async function refreshDockerModal() {{
      const tbody = document.getElementById("docker-modal-tbody");
      tbody.innerHTML = "<tr><td colspan='5' style='padding: 1rem; text-align: center;'>Загрузка контейнеров...</td></tr>";
      try {{
        const containers = await apiFetch("/api/docker/containers");
        tbody.innerHTML = "";
        if (!containers || containers.length === 0) {{
          tbody.innerHTML = "<tr><td colspan='5' style='padding: 1.5rem; text-align: center; color: var(--text-muted);'>Нет созданных контейнеров Docker</td></tr>";
          return;
        }}
        containers.forEach(c => {{
          const tr = document.createElement("tr");
          tr.style.borderBottom = "1px solid var(--card-border)";
          const statusBadge = c.is_running
            ? `<span class="badge badge-success">Running</span>`
            : `<span class="badge badge-danger">Stopped</span>`;
          const actionBtn = c.is_running
            ? `<button class="btn btn-sm btn-danger" onclick="dockerAction('${{c.name}}', 'stop')">Остановить</button>`
            : `<button class="btn btn-sm btn-success" onclick="dockerAction('${{c.name}}', 'start')">Запустить</button>`;

          tr.innerHTML = `
            <td style="padding: 8px; font-weight: 600;">🐳 ${{c.name}}</td>
            <td style="padding: 8px; font-family: monospace; font-size: 0.75rem; color: var(--text-muted);">${{c.image}}</td>
            <td style="padding: 8px;">${{statusBadge}} <span style="font-size: 0.72rem; color: var(--text-muted);">${{c.status_str}}</span></td>
            <td style="padding: 8px; font-size: 0.75rem; font-family: monospace;">${{c.ports}}</td>
            <td style="padding: 8px; text-align: right;">
              <button class="btn btn-sm btn-outline" onclick="openDockerLogsModal('${{c.name}}')">📜 Логи</button>
              <button class="btn btn-sm btn-primary" onclick="dockerAction('${{c.name}}', 'restart')">Рестарт</button>
              ${{actionBtn}}
            </td>
          `;
          tbody.appendChild(tr);
        }});
      }} catch (e) {{
        tbody.innerHTML = "<tr><td colspan='5' style='color: var(--danger); padding: 1rem;'>Ошибка загрузки: " + e.message + "</td></tr>";
      }}
    }}

    async function dockerAction(container, action) {{
      try {{
        showToast("Docker: " + action + " " + container + "...");
        const data = await apiFetch("/api/docker/action", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ container, action }})
        }});
        if (data.success) {{
          showToast("Успешно: " + container + " -> " + action);
          refreshDockerModal();
          loadProjects();
        }} else {{
          showToast("Ошибка: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка сети");
      }}
    }}

    async function openDockerLogsModal(container) {{
      document.getElementById("logs-modal-title").innerText = "📜 Логи контейнера Docker: " + container;
      document.getElementById("logs-modal").classList.add("active");
      const term = document.getElementById("logs-modal-terminal");
      term.innerText = "Загрузка журнала контейнера " + container + "...";
      try {{
        const data = await apiFetch("/api/docker/logs?container=" + container);
        term.innerText = data.logs;
        term.scrollTop = term.scrollHeight;
      }} catch (e) {{
        term.innerText = "Ошибка загрузки логов: " + e.message;
      }}
    }}

    // --- RustDesk GUI Functions ---
    async function openRustDeskModal() {{
      document.getElementById("rustdesk-modal").classList.add("active");
      refreshRustDeskData();
    }}

    function closeRustDeskModal() {{
      document.getElementById("rustdesk-modal").classList.remove("active");
    }}

    let cachedRustDeskConfig = "";

    async function refreshRustDeskData() {{
      try {{
        const data = await apiFetch("/api/rustdesk/info");
        document.getElementById("rd-id-server").value = data.id_server || "";
        document.getElementById("rd-relay-server").value = data.relay_server || "";
        document.getElementById("rd-pub-key").value = data.public_key || "";
        document.getElementById("rd-peers-count").innerText = `${{data.peers_count}} устройств`;
        cachedRustDeskConfig = data.config_string || "";

        const tbody = document.getElementById("rd-peers-tbody");
        tbody.innerHTML = "";
        if (!data.peers || data.peers.length === 0) {{
          tbody.innerHTML = "<tr><td colspan='4' style='padding: 1.5rem; text-align: center; color: var(--text-muted);'>Нет подключенных устройств</td></tr>";
          return;
        }}
        data.peers.forEach(p => {{
          const tr = document.createElement("tr");
          tr.style.borderBottom = "1px solid var(--card-border)";
          tr.innerHTML = `
            <td style="padding: 8px; font-weight: 700; color: var(--accent);">💻 ${{p.id}}</td>
            <td style="padding: 8px;">${{p.flag}} <b>${{p.ip}}</b> <span style="font-size: 0.72rem; color: var(--text-muted);">(${{p.country}})</span></td>
            <td style="padding: 8px; font-family: monospace; font-size: 0.72rem; color: var(--text-muted);">${{p.uuid}}</td>
            <td style="padding: 8px; text-align: right;">
              <button class="btn btn-sm btn-danger" onclick="deleteRustDeskPeer('${{p.id}}')">Удалить</button>
            </td>
          `;
          tbody.appendChild(tr);
        }});
      }} catch (e) {{
        showToast("Ошибка загрузки данных RustDesk: " + e.message);
      }}
    }}

    async function deleteRustDeskPeer(id) {{
      if (!confirm(`Удалить устройство ID ${id} из базы RustDesk?`)) return;
      try {{
        showToast(`Удаление устройства ${id}...`);
        const data = await apiFetch("/api/rustdesk/peer/delete", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ id: id }})
        }});
        if (data.success) {{
          showToast(`Устройство ${id} удалено`);
          refreshRustDeskData();
        }} else {{
          showToast("Ошибка: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка сети");
      }}
    }}

    async function restartRustDeskBoth() {{
      try {{
        showToast("Перезапуск служб RustDesk (Signal + Relay)...");
        await apiFetch("/api/service/action", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ service: "rustdesksignal", action: "restart" }})
        }});
        await apiFetch("/api/service/action", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ service: "rustdeskrelay", action: "restart" }})
        }});
        showToast("RustDesk успешно перезапущен!");
        refreshRustDeskData();
        loadProjects();
      }} catch (e) {{
        showToast("Ошибка перезапуска");
      }}
    }}

    function copyConfigString() {{
      if (cachedRustDeskConfig) {{
        navigator.clipboard.writeText(cachedRustDeskConfig);
        showToast("Строка конфигурации скопирована в буфер!");
      }} else {{
        showToast("Конфигурация еще не загружена");
      }}
    }}

    function copyField(fieldId) {{
      const el = document.getElementById(fieldId);
      if (el && el.value) {{
        navigator.clipboard.writeText(el.value);
        showToast("Скопировано: " + el.value);
      }}
    }}

    async function addCustomService() {{
      const svc = prompt("Введите имя systemd-службы (например, wireguard, cron, ufw):");
      if (!svc) return;
      try {{
        showToast("Добавление службы " + svc + "...");
        const data = await apiFetch("/api/service/add", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ service: svc.trim() }})
        }});
        if (data.success) {{
          showToast("Служба добавлена!");
          loadProjects();
        }} else {{
          showToast("Ошибка: " + data.error);
        }}
      }} catch (e) {{
        showToast("Ошибка сети");
      }}
    }}

    function loadAllData() {{
      loadSystemStats();
      loadProjects();
      loadSecurity();
    }}

    applyTheme();
    applyLang();
    drawSparkline("cpu-chart", cpuHistory, "#3b82f6");
    drawSparkline("ram-chart", ramHistory, "#10b981");
    refreshTimer = setInterval(loadAllData, currentInterval);
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

    def do_HEAD(self):
        self.do_GET()

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

        elif path == "/api/service/logs":
            qs = parse_qs(urlparse(self.path).query)
            service = qs.get("service", [""])[0].strip()
            custom_svcs = CONFIG.get("custom_services", [])
            allowed_services = ["rustdesksignal", "rustdeskrelay", "gohttpserver", "docker", "nginx", "fail2ban", "vps-dashboard", "ssh"] + custom_svcs

            if service in allowed_services:
                code, out, err = run_cmd(f"journalctl -u {service}.service -n 100 --no-pager", timeout=10)
                self.send_json({"service": service, "logs": out or err or "No logs available."})
            else:
                self.send_json({"error": "Invalid service"}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/docker/logs":
            qs = parse_qs(urlparse(self.path).query)
            container = qs.get("container", [""])[0].strip()
            if container and (container.isalnum() or "-" in container or "_" in container):
                code, out, err = run_cmd(f"docker logs --tail 100 --timestamps {container}", timeout=10)
                self.send_json({"container": container, "logs": out or err or "No logs available for container."})
            else:
                self.send_json({"error": "Invalid container name or id"}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/docker/containers":
            containers = get_docker_containers()
            self.send_json(containers)
            return

        elif path == "/api/rustdesk/info":
            data = get_rustdesk_details()
            self.send_json(data)
            return

        elif path == "/api/backup":
            now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"vps_backup_{now_str}.tar.gz"
            tmp_tar = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
            tmp_tar.close()

            paths_to_backup = [
                "/opt/vps-dashboard/config.json",
                "/etc/systemd/system/vps-dashboard.service",
                "/opt/rustdesk/db_v2.sqlite3",
                "/opt/rustdesk/id_ed25519",
                "/opt/rustdesk/id_ed25519.pub",
                "/etc/fail2ban/jail.local",
                "/etc/nginx/sites-available",
                "/etc/nginx/sites-enabled"
            ]
            with tarfile.open(tmp_tar.name, "w:gz") as tar:
                for p in paths_to_backup:
                    if os.path.exists(p):
                        tar.add(p, arcname=p.lstrip("/"))

            with open(tmp_tar.name, "rb") as f:
                content = f.read()
            try:
                os.unlink(tmp_tar.name)
            except Exception:
                pass

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
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
            custom_svcs = CONFIG.get("custom_services", [])
            allowed_services = ["rustdesksignal", "rustdeskrelay", "gohttpserver", "docker", "nginx", "fail2ban", "ssh", "vps-dashboard"] + custom_svcs
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

        elif path == "/api/docker/action":
            container = payload.get("container", "").strip()
            act = payload.get("action", "").strip()
            allowed_actions = ["start", "stop", "restart"]
            if container and act in allowed_actions:
                code, out, err = run_cmd(f"docker {act} {container}")
                if code == 0:
                    self.send_json({"success": True, "message": f"Container {container} {act}ed"})
                else:
                    self.send_json({"success": False, "error": err or out})
            else:
                self.send_json({"success": False, "error": "Invalid container or action"})
            return

        elif path == "/api/rustdesk/peer/delete":
            pid = payload.get("id", "").strip()
            db_path = "/opt/rustdesk/db_v2.sqlite3"
            if pid and os.path.exists(db_path):
                try:
                    con = sqlite3.connect(db_path, timeout=3.0)
                    cur = con.cursor()
                    cur.execute("DELETE FROM peer WHERE id = ?", (pid,))
                    con.commit()
                    con.close()
                    self.send_json({"success": True, "message": f"Peer {pid} deleted"})
                except Exception as e:
                    self.send_json({"success": False, "error": str(e)})
            else:
                self.send_json({"success": False, "error": "Invalid peer ID or database not found"})
            return

        elif path == "/api/service/add":
            svc = payload.get("service", "").strip()
            if svc and svc.isalnum() or "-" in svc or "_" in svc:
                custom_svcs = CONFIG.get("custom_services", [])
                if svc not in custom_svcs:
                    custom_svcs.append(svc)
                    CONFIG["custom_services"] = custom_svcs
                    save_config(CONFIG)
                self.send_json({"success": True, "message": f"Service {svc} added"})
            else:
                self.send_json({"success": False, "error": "Invalid service name"})
            return

        elif path == "/api/security/ban":
            ip = payload.get("ip", "").strip()
            if ip:
                code, out, err = run_cmd(f"fail2ban-client set sshd banip {ip}")
                if code == 0:
                    self.send_json({"success": True, "message": f"IP {ip} banned"})
                else:
                    self.send_json({"success": False, "error": err or out})
            else:
                self.send_json({"success": False, "error": "IP is required"})
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

        elif path == "/api/update/self":
            cmd = """
            curl -sSL "https://raw.githubusercontent.com/Skverna84/vps-dashboard/main/app.py" -o /tmp/app_new.py
            python3 -m py_compile /tmp/app_new.py
            if [ $? -eq 0 ]; then
                cp /tmp/app_new.py /opt/vps-dashboard/app.py
                chmod +x /opt/vps-dashboard/app.py
                echo "SUCCESS"
                (sleep 1 && systemctl restart vps-dashboard.service) &
            else
                echo "COMPILE_ERROR"
            fi
            """
            code, out, err = run_cmd(cmd, timeout=30)
            if "SUCCESS" in out:
                self.send_json({"success": True, "message": "Dashboard updated and restarting"})
            else:
                self.send_json({"success": False, "error": err or out})
            return

        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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
    print(f"VPS Dashboard v{VERSION} running on port {port}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
