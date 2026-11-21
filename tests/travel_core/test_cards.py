import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import travel_cards as cards


class CardReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog = {"east": {"name": "东门"}, "west": {"name": "西门"}}
        self.state = SimpleNamespace(travel={"report_format": "cards", "trip": {"budget_fen": 250000, "travelers": 2}},
                                     summary={}, model_id="fixture")
        self.plan = {"booking_entries": [{"poi_id": "east"}], "legs": [
            {"origin": "铜仁站", "poi_id": gate, "distance_m": 60000} for gate in ["east", "west"]
        ], "budget": {"lines": [
            {"label": "住宿", "unit": "room_night", "source_url": "fixture", "lower_fen": 20000,
             "upper_fen": 20000, "quantity": 2},
            {"label": "餐食", "lower_fen": 0, "upper_fen": None, "quantity": 1}
        ]}, "unresolved": ["开放时间待确认"]}
        self.facts = cards.facts_for(self.state.travel["trip"], self.plan, self.catalog)
        self.generate = AsyncMock(return_value=json.dumps(self.facts))
        self.checkpoints = []
        async def checkpoint(status):
            self.checkpoints.append(copy.deepcopy(self.state.summary))
        self.checkpoint = checkpoint

    async def finalize(self):
        await cards.finalize(self.state, self.plan, self.catalog, self.checkpoint, self.generate)

    async def test_once_and_saved_before_call(self):
        await self.finalize()
        await self.finalize()
        self.assertEqual(self.generate.await_count, 1)
        self.assertEqual(self.checkpoints[0]["card_generation"]["status"], "attempted")
        self.assertEqual(self.checkpoints[-1]["card_report"], self.facts)

    async def test_no_second_call_after_interruption(self):
        self.state.summary["card_generation"] = {"status":"attempted","calls":1}
        await self.finalize()
        self.generate.assert_not_awaited()
        self.assertNotIn("card_report", self.state.summary)

    async def test_timeout_and_text(self):
        self.generate.side_effect = TimeoutError
        await self.finalize()
        await self.finalize()
        self.assertEqual(1, self.generate.await_count)
        self.assertEqual(self.state.summary["card_generation"]["status"], "text")
        self.assertNotIn("card_report", self.state.summary)
        self.state.summary.clear()
        self.state.travel["report_format"] = "text"
        await self.finalize()
        self.assertEqual(1, self.generate.await_count)

    async def test_unavailable_model(self):
        with patch.object(cards.config, "DASHSCOPE_API_KEY", ""):
            await cards.finalize(self.state, self.plan, self.catalog, self.checkpoint)
        self.assertEqual(0, self.state.summary["card_generation"]["calls"])

    def test_invalid_routes_amounts_and_shape(self):
        examples = []
        for field, value in [("choices", self.facts["choices"][:1]), ("invented", {}),
                             ("budget", [["全团预算上限（全程）", 2501]]), ("scenes", [])]:
            examples.append(dict(self.facts, **{field:value}))
        wrong = copy.deepcopy(self.facts)
        wrong["scenes"][0]["nodes"].reverse()
        examples.append(wrong)
        wrong = copy.deepcopy(self.facts)
        wrong["scenes"][0]["tip"] = "天天营业，门票免费"
        examples.append(wrong)
        for raw in [json.dumps(obj) for obj in examples] + ["[]", "x", "```json\n{}\n```"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cards.validate(raw, self.facts)

    def test_no_single_choice_and_correct_amounts(self):
        self.plan["legs"].pop()
        result = cards.facts_for(self.state.travel["trip"], self.plan, self.catalog)
        self.assertEqual(result["choices"], [])
        self.assertEqual([row[1] for row in result["budget"]], [2500, 200, 400, None])
        self.assertEqual(cards.validate(json.dumps(result), result), result)

