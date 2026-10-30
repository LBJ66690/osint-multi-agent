"""复现 Schema 回显及 Windows 10054 关闭回调；不调用外部模型。"""
import asyncio
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agents
import server
from schemas import Knowledge, VerificationReport
from asyncio_compat import install_cleanup_handler


class JsonTests(unittest.IsolatedAsyncioTestCase):
    def test_template_matches_real_entity_shape_without_wrappers(self):
        contract=agents._output_contract(Knowledge)
        template, _=json.JSONDecoder().raw_decode(contract)
        self.assertEqual(set(template['entities'][0]), {'entity','canonical','type'})
        self.assertIsInstance(template['entities'][0]['entity'],str)
        self.assertNotIn('填写',template['entities'][0])

    async def test_nested_entity_reply_can_be_fixed_with_concrete_error_context(self):
        bad='{"entities":[{"entity":{"entity":"贵州","canonical":"贵州","type":"地点"}}]}'
        good='{"entities":[{"entity":"贵州","canonical":"贵州","type":"地点"}]}'
        value,_,_=await agents.run_json(self.agent([bad,good]),'仅据提供资料分析',Knowledge)
        self.assertEqual(value.entities[0].canonical,'贵州')
        self.assertIn('entities.0.entity',self.prompts[1]);self.assertIn(bad,self.prompts[1])

    async def test_invalid_comma_reply_is_regenerated_with_syntax_feedback(self):
        bad='{"entities":[],"aliases":{},"viewpoints":[{"topic":"景区" "summary":"有来源"}]}'
        good='{"entities":[],"aliases":{},"viewpoints":[{"topic":"景区","sentiment":"中性","summary":"有来源"}]}'
        value,_,usage=await agents.run_json(self.agent([bad,good]),'分析',Knowledge)
        self.assertEqual(value.viewpoints[0].summary,'有来源');self.assertEqual(usage['total'],6)
        self.assertIn('JSON 语法错误',self.prompts[1]);self.assertIn('逗号',self.prompts[1])
        self.assertIn(bad,self.prompts[1])

    async def test_two_truncated_replies_fail_visibly_without_partial_result(self):
        bad='{"entities":[],"viewpoints":[{"topic":"贵州"'
        with self.assertRaisesRegex(agents.StructuredOutputError,'已保留之前成功的阶段') as caught:
            await agents.run_json(self.agent([bad,bad]),'分析',Knowledge)
        self.assertIsInstance(caught.exception.__cause__,json.JSONDecodeError)
        self.assertEqual(len(self.prompts),2)

    async def test_placeholder_copy_is_not_accepted_as_real_knowledge(self):
        bad='{"aliases":{},"entities":[{"entity":"<填写:$.entities[].entity>","canonical":"贵州","type":"地点"}]}'
        good='{"aliases":{},"entities":[],"viewpoints":[]}'
        value,_,_=await agents.run_json(self.agent([bad,good]),'分析',Knowledge)
        self.assertEqual(value.entities,[]);self.assertEqual(len(self.prompts),2)
        self.assertIn('占位符',self.prompts[1])

    async def test_long_invalid_reply_context_is_bounded(self):
        bad='{"entities":['+'x'*15000+'}'
        await agents.run_json(self.agent([bad,'{"entities":[]}']),'分析',Knowledge)
        self.assertIn('[中段省略]',self.prompts[1])
        self.assertLess(len(self.prompts[1])-len(self.prompts[0]),9500)

    def agent(self, replies):
        values=iter(replies); self.prompts=[]
        async def run(prompt, **kwargs):
            self.prompts.append(prompt)
            return SimpleNamespace(text=next(values), usage_details={'total_token_count': 3})
        return SimpleNamespace(name='核查官', run=run)

    async def test_schema_echo_retries_to_actual_verdict(self):
        echo=json.dumps(VerificationReport.model_json_schema(), ensure_ascii=False)
        good=json.dumps({'verdict':'fail','summary':'证据不足','issues':[], 'redo_targets':[]},ensure_ascii=False)
        value, _, usage=await agents.run_json(self.agent([echo,good]), '核查实际证据', VerificationReport)
        self.assertEqual(value.verdict,'fail');self.assertEqual(usage['total'],6)
        self.assertNotIn('"$defs"',self.prompts[0]);self.assertNotIn('"properties"',self.prompts[0])
        self.assertIn('返回了 JSON Schema', self.prompts[1])
        self.assertIn('核查实际证据', self.prompts[1])

    async def test_two_schema_echoes_fail_without_fabricating_pass(self):
        echo=json.dumps(VerificationReport.model_json_schema())
        with self.assertRaisesRegex(ValueError, 'JSON Schema'):
            await agents.run_json(self.agent([echo,echo]),'核查',VerificationReport)
        self.assertEqual(len(self.prompts),2)

    async def test_optional_fields_do_not_allow_schema_as_empty_knowledge(self):
        echo=json.dumps(Knowledge.model_json_schema())
        good='{"entities":[],"aliases":{},"viewpoints":[]}'
        value,_,_=await agents.run_json(self.agent([echo,good]),'分析',Knowledge)
        self.assertEqual(len(self.prompts),2);self.assertEqual(value.entities,[])

    async def test_normal_business_json_needs_one_attempt(self):
        good='{"verdict":"pass","summary":"有证据支撑"}'
        value,_,_=await agents.run_json(self.agent([good]),'核查',VerificationReport)
        self.assertEqual(value.verdict,'pass');self.assertEqual(len(self.prompts),1)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_proactor_callback_finishes_cleanup_after_reset(self):
        from asyncio.proactor_events import _ProactorBasePipeTransport
        # 使用标准库真实回调和模拟 socket 复现 shutdown 抛出 10054。
        transport=object.__new__(_ProactorBasePipeTransport)
        error=ConnectionResetError('connection reset');error.winerror=10054
        sock=Mock();sock.shutdown.side_effect=error
        transport._sock=sock;transport._server=Mock();owner=transport._server
        transport._protocol=Mock();transport._closing=True;transport._called_connection_lost=False
        loop=asyncio.get_running_loop();previous=Mock();loop.set_exception_handler(previous)
        restore=install_cleanup_handler(loop)
        try:
            with patch('asyncio_compat.sys.platform','win32'):
                loop.call_soon(transport._call_connection_lost,None)
                await asyncio.sleep(0)
            previous.assert_not_called();sock.close.assert_called_once();owner._detach.assert_called_once()
            self.assertIsNone(transport._sock);self.assertTrue(transport._called_connection_lost)
            transport._protocol.connection_lost.assert_called_once_with(None)
        finally:restore();loop.set_exception_handler(None)

    async def test_request_reset_and_other_errors_still_reach_existing_handler(self):
        loop=asyncio.get_running_loop();previous=Mock();loop.set_exception_handler(previous)
        restore=install_cleanup_handler(loop)
        try:
            reset=ConnectionResetError('request failed');reset.winerror=10054
            for error in (reset,RuntimeError('unrelated failure')):
                context={'exception':error,'message':'real request failed'}
                loop.call_exception_handler(context)
            self.assertEqual(previous.call_count,2)
        finally:restore();self.assertIs(loop.get_exception_handler(),previous);loop.set_exception_handler(None)

    async def test_fastapi_lifespan_installs_and_restores_handler(self):
        loop=asyncio.get_running_loop();previous=loop.get_exception_handler()
        async with server.lifespan(server.app):
            self.assertIsNot(loop.get_exception_handler(),previous)
        self.assertIs(loop.get_exception_handler(),previous)


if __name__=='__main__':unittest.main()
