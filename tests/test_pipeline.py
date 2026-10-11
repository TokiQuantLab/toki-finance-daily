import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

from toki_finance_daily.brief import BriefError, generate_brief, make_packet
from toki_finance_daily.cli import main, render

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.dataset = json.loads((ROOT / 'examples/dataset.json').read_text())
        self.brief = json.loads((ROOT / 'examples/brief.json').read_text())

    @staticmethod
    def response(value):
        return io.BytesIO(json.dumps(value).encode())

    @staticmethod
    def envelope(brief):
        return {'choices':[{'message':{'content':json.dumps(brief)}, 'finish_reason':'stop'}], 'usage':{'total_tokens':123}}

    @patch.dict(os.environ, {'LLM_API_KEY':'local-test-placeholder', 'LLM_MODEL':'local-test', 'LLM_BASE_URL':'http://localhost:12345/v1'})
    def test_invalid_editorial_output_gets_one_targeted_correction(self):
        responses = [self.response(self.envelope({})), self.response(self.envelope(self.brief))]
        with TemporaryDirectory() as tmp, patch('toki_finance_daily.brief.urlopen', side_effect=responses) as request:
            output = generate_brief(self.dataset, tmp)
            self.assertEqual(output, self.brief)
            self.assertEqual(request.call_count, 2)
            self.assertTrue((Path(tmp) / 'model-invalid-1.txt').exists())
            second_payload = json.loads(request.call_args.args[0].data)
            self.assertIn('missing', second_payload['messages'][-1]['content'])

    @patch.dict(os.environ, {'LLM_API_KEY':'local-test-placeholder', 'LLM_MODEL':'local-test', 'LLM_BASE_URL':'http://localhost:12345/v1'})
    def test_malformed_provider_envelopes_fail_cleanly_without_paid_retry(self):
        for value in [None, [], {'choices':[]}, {'choices':[{'message':None}]}]:
            with self.subTest(value=value), TemporaryDirectory() as tmp, patch('toki_finance_daily.brief.urlopen', return_value=self.response(value)) as request:
                with self.assertRaisesRegex(BriefError, '模型服务'):
                    generate_brief(self.dataset, tmp)
                self.assertEqual(request.call_count, 1)

    @patch.dict(os.environ, {'LLM_API_KEY':'local-test-placeholder', 'LLM_MODEL':'local-test', 'LLM_BASE_URL':'http://localhost:12345/v1'})
    def test_transport_failure_has_no_automatic_retry(self):
        with patch('toki_finance_daily.brief.urlopen', side_effect=URLError('offline')) as request:
            with self.assertRaisesRegex(BriefError, '未自动重试'):
                generate_brief(self.dataset)
            self.assertEqual(request.call_count, 1)

    def test_failed_new_run_archives_previous_poster_and_marks_failed(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'poster.png').write_bytes(b'previous-poster')
            (folder / 'dataset.json').write_text(json.dumps({'date':'previous'}))
            (folder / 'manifest.json').write_text(json.dumps({'status':'ready', 'png':str(folder / 'poster.png')}))
            with patch('toki_finance_daily.collect.collect', return_value=self.dataset), patch('toki_finance_daily.cli.generate_brief', side_effect=BriefError('missing API configuration')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(['run','--out',tmp])
            self.assertEqual(code, 2)
            self.assertFalse((folder / 'poster.png').exists())
            self.assertEqual(json.loads((folder / 'manifest.json').read_text())['status'], 'failed')
            archived = list((folder / 'history').glob('*/poster.png'))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_bytes(), b'previous-poster')

    def test_html_only_does_not_leave_previous_png_as_current(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'poster.png').write_bytes(b'previous-poster')
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = render(self.dataset, self.brief, folder, html_only=True)
            self.assertEqual(manifest['status'], 'ready')
            self.assertIsNone(manifest['png'])
            self.assertFalse((folder / 'poster.png').exists())
            self.assertTrue((folder / 'poster.html').exists())
            self.assertTrue(list((folder / 'history').glob('*/poster.png')))

    def test_date_only_candidate_uses_known_publication_day(self):
        template = self.dataset['records'][0]
        self.dataset['records'] = [{**template, 'id':str(i), 'data':{'published_date':day}} for i, day in enumerate(['2026-10-09','2026-10-10'])]
        self.assertEqual(make_packet(self.dataset, limit=1)['records'][0]['id'], '1')

    def test_mobile_scale_is_forwarded_without_changing_layout_width(self):
        with TemporaryDirectory() as tmp, patch('toki_finance_daily.render.render_png', return_value={'ok':True}) as screenshot, contextlib.redirect_stdout(io.StringIO()):
            render(self.dataset, self.brief, tmp, width=540, scale=2)
            self.assertEqual(screenshot.call_args.kwargs, {'width':540, 'scale':2})

    def test_cli_rejects_nonfinite_and_out_of_range_scale(self):
        for scale in ['nan', 'inf', '0.5', '4.1']:
            with self.subTest(scale=scale), TemporaryDirectory() as tmp, patch('toki_finance_daily.render.render_png') as screenshot, contextlib.redirect_stderr(io.StringIO()):
                code = main(['demo', '--out', tmp, '--scale', scale])
                self.assertEqual(code, 2)
                screenshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
