"""Перезапуск сервера после обновления, когда его запустили не через start.cmd: дождаться, пока старый процесс
освободит порт, и запустить server.py заново отдельным процессом (вывод — в work/server.log).

python tools/restart.py <port> <python> <server.py>
"""
import os
import socket
import subprocess
import sys
import time

port, py, server = int(sys.argv[1]), sys.argv[2], sys.argv[3]
root = os.path.dirname(os.path.abspath(server))
for _ in range(120):  # до 30 с
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            break
        except OSError:
            time.sleep(0.25)
os.makedirs(os.path.join(root, "work"), exist_ok=True)
log = open(os.path.join(root, "work", "server.log"), "a", encoding="utf-8")
flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
         | getattr(subprocess, "CREATE_NO_WINDOW", 0))
subprocess.Popen([py, server], cwd=root, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True,
                 env=dict(os.environ, PYTHONIOENCODING="utf-8"))
