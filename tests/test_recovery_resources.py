"""恢复及成本观测回归；固定内存任务，不使用网络或真实凭据。"""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from pydantic import BaseModel

from checkpoint_resume import resume_info
from execution_runtime import ExecutionTimeout, Operations, WorkBudget, signature
from resource_usage import consume_event, snapshot


class Payload(BaseModel):
    value: int


class OperationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'checkpoint.json'
        self.data = {'task': 'fixture', 'run_id': 'fixture'}
        self.events = []
        async def save():
            self.path.write_text(json.dumps(self.data), encoding='utf-8')
        async def emit(event):
            self.events.append(event)
        self.save, self.emit = save, emit
        self.ops = Operations(self.data, save, emit, WorkBudget(5))

    async def test_success_is_restored_after_new_operations_instance(self):
        count = 0
        async def work():
            nonlocal count
            count += 1
            return Payload(value=7)
        await self.ops.get('x', ['input'], work, 1, 'analyzing', Payload)
        self.data = json.loads(self.path.read_text(encoding='utf-8'))
        restarted = Operations(self.data, self.save, self.emit, WorkBudget(5))
        value = await restarted.get('x', ['input'], work, 1, 'analyzing', Payload)
        self.assertEqual(value.value, 7)
        self.assertEqual(count, 1)
        self.assertEqual(self.events[-1]['type'], 'operation_restored')

    async def test_corrupt_cache_recomputes_only_corrupt_operation(self):
        self.data['operations'].update({'x': {'signature': signature(['x']), 'value': {'wrong': 1}},
                                        'good': {'signature': 'saved', 'value': {'value': 4}}})
        old_good = copy.deepcopy(self.data['operations']['good'])
        async def work():
            return Payload(value=2)
        value = await self.ops.get('x', ['x'], work, 1, 'analyzing', Payload)
        self.assertEqual(value.value, 2)
        self.assertEqual(old_good, self.data['operations']['good'])
        self.assertTrue(any(e['type'] == 'operation_cache_invalid' for e in self.events))
        self.assertFalse(any(e['type'] == 'operation_restored' for e in self.events))

    async def test_input_change_failure_preserves_previous_success_but_never_reuses_it(self):
        async def good():
            return Payload(value=1)
        await self.ops.get('x', {'model': 'a'}, good, 1, 'planning', Payload)
        previous = copy.deepcopy(self.data['operations']['x'])
        async def fail():
            raise ConnectionError('fixture network down')
        with self.assertRaises(ConnectionError):
            await self.ops.get('x', {'model': 'b'}, fail, 1, 'planning', Payload)
        self.assertEqual(previous, self.data['operations']['x'])
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['operation_attempts']['x']['status'], 'error')
        self.assertEqual(saved['last_failed_operation'], 'x')
        self.assertEqual(self.events[-1]['type'], 'operation_error')
        self.assertEqual(self.events[-1]['operation_attempt'], 2)
        value = await self.ops.get('x', {'model': 'b'}, good, 1, 'planning', Payload)
        self.assertEqual(value.value, 1)
        self.assertEqual(self.data['operations']['x']['signature'], signature({'model': 'b'}))

    async def test_invalid_live_result_never_becomes_success_cache(self):
        async def invalid():
            return {'wrong': 'JSON shape'}
        with self.assertRaises(ValueError):
            await self.ops.get('x', [], invalid, 1, 'analyzing', Payload)
        self.assertNotIn('x', self.data['operations'])
        self.assertEqual(self.data['operation_attempts']['x']['failures'], 1)

    async def test_cancel_is_saved_and_does_not_become_success(self):
        entered = asyncio.Event()
        async def work():
            entered.set()
            await asyncio.sleep(10)
        task = asyncio.create_task(self.ops.get('x', [], work, 4, 'reasoning'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertNotIn('x', saved['operations'])
        self.assertEqual(saved['operation_attempts']['x']['status'], 'cancelled')
        self.assertEqual(self.events[-1]['type'], 'operation_cancelled')

    async def test_concurrent_identical_request_runs_factory_once(self):
        count = 0
        async def work():
            nonlocal count
            count += 1
            await asyncio.sleep(.02)
            return {'value': 3}
        values = await asyncio.gather(*(self.ops.get('same', [], work, 1, 'analyzing', Payload) for _ in range(3)))
        self.assertEqual(count, 1)
        self.assertEqual([v.value for v in values], [3, 3, 3])

    async def test_tool_timeout_is_not_misreported_as_work_budget_timeout(self):
        async def tool():
            raise TimeoutError('MCP connect timed out')
        with self.assertRaises(TimeoutError) as caught:
            await self.ops.get('search:x', [], tool, 2, 'collecting')
        self.assertNotIsInstance(caught.exception, ExecutionTimeout)
        self.assertEqual(str(caught.exception), 'MCP connect timed out')

    async def test_work_deadline_cancels_pending_call_and_records_error(self):
        cancelled = []
        async def work():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        with self.assertRaises(ExecutionTimeout):
            await self.ops.get('search:x', [], work, .02, 'collecting')
        self.assertEqual(cancelled, [True])
        self.assertNotIn('search:x', self.data['operations'])
        self.assertEqual(self.data['operation_attempts']['search:x']['status'], 'error')

    async def test_exhausted_budget_never_invokes_factory(self):
        calls = []
        async def work():
            calls.append(True)
        ops = Operations(self.data, self.save, self.emit, WorkBudget(0))
        with self.assertRaises(ExecutionTimeout):
            await ops.get('x', [], work, 10, 'planning')
        self.assertEqual(calls, [])


class CheckpointValidationTests(unittest.TestCase):
    def test_legacy_checkpoint_without_new_metadata_stays_valid(self):
        self.assertEqual(resume_info({'task': 'old', 'run_id': 'old'}, 'old')['next_stage'], 'planning')

    def test_noncritical_bad_operation_remains_locally_recoverable(self):
        data = {'task': 'old', 'operations': {'x': {'signature': 'x', 'value': {'wrong': 1}}}}
        self.assertEqual(resume_info(data)['next_stage'], 'planning')

    def test_invalid_control_containers_fail_safely(self):
        for key, value in [('operations', []), ('stage_state', []), ('decisions', []),
                           ('role_models', ['bad']), ('active_operation', {}), ('report_state', [])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                resume_info({'task': 'old', key: value})

    def test_wrong_run_and_missing_completed_output_still_rejected(self):
        with self.assertRaises(ValueError):
            resume_info({'task': 'old', 'run_id': 'a'}, 'b')
        with self.assertRaises(ValueError):
            resume_info({'task': 'old', 'completed_stages': ['planning']})


class ResourceUsageTests(unittest.TestCase):
    def request(self, stats, rid='a', **kwargs):
        consume_event(stats, {'type': 'model_request', 'request_id': rid, 'role': 'analyst',
                              'operation': 'analyzing:produce:0', 'attempt': 1, **kwargs})

    def response(self, stats, rid='a', usage=None):
        consume_event(stats, {'type': 'model_response', 'request_id': rid, 'usage': usage})

    def test_unknown_tokens_remain_none_not_zero(self):
        stats = {}
        self.request(stats)
        self.response(stats)
        result = snapshot(stats)
        self.assertEqual(result['model_calls'], 1)
        self.assertIsNone(result['total'])
        self.assertIsNone(result['prompt'])
        self.assertEqual(result['unknown_usage_calls'], 1)
        self.assertIsNone(result['price'])

    def test_explicit_zero_is_valid_known_usage(self):
        stats = {}
        self.request(stats)
        self.response(stats, usage={'prompt': 0, 'completion': 0, 'total': 0})
        self.assertEqual(snapshot(stats)['total'], 0)
        self.assertTrue(snapshot(stats)['usage_available'])

    def test_known_partial_sum_is_not_misrepresented_as_total(self):
        stats = {}
        self.request(stats)
        self.response(stats, usage={'prompt': 10, 'completion': 4, 'total': 14})
        self.request(stats, 'b')
        self.response(stats, 'b')
        result = snapshot(stats)
        self.assertEqual(result['known_total'], 14)
        self.assertIsNone(result['total'])
        self.assertEqual(result['model_calls'], 2)

    def test_request_replay_and_aggregate_compatibility_event_do_not_double_count(self):
        stats = {}
        for _ in range(2):
            self.request(stats)
            self.response(stats, usage={'prompt': 10, 'completion': 4, 'total': 14})
        consume_event(stats, {'type': 'usage', 'total': 14})
        self.assertEqual(snapshot(stats)['model_calls'], 1)
        self.assertEqual(snapshot(stats)['total'], 14)

    def test_schema_failures_still_count_response_usage_and_retry(self):
        stats = {}
        self.request(stats)
        self.response(stats, usage={'prompt': 10, 'completion': 4, 'total': 14})
        consume_event(stats, {'type': 'model_parse_error', 'request_id': 'a'})
        self.request(stats, 'b', attempt=2)
        self.response(stats, 'b', {'prompt': 12, 'completion': 5, 'total': 17})
        result = snapshot(stats)
        self.assertEqual(result['parse_failures'], 1)
        self.assertEqual(result['model_retries'], 1)
        self.assertEqual(result['total'], 31)
        self.assertEqual(result['roles']['analyst']['model_calls'], 2)

    def test_request_failure_cancellation_and_parse_error_are_separate(self):
        stats = {}
        self.request(stats)
        consume_event(stats, {'type': 'model_error', 'request_id': 'a', 'cancelled': False})
        self.request(stats, 'b')
        consume_event(stats, {'type': 'model_error', 'request_id': 'b', 'cancelled': True})
        result = snapshot(stats)
        self.assertEqual(result['model_failures'], 1)
        self.assertEqual(result['model_cancellations'], 1)
        self.assertEqual(result['parse_failures'], 0)

    def test_logical_operations_are_not_counted_as_model_calls(self):
        stats = {}
        consume_event(stats, {'type': 'operation_start', 'operation': 'x', 'operation_attempt': 1})
        consume_event(stats, {'type': 'operation_error', 'operation': 'x', 'operation_attempt': 1})
        consume_event(stats, {'type': 'operation_start', 'operation': 'x', 'operation_attempt': 2})
        consume_event(stats, {'type': 'operation_restored', 'operation': 'old'})
        self.assertEqual(snapshot(stats)['model_calls'], 0)
        self.assertEqual(snapshot(stats)['operation_failures'], 1)
        self.assertEqual(snapshot(stats)['operation_retries'], 1)
        self.assertEqual(snapshot(stats)['restored_operations'], 1)

    def test_search_events_count_actual_requests_and_real_supplement(self):
        stats = {}
        start = {'type': 'tool_call', 'stage': 'collecting', 'tool': 'parallel', 'call_id': 'a'}
        consume_event(stats, start)
        consume_event(stats, start)
        consume_event(stats, {**start, 'type': 'tool_result'})
        consume_event(stats, {**start, 'call_id': 'b', 'supplemental': True, 'retry': True})
        consume_event(stats, {**start, 'call_id': 'b', 'type': 'tool_error'})
        consume_event(stats, {'type': 'tool_error', 'stage': 'collecting', 'tool': 'WebSearch', 'error': 'quota limit'})
        consume_event(stats, {'type': 'tool_call', 'stage': 'reporting', 'tool': 'local_template', 'call_id': 'c'})
        result = snapshot(stats)
        self.assertEqual(result['search_calls'], 2)
        self.assertEqual(result['search_failures'], 1)
        self.assertEqual(result['supplemental_searches'], 1)
        self.assertEqual(result['search_retries'], 1)

    def test_stats_roundtrip_and_stop_reasons_stay_idempotent(self):
        stats = {}
        self.request(stats)
        self.response(stats, usage={'prompt': 3, 'completion': 2, 'total': 5})
        for _ in range(2):
            consume_event(stats, {'type': 'decision', 'action': 'STOP_SEARCH', 'reason': '工作预算不足'})
        restored = json.loads(json.dumps(stats))
        self.request(restored)
        self.assertEqual(snapshot(restored)['stop_reasons'], ['工作预算不足'])
        self.assertEqual(snapshot(restored)['total'], 5)

    def test_old_run_newly_recorded_usage_never_appears_as_full_run_total(self):
        stats = {'history_incomplete': True}
        self.request(stats)
        self.response(stats, usage={'prompt': 10, 'completion': 4, 'total': 14})
        result = snapshot(stats)
        self.assertTrue(result['history_incomplete'])
        self.assertEqual(result['scope'], 'recorded_requests')
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(result['known_total'], 14)
        self.assertEqual(result['known_prompt'], 10)
        self.assertEqual(result['known_completion'], 4)
        for key in ('prompt', 'completion', 'total'):
            self.assertIsNone(result[key])
            self.assertIsNone(result['roles']['analyst'][key])
        self.assertFalse(result['usage_available'])

    def test_incomplete_history_marker_survives_checkpoint_roundtrip_and_replay(self):
        stats = {'history_incomplete': True}
        self.request(stats)
        self.response(stats, usage={'prompt': 3, 'completion': 2, 'total': 5})
        restored = json.loads(json.dumps(stats))
        self.request(restored)
        self.response(restored, usage={'prompt': 3, 'completion': 2, 'total': 5})
        result = snapshot(restored)
        self.assertEqual(result['known_total'], 5)
        self.assertEqual(result['model_calls'], 1)
        self.assertIsNone(result['total'])
        self.assertTrue(result['history_incomplete'])


if __name__ == '__main__':
    unittest.main()
