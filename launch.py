"""交互启动入口：每次输入密钥，服务就绪后才打开前端。"""
from __future__ import annotations

import socket
import sys
import threading
import urllib.request
import webbrowser


def open_when_ready(url, stop):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while not stop.wait(.5):
        try:
            with client.open(url + '/api/health', timeout=2) as response:
                if response.status == 200:
                    if not stop.is_set():
                        webbrowser.open(url)
                    return
        except (OSError, ValueError):
            pass


def main(argv=None):
    import config
    args = sys.argv[1:] if argv is None else argv
    if args == ['--check']:
        import fastapi, uvicorn, mcp
        from agent_framework.openai import OpenAIChatClient
        from openai import AsyncAzureOpenAI
        print(f'Dependencies OK. URL: http://{config.HOST}:{config.PORT}')
        return 0
    if args:
        print('Usage: python launch.py [--check]')
        return 2
    config.request_api_key()
    try:
        with socket.socket() as probe:
            probe.bind((config.HOST, config.PORT))
    except OSError:
        config.DASHSCOPE_API_KEY = ''
        print(f'Port {config.PORT} is unavailable. Close the old service window and retry.')
        return 1
    stop = threading.Event()
    url = f'http://{config.HOST}:{config.PORT}'
    try:
        import server
        print(f'Starting service: {url}\nClose this window or press Ctrl+C to stop.')
        threading.Thread(target=open_when_ready, args=(url, stop), daemon=True).start()
        server.main()
        return 0
    finally:
        stop.set()
        config.DASHSCOPE_API_KEY = ''


if __name__ == '__main__':
    raise SystemExit(main())
