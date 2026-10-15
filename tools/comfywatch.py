"""Живой прогресс ComfyUI для строки состояния: фоновый поток слушает websocket Comfy (без сторонних библиотек)
и держит в STATE текущую ноду, шаг выборки и время последнего события — по нему видно, что Comfy завис.
"""
import base64
import json
import os
import socket
import threading
import time
import urllib.request

import config
from urllib.parse import urlparse


def _addr():
    u = urlparse(config.get("comfy_url"))
    return u.hostname or "127.0.0.1", u.port or 8188


HOST, PORT = _addr()
STATE = {"connected": False, "prompt_id": None, "node": None, "cls": None, "value": None, "max": None,
         "node_since": None, "last_event": None, "started": None}
_classes = {}  # prompt_id -> {node_id: class_type}


def _node_class(prompt_id, node):
    if prompt_id not in _classes:
        try:
            with urllib.request.urlopen(f"http://{HOST}:{PORT}/queue", timeout=5) as r:
                q = json.loads(r.read())
            for item in q.get("queue_running", []) + q.get("queue_pending", []):
                _classes[item[1]] = {k: v.get("class_type") for k, v in item[2].items()}
        except Exception:  # noqa: BLE001
            return None
    return _classes.get(prompt_id, {}).get(str(node))


def _frames(sock):
    buf = b""

    def need(n):
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk

    while True:
        need(2)
        op, ln, off = buf[0] & 0x0F, buf[1] & 0x7F, 2
        if ln == 126:
            need(4); ln, off = int.from_bytes(buf[2:4], "big"), 4
        elif ln == 127:
            need(10); ln, off = int.from_bytes(buf[2:10], "big"), 10
        need(off + ln)
        payload, buf = buf[off:off + ln], buf[off + ln:]
        yield op, payload


def _pong(sock, payload):
    mask = os.urandom(4)
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.send(bytes([0x8A, 0x80 | len(data)]) + mask + data)


def _handle(m):
    t, d, now = m.get("type"), m.get("data") or {}, time.time()
    if t in ("executing", "progress", "execution_start", "execution_cached", "executed", "progress_state"):
        STATE["last_event"] = now
    if t == "execution_start":
        STATE.update(prompt_id=d.get("prompt_id"), started=now, node=None, cls=None, value=None, max=None)
    elif t == "executing":
        node = d.get("node")
        if node is None:  # промпт закончен
            STATE.update(prompt_id=None, node=None, cls=None, value=None, max=None, node_since=None, started=None)
        elif node != STATE["node"]:
            pid = d.get("prompt_id") or STATE["prompt_id"]
            STATE.update(prompt_id=pid, node=node, cls=_node_class(pid, node), value=None, max=None, node_since=now)
            STATE["started"] = STATE["started"] or now
    elif t == "progress":
        if d.get("node") and d.get("node") != STATE["node"]:
            STATE.update(node=d["node"], cls=_node_class(STATE["prompt_id"], d["node"]), node_since=now)
        STATE.update(value=d.get("value"), max=d.get("max"))
    elif t in ("execution_error", "execution_interrupted"):
        STATE.update(prompt_id=None, node=None, cls=None, value=None, max=None, node_since=None, started=None)


def _run():
    global HOST, PORT
    while True:
        try:
            HOST, PORT = _addr()  # адрес мог поменяться на экране настроек
            sock = socket.create_connection((HOST, PORT), timeout=10)
            key = base64.b64encode(os.urandom(16)).decode()
            sock.send(f"GET /ws?clientId=propgen-watch HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
            head = b""
            while b"\r\n\r\n" not in head:
                head += sock.recv(1)
            sock.settimeout(None)
            STATE["connected"] = True
            # подключились посреди задачи — событий о ней не будет до следующего шага, берём её из /queue
            try:
                with urllib.request.urlopen(f"http://{HOST}:{PORT}/queue", timeout=5) as r:
                    running = json.loads(r.read()).get("queue_running", [])
                if running and not STATE["prompt_id"]:
                    STATE.update(prompt_id=running[0][1], cls="выполняется", node="?", last_event=time.time())
            except Exception:  # noqa: BLE001
                pass
            for op, payload in _frames(sock):
                if op == 1:
                    try:
                        _handle(json.loads(payload))
                    except Exception:  # noqa: BLE001
                        pass
                elif op == 9:
                    _pong(sock, payload)
                elif op == 8:
                    break
        except Exception:  # noqa: BLE001 — Comfy не запущен или перезапускается
            pass
        STATE["connected"] = False
        time.sleep(3)


def start():
    threading.Thread(target=_run, daemon=True, name="comfywatch").start()


def snapshot():
    s = dict(STATE)
    s["now"] = time.time()
    return s
