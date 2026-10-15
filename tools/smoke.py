"""Проверка установки целиком: проп из examples/kettle.png → чистый фронт → 3D → лоу-поли через API сервера.
Сервер (start.cmd) и ComfyUI должны быть запущены. Печатает время шагов; проп остаётся в списке — его можно удалить.

python tools/smoke.py [--image examples/kettle.png] [--notes "..."]
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

BASE = f"http://127.0.0.1:{config.get('port')}"


def req(path, data=None, method=None, ctype="application/json"):
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": ctype} if data else {})
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=os.path.join(config.ROOT, "examples", "kettle.png"))
    ap.add_argument("--notes", default="старый жёлтый эмалированный чайник с розами на белой электроплитке")
    a = ap.parse_args()
    bd = uuid.uuid4().hex
    body = b"".join([
        f'--{bd}\r\nContent-Disposition: form-data; name="name"\r\n\r\nпроверка установки\r\n'.encode(),
        f'--{bd}\r\nContent-Disposition: form-data; name="desc"\r\n\r\n{a.notes}\r\n'.encode(),
        f'--{bd}\r\nContent-Disposition: form-data; name="files"; filename="{os.path.basename(a.image)}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode() + open(a.image, "rb").read() + b"\r\n",
        f"--{bd}--\r\n".encode()])
    p = req("/api/props", body, "POST", f"multipart/form-data; boundary={bd}")
    pid = p["id"]
    print("проп", pid)
    req(f"/api/props/{pid}/crop", json.dumps({"photo": 0, "full": True}).encode(), "POST")  # пример уже обрезан
    req(f"/api/props/{pid}/run", json.dumps({"step": "all"}).encode(), "POST")
    t0, seen = time.time(), {}
    while True:
        m = req(f"/api/props/{pid}")
        for s, st in m["steps"].items():
            if st.get("status") in ("done", "error") and seen.get(s) != st["status"]:
                seen[s] = st["status"]
                dt = (st.get("finished") or 0) - (st.get("started") or 0)
                print(f"  {s:9} {st['status']:6} {dt:6.0f} с" + (f"  {st.get('error', '')[-300:]}" if st["status"] == "error" else ""))
        if any(st.get("status") == "error" for st in m["steps"].values()):
            sys.exit(1)
        if m["steps"].get("lowpoly", {}).get("status") == "done":
            break
        if time.time() - t0 > 3600:
            sys.exit("дольше часа — что-то не так (см. логи шага в интерфейсе)")
        time.sleep(5)
    print(f"готово за {time.time() - t0:.0f} с: {m['files'].get('lo')}  промпт: {m.get('desc')}")


if __name__ == "__main__":
    main()
