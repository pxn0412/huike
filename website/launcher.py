"""Double-click entry point. Starts a hidden local server, then opens its page."""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 端口跟 server.py 用同一个变量，否则服务起在别的端口、浏览器却打开 8765。
PORT = os.environ.get('HUIKE_PORT', '8765')
URL = f'http://127.0.0.1:{PORT}'


def ready():
    try:
        with urllib.request.urlopen(URL + '/api/status', timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


if __name__ == '__main__':
    if not ready():
        (ROOT / 'data').mkdir(exist_ok=True)
        log = open(ROOT / 'data/server.log', 'ab')
        process = subprocess.Popen([sys.executable, str(ROOT / 'server.py')], cwd=ROOT,
                                   env={**os.environ, 'PYTHONIOENCODING':'utf-8'},
                                   stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log.close()
        for _ in range(40):
            if ready():
                break
            if process.poll() is not None:
                break
            time.sleep(.25)
    if ready():
        webbrowser.open(URL)
        print('网站已启动：' + URL)
    else:
        print('启动未完成，请查看 website/data/server.log。')
        sys.exit(1)
