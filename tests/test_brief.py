import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from toki_finance_daily.brief import BriefError, make_packet, validate_brief, generate_brief

ROOT = Path(__file__).resolve().parents[1]


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / 'examples/dataset.json').read_text())
        self.brief = json.loads((ROOT / 'examples/brief.json').read_text())

    def test_demo_contract(self):
        self.assertIs(validate_brief(self.brief, self.data), self.brief)

    def test_unknown_citation_rejected(self):
        self.brief['stories'][0]['evidence_ids'] = ['invented']
        with self.assertRaisesRegex(BriefError, 'unknown evidence'):
            validate_brief(self.brief, self.data)

    def test_demo_cannot_be_presented_as_live(self):
        self.brief['mode'] = 'live'
        with self.assertRaisesRegex(BriefError, 'must match dataset'):
            validate_brief(self.brief, self.data)

    def test_calendar_time_cannot_be_invented(self):
        self.brief['calendar'][0]['event_at'] = '2026-10-14T09:30:00+08:00'
        with self.assertRaisesRegex(BriefError, 'event time'):
            validate_brief(self.brief, self.data)

    def test_malformed_refs_have_clear_error(self):
        self.brief['lead']['evidence_ids'] = 'demo-1'
        with self.assertRaisesRegex(BriefError, 'expected array'):
            validate_brief(self.brief, self.data)

    def test_packet_keeps_source_diversity_and_bounds_text(self):
        original = self.data['records'][0]
        self.data['records'] = []
        for source, count in [('domestic', 20), ('overseas', 3)]:
            for i in range(count):
                record = copy.deepcopy(original)
                record.update(id=f'{source}-{i}', source_id=source, summary='x'*5000)
                self.data['records'].append(record)
        packet = make_packet(self.data, limit=6)
        self.assertEqual({r['source_id'] for r in packet['records']}, {'domestic', 'overseas'})
        self.assertEqual(len(packet['records']), 6)
        self.assertTrue(all(len(r['summary']) <= 550 for r in packet['records']))
        self.assertTrue(all(r['observed_at'] == original['observed_at'] for r in packet['records']))
        self.assertEqual(len(self.data['records'][0]['summary']), 5000)

    @patch.dict('os.environ', {}, clear=True)
    def test_missing_api_config_does_not_call_network(self):
        with self.assertRaisesRegex(BriefError, 'LLM_API_KEY'):
            generate_brief(self.data)

    def test_packet_prefers_finance_candidates_after_latest(self):
        template = self.data['records'][0]
        self.data['records'] = [{**template, 'id':str(i), 'title': title, 'published_at':stamp, 'summary':''} for i, (title, stamp) in enumerate([
            ('最新地方资讯', '2026-10-10T23:00:00+08:00'),
            ('天气与交通提醒', '2026-10-10T22:00:00+08:00'),
            ('财政预算和货币政策发布', '2026-10-10T20:00:00+08:00'),
        ])]
        packet = make_packet(self.data, limit=2)
        self.assertEqual([r['id'] for r in packet['records']], ['0','2'])

    def test_packet_includes_digital_asset_regulation_candidates(self):
        template = self.data['records'][0]
        self.data['records'] = [{**template, 'id':str(i), 'title':title, 'published_at':stamp, 'summary':''} for i, (title, stamp) in enumerate([
            ('最新行业活动安排', '2026-10-10T23:00:00+08:00'),
            ('社区见面会现场回顾', '2026-10-10T22:00:00+08:00'),
            ('数字资产监管与稳定币新规', '2026-10-10T21:00:00+08:00'),
        ])]
        self.assertEqual([r['id'] for r in make_packet(self.data, limit=2)['records']], ['0','2'])

    def test_technical_volume_cannot_crowd_official_calendar_macro_or_daily_ratings(self):
        template = self.data['records'][0]
        records = []
        for symbol in range(5):
            for frame in ('5m', '15m', '1h', '1D'):
                records.append({**template, 'id': f'tech-{symbol}-{frame}', 'kind': 'macro', 'source_id': 'technical_page', 'data': {'topic': 'technical', 'subtype': 'summary', 'timeframe': frame, 'rating': '买入', 'symbol': str(symbol)}})
        for subtype, count in (('moving_averages', 5), ('pivots', 5), ('indicators', 5), ('candlesticks', 9)):
            for i in range(count):
                records.append({**template, 'id': f'{subtype}-{i}', 'kind': 'macro', 'source_id': 'technical_page', 'data': {'topic': 'technical', 'subtype': subtype, 'timeframe': '1D' if subtype == 'candlesticks' else None}})
        self.assertEqual(len(records), 44)
        records.append({**template, 'id': 'daily-index', 'kind': 'macro', 'source_id': 'technical_index', 'data': {'topic': 'technical', 'timeframe': '1D', 'rating': '卖出'}})
        for i in range(2):
            records.append({**template, 'id': f'calendar-{i}', 'kind': 'calendar', 'source_id': 'official', 'data': {'event_at': f'2026-10-{14+i}T09:30:00+08:00'}})
        for i in range(8):
            records.append({**template, 'id': f'macro-{i}', 'kind': 'macro', 'source_id': 'official' if i < 6 else 'market_quotes', 'data': {'topic': 'macro' if i < 6 else 'market', 'series_id': f'series-{i}', 'value': i, 'period': '2026-10-09'}})
        self.data['sources'] = [{'id': 'official', 'source_type': 'official'}]
        self.data['records'] = records
        packet = make_packet(self.data)
        selected = {r['id'] for r in packet['records']}
        self.assertEqual(len(selected), 30)
        self.assertTrue({f'calendar-{i}' for i in range(2)} <= selected)
        self.assertTrue({f'macro-{i}' for i in range(8)} <= selected)
        self.assertTrue({f'tech-{i}-1D' for i in range(5)} | {'daily-index'} <= selected)
        self.data['records'] = list(reversed(records))
        self.assertEqual({r['id'] for r in make_packet(self.data)['records']}, selected)

    def test_news_covers_latest_geopolitics_and_distinct_asset_clues(self):
        template = self.data['records'][0]
        rows = [
            ('a-latest', 'a', '最新地方资讯', '23:00'),
            ('b-latest', 'b', '交通天气提醒', '23:01'),
            ('a-finance', 'a', '财政预算货币政策经济消费就业贸易关税', '22:50'),
            ('b-finance', 'b', '金融银行利率投资债券股市资本', '22:49'),
            ('a-war-old', 'a', '伊朗停火谈判进展', '20:00'),
            ('a-war-new', 'a', '中东冲突最新消息', '22:40'),
            ('b-war', 'b', 'Kremlin relayed Iran conflict proposal', '22:39'),
            ('a-gold', 'a', '黄金报价与交易动态', '22:30'),
            ('b-energy', 'b', '原油与天然气供应情况', '22:29'),
            ('a-sports', 'a', 'Edward wins local awards', '22:00'),
        ]
        self.data['records'] = [{**template, 'id': eid, 'source_id': source, 'title': title, 'summary': '', 'published_at': f'2026-10-10T{stamp}:00+08:00'} for eid, source, title, stamp in rows]
        packet = make_packet(self.data, limit=8)
        selected = {r['id'] for r in packet['records']}
        self.assertTrue({'a-latest', 'b-latest', 'a-war-new', 'b-war', 'a-gold', 'b-energy'} <= selected)
        self.assertNotIn('a-war-old', selected)
        index = {r['id']: r for r in packet['candidate_index']}
        self.assertIn('geopolitics', index['a-war-new']['topics'])
        self.assertEqual(index['a-war-new']['assets'], [])
        self.assertEqual(index['a-gold']['assets'], ['gold'])
        self.assertNotIn('geopolitics', index['a-sports']['topics'])

    def test_candidate_index_pages_all_real_ids_with_original_time_precision(self):
        original = copy.deepcopy(self.data)
        template = self.data['records'][0]
        self.data['records'] = [{**template, 'id': f'candidate-{i}', 'title': '黄金' + '长标题' * 80, 'summary': 'z' * 5000, 'published_at': None, 'data': {'published_date': '2026-10-09', 'date_precision': 'day'}} for i in range(7)]
        packet = make_packet(self.data, limit=1, index_limit=3)
        first = packet['candidate_index']
        self.assertEqual([r['id'] for r in first], ['candidate-0', 'candidate-1', 'candidate-2'])
        self.assertEqual(packet['packet_info']['candidate_index']['next_offset'], 3)
        next_page = make_packet(self.data, limit=1, index_offset=3, index_limit=3)
        final_page = make_packet(self.data, limit=1, index_offset=6, index_limit=3)
        self.assertEqual({r['id'] for r in first + next_page['candidate_index'] + final_page['candidate_index']}, {r['id'] for r in self.data['records']})
        self.assertIsNone(final_page['packet_info']['candidate_index']['next_offset'])
        for record in first:
            self.assertLessEqual(len(record['title']), 100)
            self.assertEqual(record['time'], '2026-10-09')
            self.assertEqual(record['time_field'], 'published_date')
            self.assertEqual(record['observed_at'], template['observed_at'])
            self.assertNotIn('summary', record)
            self.assertNotIn('url', record)
        self.assertEqual(len(self.data['records'][0]['summary']), 5000)
        self.assertIsNone(self.data['records'][0]['published_at'])
        # Rendering contracts still use Dataset, not the compact packet/index.
        self.assertIs(validate_brief(self.brief, original), self.brief)


if __name__ == '__main__':
    unittest.main()
