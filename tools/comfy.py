"""Минимальный клиент API ComfyUI: сборка графа в API-формате, постановка в очередь, ожидание, скачивание результатов.

Граф собирается через Graph: n = g.node("KSampler", seed=1, model=other[0]) — ссылка на выход узла пишется как node[i].
"""
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
import uuid

import config  # noqa: E402 — tools/config.py, адрес ComfyUI из настроек propgen

HOST = (os.environ.get("COMFY_HOST") or config.get("comfy_url")).rstrip("/")


class Ref:
    def __init__(self, node_id, index):
        self.node_id, self.index = node_id, index


class Node:
    def __init__(self, node_id):
        self.id = node_id

    def __getitem__(self, i):
        return Ref(self.id, i)


class Graph:
    def __init__(self):
        self.nodes = {}
        self._n = 0

    def node(self, class_type, **inputs):
        self._n += 1
        nid = str(self._n)
        conv = {}
        for k, v in inputs.items():
            k = k.replace("__", ".")  # sign_mode__qef -> sign_mode.qef (DynamicCombo)
            if isinstance(v, Node):
                v = v[0]
            conv[k] = [v.node_id, v.index] if isinstance(v, Ref) else v
        self.nodes[nid] = {"class_type": class_type, "inputs": conv}
        return Node(nid)

    def to_api(self):
        return self.nodes


def _req(path, data=None):
    url = HOST + path
    if data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {path}: {e.read().decode(errors='replace')[:3000]}") from None


def upload_image(path, name=None):
    """Загружает файл через /upload/image, возвращает имя для LoadImage."""
    name = name or os.path.basename(path)
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(HOST + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read())
    return (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]


def run(graph, timeout=3600, poll=2.0, verbose=True):
    """Ставит граф в очередь и ждёт. Возвращает history-запись (outputs)."""
    client_id = uuid.uuid4().hex
    resp = _req("/prompt", {"prompt": graph.to_api(), "client_id": client_id})
    if resp.get("node_errors"):
        raise RuntimeError(json.dumps(resp["node_errors"], ensure_ascii=False, indent=1))
    pid = resp["prompt_id"]
    t0 = time.time()
    while True:
        h = _req(f"/history/{pid}")
        if h and pid in h:
            entry = h[pid]
            st = entry.get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(json.dumps(msgs, ensure_ascii=False, indent=1)[:4000])
            if st.get("completed") or entry.get("outputs"):
                if verbose:
                    print(f"done in {time.time() - t0:.0f}s", flush=True)
                return entry
        if time.time() - t0 > timeout:
            raise TimeoutError(pid)
        time.sleep(poll)


def collect(entry, dest_dir, prefix=""):
    """Копирует все файлы-результаты (изображения, 3D) в dest_dir. Возвращает список путей."""
    os.makedirs(dest_dir, exist_ok=True)
    out = []
    for node_id, o in entry.get("outputs", {}).items():
        for key in ("images", "3d", "result", "model_file", "files"):
            for f in o.get(key, []) or []:
                if not isinstance(f, dict) or "filename" not in f:
                    continue
                q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                            "type": f.get("type", "output")})
                dst = os.path.join(dest_dir, f"{prefix}{node_id}_{os.path.basename(f['filename'])}")
                with urllib.request.urlopen(f"{HOST}/view?{q}", timeout=300) as r, open(dst, "wb") as w:
                    shutil.copyfileobj(r, w)
                out.append(dst)
    return out
