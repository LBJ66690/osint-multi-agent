"""启动与密钥生命周期回归。全部使用虚构凭据，不请求外部接口。"""
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

import config
import launch
import pipeline
import server

FAKE = 'test-runtime-credential'


class LauncherTests(unittest.TestCase):
    def test_force_prompt_overrides_previous_key_and_retries_blank(self):
        with patch.object(config, 'DASHSCOPE_API_KEY', 'previous'), patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'inherited'}), patch('getpass.getpass', side_effect=['  ', FAKE]) as prompt, patch('builtins.print'):
            config.request_api_key()
            self.assertEqual(config.DASHSCOPE_API_KEY, FAKE)
            self.assertEqual(prompt.call_count, 2)
            self.assertNotIn('DASHSCOPE_API_KEY', os.environ)

    def test_cancel_input_never_starts_service_or_browser(self):
        with patch.object(config, 'DASHSCOPE_API_KEY', ''), patch('getpass.getpass', side_effect=KeyboardInterrupt), patch('webbrowser.open') as browser, patch('server.main') as service:
            with self.assertRaises(SystemExit): launch.main([])
            service.assert_not_called(); browser.assert_not_called()
            self.assertEqual(config.DASHSCOPE_API_KEY, '')

    def test_insecure_echo_fallback_is_rejected(self):
        import getpass, warnings
        def unsafe(*args): warnings.warn('echo would be enabled', getpass.GetPassWarning)
        with patch.object(config, 'DASHSCOPE_API_KEY', ''), patch('getpass.getpass', side_effect=unsafe):
            with self.assertRaises(SystemExit): config.request_api_key()

    def test_check_does_not_prompt_or_open(self):
        with patch.object(config, 'request_api_key') as prompt, patch('webbrowser.open') as browser:
            self.assertEqual(launch.main(['--check']), 0)
            prompt.assert_not_called(); browser.assert_not_called()

    def test_busy_port_does_not_reuse_old_service(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            with patch.object(config, 'PORT', listener.getsockname()[1]), patch.object(config, 'DASHSCOPE_API_KEY', FAKE), patch.object(config, 'request_api_key'), patch('webbrowser.open') as browser, patch('server.main') as service:
                self.assertEqual(launch.main([]), 1)
                service.assert_not_called(); browser.assert_not_called()
                self.assertEqual(config.DASHSCOPE_API_KEY, '')

    def test_browser_waits_for_health(self):
        stop = threading.Event(); response = Mock(status=200)
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        client = Mock(); client.open.side_effect = [OSError('not ready'), response]
        with patch('urllib.request.build_opener', return_value=client), patch('webbrowser.open') as browser:
            launch.open_when_ready('http://127.0.0.1:8878', stop)
            self.assertEqual(client.open.call_count, 2)
            browser.assert_called_once_with('http://127.0.0.1:8878')

    def test_env_file_key_is_ignored_and_environment_key_not_inherited(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'config.py').write_bytes(Path(config.__file__).read_bytes())
            (root/'.env').write_text('DASHSCOPE_API_KEY='+FAKE+'\nMODEL=example-model\n', encoding='utf-8')
            env=dict(os.environ); env.pop('DASHSCOPE_API_KEY', None); env.pop('MODEL', None)
            code="import config,os,json;print(json.dumps([bool(config.DASHSCOPE_API_KEY),config.DEFAULT_MODEL,'DASHSCOPE_API_KEY' in os.environ]))"
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=root,env=env,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout), [False, 'example-model', False])
            env['DASHSCOPE_API_KEY']=FAKE
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=root,env=env,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout), [True, 'example-model', False])

    def test_redacts_logs_exceptions_reports_checkpoints_and_events(self):
        async def run(root):
            await pipeline._awrite(root/'report.md', 'report '+FAKE)
            with patch.object(config, 'CHECKPOINT_DIR', root):
                await pipeline.save_checkpoint('secret_test', {'error':FAKE})
            state=server.RunState(task='test', model_id='qwen3.8-flash')
            emit=await server._emit_for(state)
            with patch('server._persist_events', new=__import__('unittest').mock.AsyncMock()):
                await emit({'type':'error','message':FAKE})
            self.assertNotIn(FAKE, str(state.events))
        with patch.object(config, 'DASHSCOPE_API_KEY', FAKE), tempfile.TemporaryDirectory() as folder:
            stream=io.StringIO(); handler=logging.StreamHandler(stream)
            handler.setFormatter(config.SecretFormatter('%(message)s'))
            log=logging.Logger('secret-test'); log.addHandler(handler)
            try: raise RuntimeError(FAKE)
            except RuntimeError: log.exception('failed: %s', FAKE)
            self.assertNotIn(FAKE, stream.getvalue())
            root=Path(folder); asyncio.run(run(root))
            for path in root.iterdir(): self.assertNotIn(FAKE, path.read_text(encoding='utf-8'))


if __name__ == '__main__': unittest.main()
