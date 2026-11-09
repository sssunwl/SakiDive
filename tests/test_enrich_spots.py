"""All network access disabled; APIs, grounding redirects and webhooks mocked."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_pipeline'))
import enrich_spots as e


def data():
    return dict(lat=26.2, lng=127.3, months=[4, 5], levelMin='beginner',
                entry=['boat'], tags=['turtle'], see='海龜與珊瑚', note='留意海況',
                monthlyNote='依當地觀察', monthly={str(n): [] for n in range(1, 13)},
                sources=[dict(url='https://example.org/a'), dict(url='https://example.org/b')])


def response(raw=None, text=None):
    return {'candidates': [{'content': {'parts': [{'text': text if text is not None else json.dumps(raw if raw is not None else data())}]},
            'finishReason': 'STOP', 'groundingMetadata': {'groundingChunks': [
                {'web': {'uri': 'https://example.org/a', 'title': '來源一'}},
                {'web': {'uri': 'https://example.org/b', 'title': '來源二'}}]}}]}


class SpotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); (self.root / 'data').mkdir()
        self.queue = self.root / 'data/spot_queue.md'
        self.queue.write_text('# 佇列\n- [ ] first｜第一點｜japan｜kerama\n- [ ] second｜第二點｜japan｜izu\n- [ ] third｜第三點｜japan｜izu\n')
        self.creatures = {'turtle': {'id': 'turtle', 'name': '海龜'}}
        net = patch.object(e.urllib.request, 'urlopen', side_effect=AssertionError('Network disabled'))
        net.start(); self.addCleanup(net.stop)
        notify = patch.object(e, 'notify_queue'); self.notify = notify.start(); self.addCleanup(notify.stop)
        env = patch.dict(os.environ, {'GITHUB_OUTPUT': '', 'GITHUB_STEP_SUMMARY': '', 'DISCORD_WEBHOOK_SAKI': ''})
        env.start(); self.addCleanup(env.stop)

    def run_mock(self, replies=None, **kwargs):
        caller = Mock(side_effect=replies or [(response(), None), (response(dict(data(), areaName='伊豆')), None)])
        result = e.run(db_root=self.root, key='fake', api_caller=caller,
                       resolver=lambda u: u, creature_loader=lambda: self.creatures, **kwargs)
        return result, caller

    def test_validator_bad_data(self):
        self.assertEqual(e.validate_spot(data(), self.creatures), data())
        bad = [('lat', -16), ('lat', True), ('lng', 161), ('lat', float('nan')),
               ('months', [0]), ('months', [True]), ('months', '4'),
               ('levelMin', 'expert'), ('levelMin', []), ('entry', ['swim']),
               ('tags', ['whale']), ('monthly', {'1': []}), ('monthly', []),
               ('sources', []), ('sources', [{'url': 'https://reddit.com/x'}]*2),
               ('waterTemp', {'13': 20}), ('waterTemp', {'1': 0}),
               ('see', '不容錯過'), ('note', 12), ('monthlyNote', '打造'),
               ('warn', 'true'), ('newCreatures', [{'name': '鯨'}])]
        for field, value in bad:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    e.validate_spot(dict(data(), **{field: value}), self.creatures)
        for row in ({'c': 'turtle', 'p': 'certain'}, {'c': 'unknown', 'p': 'low'}, None):
            raw = data(); raw['monthly']['1'] = [row]
            with self.assertRaises(ValueError): e.validate_spot(raw, self.creatures)

    def test_unknown_moved_identity_and_sources_owned(self):
        raw = data(); raw['monthly']['1'] = [{'c': 'humpback', 'p': 'low', 'name': '座頭鯨', 'en': 'Humpback whale'}]
        raw.update(id='wrong', name='wrong', country='wrong', areaName='wrong')
        task = e.select_spots(self.queue)[0]
        sources = e.extract_sources(response()['candidates'][0], lambda u: u)
        result = e.normalize_spot(raw, task, self.creatures, sources, '2026-11-09')
        self.assertEqual(result['monthly']['1'], [])
        self.assertEqual(result['newCreatures'][0]['months'], [1])
        self.assertEqual(result['areaName'], '慶良間群島')
        self.assertEqual(result['name'], '第一點')
        self.assertFalse(result['monthlySeed'])
        self.assertEqual(result['sources'][0]['checked'], '2026-11-09')

    def test_two_success_marks_only_first_two(self):
        result, caller = self.run_mock()
        self.assertEqual(result, 0); self.assertEqual(caller.call_count, 2)
        self.assertEqual(len(list((self.root / 'data/spots').glob('*.json'))), 2)
        text = self.queue.read_text()
        self.assertRegex(text, r'- \[x\] \d{4}-\d{2}-\d{2} first｜第一點｜japan｜kerama')
        self.assertIn('- [ ] third', text)
        self.notify.assert_called_once()

    def test_first_failure_second_written(self):
        result, _ = self.run_mock([(response(dict(data(), lat=99)), None), (response(dict(data(), areaName='伊豆')), None)])
        self.assertEqual(result, 1)
        self.assertFalse((self.root / 'data/spots/first.json').exists())
        self.assertTrue((self.root / 'data/spots/second.json').exists())
        self.assertIn('- [ ] first', self.queue.read_text())
        self.assertIn(' second｜', self.queue.read_text())

    def test_override(self):
        before = self.queue.read_bytes()
        result, caller = self.run_mock(spot_override='- [ ] manual｜手動點｜japan｜kerama')
        self.assertEqual(result, 0); self.assertEqual(caller.call_count, 1)
        self.assertEqual(before, self.queue.read_bytes())
        self.assertTrue((self.root / 'data/spots/manual.json').exists())
        for invalid in ('', '../evil｜點｜japan｜kerama', 'id｜點｜japan'):
            with self.assertRaises(ValueError): e.select_spots(self.queue, invalid)

    def test_grounding_filters_and_requires_two(self):
        candidate = response()['candidates'][0]
        for host in ('facebook.com', 'old.reddit.com', 'instagram.com', 'm.youtube.com'):
            candidate['groundingMetadata']['groundingChunks'].append({'web': {'uri': f'https://{host}/x'}})
        sources = e.extract_sources(candidate, lambda u: u)
        self.assertEqual(len(sources), 2)
        result, _ = self.run_mock([( {'candidates': [candidate]}, None)], spot_override='one｜一｜japan｜kerama')
        self.assertEqual(result, 0)
        result, _ = self.run_mock([(response(), None)], spot_override='two｜二｜japan｜kerama')
        self.assertEqual(result, 0)
        bare = response(); bare['candidates'][0]['groundingMetadata'] = {}
        result, _ = self.run_mock([(bare, None)], spot_override='bad｜三｜japan｜kerama')
        self.assertEqual(result, 1)
        self.assertFalse((self.root / 'data/spots/bad.json').exists())

    def test_json_retry_and_fenced_json(self):
        result, caller = self.run_mock([(response(text='{'), None), (response(text='```json\n'+json.dumps(data())+'\n```'), None)], spot_override='retry｜重試｜japan｜kerama')
        self.assertEqual(result, 0); self.assertEqual(caller.call_count, 2)
        result, caller = self.run_mock([(response(text='{'), None)]*2, spot_override='bad｜壞｜japan｜kerama')
        self.assertEqual(result, 1); self.assertEqual(caller.call_count, 2)

    def test_loader_fail_no_calls_or_writes(self):
        caller = Mock()
        with self.assertRaises(OSError):
            e.run(db_root=self.root, key='fake', api_caller=caller, creature_loader=Mock(side_effect=OSError('offline')))
        caller.assert_not_called(); self.assertNotIn('[x]', self.queue.read_text())
        self.assertFalse((self.root / 'data/spots').exists())

    def test_load_creatures_mock(self):
        with patch.object(e.urllib.request, 'urlopen') as opener:
            opener.return_value.__enter__.return_value = io.StringIO(json.dumps({'creatures': list(self.creatures.values())}))
            self.assertEqual(e.load_creatures(), self.creatures)
            self.assertEqual(opener.call_args.args[0], e.CREATURES_URL)
            opener.return_value.__enter__.return_value = io.StringIO('{}')
            with self.assertRaises(ValueError): e.load_creatures()

    def test_material_and_prompt(self):
        folder = self.root / '01_潛水點'; folder.mkdir()
        (folder / 'guide.md').write_text('第一點的既有素材')
        selected = e.select_spots(self.queue)[0]
        material = e.read_material(self.root, selected)
        self.assertIn('既有素材', material)
        payload = e.build_request(selected, '2026-11-09', self.creatures, material)
        self.assertEqual(payload['tools'], [{'google_search': {}}])
        self.assertIn('最多 4', e.SYSTEM_PROMPT)
        for phrase in e.BANNED_PHRASES: self.assertIn(phrase, e.SYSTEM_PROMPT)

    def test_queue_changed_no_output(self):
        task = e.select_spots(self.queue)[0]; self.queue.write_text('changed')
        with self.assertRaises(ValueError):
            e.enrich_one(task, self.root, self.creatures, 'fake', lambda *_: (response(), None), lambda u: u, '2026-11-09')
        self.assertFalse((self.root / 'data/spots').exists())

    def test_failure_preserves_existing_output_and_continues(self):
        folder = self.root / 'data/spots'; folder.mkdir()
        path = folder / 'first.json'; path.write_text('{"existing": true}')
        before = path.read_bytes()
        result, caller = self.run_mock([(None, 'mock API failure'), (response(dict(data(), areaName='伊豆')), None)])
        self.assertEqual(result, 1); self.assertEqual(caller.call_count, 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((folder / 'second.json').exists())
        self.assertIn('- [ ] first', self.queue.read_text())
        self.assertRegex(self.queue.read_text(), r'- \[x\] \d{4}-\d{2}-\d{2} second')

    def test_redirect_to_blocked_domain_rejected(self):
        sources = e.extract_sources(response()['candidates'][0], lambda _: 'https://www.facebook.com/x')
        self.assertEqual(sources, [])

    def test_workflow_scope_and_partial_success(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/daily-spot-enrichment.yml').read_text()
        self.assertIn('cron: "30 1 * * *"', workflow)
        self.assertIn('spot_override:', workflow)
        self.assertIn('git add -- data/spots data/spot_queue.md', workflow)
        self.assertNotIn("outputs.status == 'ok'", workflow)
        self.assertIn('GITHUB_STEP_SUMMARY', workflow)
        self.assertIn('exit 0', workflow)

    def test_empty_and_cli_exit(self):
        self.queue.write_text('# empty\n')
        result, caller = self.run_mock()
        self.assertEqual(result, 0); caller.assert_not_called(); self.notify.assert_called_once()
        with patch.object(e, 'run', return_value=1): self.assertEqual(e.main([]), 1)
        with patch.object(e, 'run', side_effect=ValueError('mock failure')): self.assertEqual(e.main([]), 1)


if __name__ == '__main__':
    unittest.main()
