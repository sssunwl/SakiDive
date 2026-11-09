"""No external requests: all API, redirect, webhook and sleeps are mocked."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_pipeline'))
import enrich_content as e


def response(body='海' * 1000):
    return {'candidates': [{'content': {'parts': [{'text': body}]}, 'finishReason': 'STOP',
            'groundingMetadata': {'groundingChunks': [{'web': {'title': '官方', 'uri': 'https://example.org/info'}}],
            'groundingSupports': [{'segment': {'text': body[:2], 'endIndex': 2}, 'groundingChunkIndices': [0]}]}}]}


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.roadmap = self.root / '內容路線圖.md'
        self.roadmap.write_text('- [ ] test-article｜safety｜kerama｜下潛前檢查\n')
        self.net = patch.object(e.urllib.request, 'urlopen', side_effect=AssertionError('Network disabled in tests'))
        self.net.start(); self.addCleanup(self.net.stop)
        self.notify = patch.object(e, 'notify_queue'); self.notify.start(); self.addCleanup(self.notify.stop)
        env = patch.dict(os.environ, {'GITHUB_OUTPUT': '', 'GITHUB_STEP_SUMMARY': '', 'DISCORD_WEBHOOK_SAKI': ''})
        env.start(); self.addCleanup(env.stop)

    def run_mock(self, *bodies, task=None):
        caller = Mock(side_effect=[(response(body), None) for body in bodies])
        path = e.run(task_override=task, db_root=self.root, key='fake', api_caller=caller, resolver=lambda x: x)
        return path, caller

    def test_blacklist(self):
        for term in ('至關重要', '在這個忙碌的時代', '揭開海洋面紗', '不僅舒服更是安全', '開啟你的之旅', '您'):
            self.assertTrue(e.banned_matches(term))
        self.assertFalse(e.banned_matches('你可以先向教練確認。'))
        for term in e.BANNED_PHRASES:
            self.assertIn(term, e.SYSTEM_PROMPT)

    def test_new_old_formats(self):
        selected = e.select_task(self.roadmap)
        self.assertEqual((selected.text, selected.slug, selected.category, selected.spots), ('下潛前檢查', 'test-article', 'safety', ('kerama',)))
        self.roadmap.write_text('- [ ] 🛠 工程\n- [ ] 平壓技巧\n')
        selected = e.select_task(self.roadmap)
        self.assertEqual(selected.text, '平壓技巧')
        self.assertEqual(selected.slug, '')
        with self.assertRaises(ValueError):
            e.parse_task('Wrong Slug｜safety｜｜測試')

    def test_frontmatter_and_completion(self):
        path, caller = self.run_mock('海' * 1000)
        content = path.read_text()
        header = content.split('---')[1]
        data = {k: json.loads(v) for k, v in (line.split(': ', 1) for line in header.strip().splitlines())}
        self.assertEqual(data['words'], 1000)
        self.assertEqual(data['slug'], 'test-article')
        self.assertEqual(data['spots'], ['kerama'])
        self.assertEqual(len(data['summary']), 80)
        self.assertEqual(data['category'], 'safety')
        self.assertIn('[¹](#src-1)', content)
        self.assertIn('## 來源', content)
        self.assertIn('test-article｜safety｜kerama｜下潛前檢查', self.roadmap.read_text())
        self.assertIn('- [x]', self.roadmap.read_text())
        self.assertEqual(caller.call_count, 1)

    def test_task_override_no_roadmap_write(self):
        before = self.roadmap.read_bytes()
        path, _ = self.run_mock('海' * 900, task='manual-test｜life｜｜手動測試')
        self.assertEqual(path.name, 'manual-test.md')
        self.assertEqual(before, self.roadmap.read_bytes())

    def test_old_task_frontmatter(self):
        path, _ = self.run_mock('海' * 800, task='平壓技巧')
        self.assertEqual(path.name, '平壓技巧.md')
        self.assertIn('category: "gear"', path.read_text())

    def test_short_rejected(self):
        with self.assertRaisesRegex(ValueError, '少於 800'):
            self.run_mock('海' * 799, '海' * 700)
        self.assertNotIn('[x]', self.roadmap.read_text())

    def test_rewrite_without_grounding_reuses_first_sources(self):
        bare = {'candidates': [{'content': {'parts': [{'text': '海' * 1200}]}, 'finishReason': 'STOP'}]}
        caller = Mock(side_effect=[(response('海' * 2000), None), (bare, None)])
        path = e.run(db_root=self.root, key='fake', api_caller=caller, resolver=lambda x: x)
        text = path.read_text()
        self.assertIn('https://example.org/info', text)
        self.assertNotIn('#src-', text)

    def test_short_first_attempt_rewritten(self):
        path, caller = self.run_mock('海' * 500, '海' * 1000)
        self.assertEqual(caller.call_count, 2)
        self.assertIn('words: 1000', path.read_text())

    def test_length_boundaries(self):
        self.assertEqual(e.body_length('## 標題\n[海](https://example.org) **水**'), 4)
        for length, calls in ((800, 1), (1400, 1), (1600, 1), (1601, 2)):
            with self.subTest(length=length):
                self.roadmap.write_text(f'- [ ] boundary-{length}｜life｜｜測試\n')
                path, caller = self.run_mock('海' * length, '海' * 1000)
                self.assertEqual(caller.call_count, calls)

    def test_long_second_attempt_allowed(self):
        path, caller = self.run_mock('海' * 1601, '海' * 1700)
        self.assertIn('words: 1700', path.read_text())
        self.assertEqual(caller.call_count, 2)

    def test_banned_rewrite(self):
        path, caller = self.run_mock('海' * 900 + '打造', '海' * 900)
        self.assertNotIn('打造', path.read_text())
        self.assertIn('打造', caller.call_args[0][1]['contents'][-1]['parts'][0]['text'])
        self.assertEqual(caller.call_count, 2)

    def test_banned_twice_no_write(self):
        with self.assertRaisesRegex(ValueError, '重寫後'):
            self.run_mock('海' * 900 + '您', '海' * 900 + '您')
        self.assertFalse(list(self.root.rglob('test-article.md')))
        self.assertNotIn('[x]', self.roadmap.read_text())

    def test_domains_and_subdomains(self):
        urls = ['https://www.facebook.com/x', 'https://old.reddit.com/x', 'https://instagram.com/x', 'https://m.youtube.com/x']
        for url in urls:
            self.assertTrue(e.blocked_source(url))
        self.assertFalse(e.blocked_source('https://notreddit.com/x'))
        candidate = response()['candidates'][0]
        candidate['groundingMetadata']['groundingChunks'] = [{'web': {'uri': u}} for u in urls + ['https://example.org/x']]
        candidate['groundingMetadata']['groundingSupports'] = []
        resolver = Mock(side_effect=lambda u: u)
        sources = e.extract_sources(candidate, resolver)
        self.assertEqual(len(sources), 1)
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(e.extract_sources(candidate, lambda _: 'https://reddit.com/x'), [])

    def test_no_sources_and_api_failure(self):
        data = response(); data['candidates'][0]['groundingMetadata'] = {}
        for result in ((data, None), (None, 'mock failure')):
            with self.assertRaises((ValueError, RuntimeError)):
                e.run(db_root=self.root, key='fake', api_caller=lambda *_: result, resolver=lambda u: u)

    def test_model_fallback_payload_unchanged(self):
        calls = []
        def fake(req, timeout):
            calls.append((req.full_url, req.data))
            if len(calls) <= 8:
                raise urllib.error.HTTPError(req.full_url, 503, 'busy', {}, io.BytesIO(b'busy'))
            result = Mock(); result.read.return_value = json.dumps(response()).encode()
            result.__enter__ = Mock(return_value=result); result.__exit__ = Mock(return_value=False)
            return result
        with patch.object(e.urllib.request, 'urlopen', side_effect=fake), patch.object(e.time, 'sleep'):
            result, error = e.call_gemini('fake', e.build_request('測試', '2026-11-09'))
        self.assertIsNone(error)
        self.assertIn('gemini-flash-latest', calls[4][0])
        self.assertIn('gemini-flash-lite-latest', calls[-1][0])
        self.assertEqual(len({body for _, body in calls}), 1)

    def test_daily_quota_skips_to_next_model_without_retry(self):
        calls = []
        def fake(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, 'quota', {}, io.BytesIO(b'{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}'))
            result = Mock(); result.read.return_value = json.dumps(response()).encode()
            result.__enter__ = Mock(return_value=result); result.__exit__ = Mock(return_value=False)
            return result
        with patch.object(e.urllib.request, 'urlopen', side_effect=fake), patch.object(e.time, 'sleep') as sleep:
            result, error = e.call_gemini('fake', e.build_request('測試', '2026-11-09'))
        self.assertIsNone(error)
        self.assertEqual(len(calls), 2)
        self.assertIn('gemini-flash-latest', calls[1])
        sleep.assert_not_called()

    def test_empty_queue_summary(self):
        self.notify.stop()
        self.roadmap.write_text('- [ ] 🛠 工程\n')
        summary = self.root / 'summary'
        with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}):
            self.assertIsNone(e.run(db_root=self.root))
        self.assertIn('⚠️ 路線圖已空,請補題', summary.read_text())

    def test_low_queue(self):
        with patch.object(e, 'notify_queue') as notify:
            self.run_mock('海' * 900)
        notify.assert_called_once()
        self.assertIn('剩 1 題', notify.call_args[0][0])

    def test_cli_task_end_to_end_mock(self):
        from functools import partial
        runner = partial(e.run, db_root=self.root, key='fake',
                         api_caller=lambda *_: (response('海' * 1100), None), resolver=lambda u: u)
        before = self.roadmap.read_bytes()
        with patch.object(e, 'run', side_effect=runner):
            self.assertEqual(e.main(['--task', 'cli-preview｜beginner｜｜新手測試']), 0)
        path = self.root / '01_潛水點/cli-preview.md'
        self.assertTrue(path.exists())
        self.assertIn('words: 1100', path.read_text())
        self.assertEqual(before, self.roadmap.read_bytes())

    def test_cli_failure_nonzero(self):
        with patch.object(e, 'run', side_effect=ValueError('mock rejected')):
            self.assertEqual(e.main(['--task', '測試']), 1)


if __name__ == '__main__':
    unittest.main()
