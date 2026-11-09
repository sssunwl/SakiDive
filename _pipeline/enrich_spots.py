#!/usr/bin/env python3
"""每日查證兩個潛點；僅標準庫，網路入口可注入以供離線測試。"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

from enrich_content import (
    DB_ROOT, CONTENT_DIRS, BANNED_PHRASES, SelectedTask, atomic_write,
    banned_matches, blocked_source, call_gemini, extract_sources, get_candidate,
    mark_task_complete, notify_queue, pending_tasks, resolve_source_url,
    today_jst, verify_roadmap_task,
)

CREATURES_URL = 'https://raw.githubusercontent.com/sssunwl/diveinout/main/creatures.json'
AREA_NAMES = dict(zip(
    'okinawa-main ishigaki yonaguni kerama miyako kenting green-island xiaoliuqiu lanyu northeast-coast cebu bohol anilao coron palau bali komodo raja-ampat similan sipadan maldives red-sea'.split(),
    '沖繩本島 石垣島 與那國島 慶良間群島 宮古島 墾丁 綠島 小琉球 蘭嶼 東北角 宿霧 薄荷島 阿尼洛 科隆 帛琉 峇里島 科莫多 四王群島 斯米蘭 詩巴丹 馬爾地夫 紅海'.split()))
LEVELS = {'beginner', 'intermediate', 'advanced'}
ENTRIES = {'shore', 'boat', 'liveaboard', 'snorkel'}
TAGS = set('turtle manta whaleshark shark macro wreck school terrain coral mola'.split())
MONTHS = {str(n) for n in range(1, 13)}
SYSTEM_PROMPT = '''你是潛點資料查證編輯。只根據本次 Google Search grounding 查到的資料，最多 4 個搜尋查詢。
既有 Markdown 只是搜尋線索，不是已查證來源。不要依記憶補資料。使用繁中台灣用語與「你」。
只輸出一個純 JSON object（或 ```json 區塊），不要前言。未知欄位省略；monthly 必須有 1–12 全部 key，沒有資料的月份給 []。
生物出現月份須有搜尋來源支撐；不確定給 low 或不列，不可把全年或最佳潛季當作生物月份。
monthly 每項只有 c（提供清單的 id）、p（high/mid/low）。未知生物放 newCreatures：[{name,en,months:[1,2]}]。
價格、規範只能放 note 並標「⚠️需覆核」。waterTemp 只列查到的月份，絕不可用 0 補值。
欄位：place,areaName,lat,lng,level,levelMin, type,entry,season,months,see,note,warn,tags,monthly,monthlyNote,waterTemp,visibility,newCreatures。
lat/lng 必須查證。levelMin 只能 beginner/intermediate/advanced；entry 只能 shore/boat/liveaboard/snorkel；tags 只能 turtle/manta/whaleshark/shark/macro/wreck/school/terrain/coral/mola。
來源與日期由程式填入，不要自行編造。禁用詞語／正規表示式：''' + '、'.join(BANNED_PHRASES)


def parse_spot(line, line_number=None):
    original = line
    text = line.removeprefix('- [ ] ').strip()
    parts = [p.strip() for p in text.split('｜')]
    if len(parts) != 4 or not all(parts):
        raise ValueError('潛點格式必須是 id｜名稱｜國家｜area')
    if any(not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', parts[i]) for i in (0, 2, 3)):
        raise ValueError('潛點 id/country/area 不合法')
    return SelectedTask(text, line_number, original if line_number else None,
                        slug=parts[0], category=parts[2], spots=(parts[3],))


def select_spots(queue, override=None):
    if override is not None:
        return [parse_spot(override)]
    pending = [(n, line) for n, line in enumerate(queue.read_text(encoding='utf-8').splitlines(), 1)
               if line.startswith('- [ ] ')][:2]
    return [parse_spot(line, n) for n, line in pending]


def load_creatures():
    with urllib.request.urlopen(CREATURES_URL, timeout=30) as response:
        data = json.load(response)
    rows = data.get('creatures') if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('creatures.json 清單缺失或空白')
    if any(not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] for r in rows):
        raise ValueError('creatures.json id 格式異常')
    return {r['id']: r for r in rows}


def read_material(root, selected):
    name = selected.text.split('｜')[1]
    snippets = []
    for directory in CONTENT_DIRS.values():
        for path in sorted((root / directory.name).rglob('*.md')):
            text = path.read_text(encoding='utf-8')
            if name in text or selected.slug in text or name in path.stem:
                snippets.append(f'素材 {path.name}：\n{text[:12000]}')
    return '\n\n'.join(snippets)[:30000]


def build_request(selected, today, creatures, material):
    return {'system_instruction': {'parts': [{'text': SYSTEM_PROMPT}]},
            'contents': [{'role': 'user', 'parts': [{'text':
                f'日期：{today}\n指定潛點（id/name/country/area 不可改）：{selected.text}\n'
                f'現有生物：{json.dumps(list(creatures.values()), ensure_ascii=False)}\n素材：\n{material}'}]}],
            'tools': [{'google_search': {}}],
            'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 8192}}


def valid_months(value):
    return isinstance(value, list) and all(type(n) is int and 1 <= n <= 12 for n in value)


def validate_spot(data, creatures):
    if not isinstance(data, dict):
        raise ValueError('輸出必須是 JSON object')
    for field, low, high in (('lat', -15, 50), ('lng', 30, 160)):
        v = data.get(field)
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f'{field} 超出範圍或缺失')
    if 'months' in data and not valid_months(data['months']):
        raise ValueError('months 不合法')
    for field, allowed in (('entry', ENTRIES), ('tags', TAGS)):
        if field in data and (not isinstance(data[field], list) or any(not isinstance(v, str) or v not in allowed for v in data[field])):
            raise ValueError(f'{field} 不合法')
    if 'levelMin' in data and (not isinstance(data['levelMin'], str) or data['levelMin'] not in LEVELS):
        raise ValueError('levelMin 不合法')
    for field in ('place', 'areaName', 'level', 'type', 'season', 'see', 'note', 'monthlyNote', 'visibility'):
        if field in data and not isinstance(data[field], str):
            raise ValueError(f'{field} 必須是字串')
    for field in ('see', 'note', 'monthlyNote'):
        if banned_matches(data.get(field, '')):
            raise ValueError(f'{field} 含禁用詞')
    for field in ('warn', 'monthlySeed'):
        if field in data and type(data[field]) is not bool:
            raise ValueError(f'{field} 必須是布林值')
    monthly = data.get('monthly')
    if not isinstance(monthly, dict) or set(monthly) != MONTHS:
        raise ValueError('monthly 必須有 12 個月份 key')
    for rows in monthly.values():
        if not isinstance(rows, list):
            raise ValueError('monthly 月份必須是陣列')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('c'), str) or row['c'] not in creatures or row.get('p') not in ('high', 'mid', 'low'):
                raise ValueError('monthly 生物或 p 不合法')
    if 'waterTemp' in data:
        temps = data['waterTemp']
        if not isinstance(temps, dict) or any(k not in MONTHS or type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 45 for k, v in temps.items()):
            raise ValueError('waterTemp 不合法')
    new = data.get('newCreatures', [])
    if not isinstance(new, list) or any(not isinstance(r, dict) or not isinstance(r.get('name'), str) or not r['name'] or not isinstance(r.get('en'), str) or not valid_months(r.get('months')) for r in new):
        raise ValueError('newCreatures 不合法')
    sources = data.get('sources')
    if not isinstance(sources, list) or len(sources) < 2:
        raise ValueError('sources 少於 2')
    urls = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('url'), str):
            raise ValueError('sources 格式不合法')
        url = source['url']; parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or blocked_source(url):
            raise ValueError('sources 網域不合法')
        urls.add(url)
    if len(urls) < 2:
        raise ValueError('sources 需要至少 2 個不同網址')
    return data


def normalize_spot(raw, selected, creatures, sources, today):
    if not isinstance(raw, dict):
        raise ValueError('輸出必須是 JSON object')
    data = copy.deepcopy(raw)
    allowed = set('place areaName lat lng level levelMin type entry season months see note warn tags monthly monthlyNote waterTemp visibility newCreatures'.split())
    data = {k: v for k, v in data.items() if k in allowed}
    data.update(id=selected.slug, name=selected.text.split('｜')[1], country=selected.category,
                area=selected.spots[0], checked=today, monthlySeed=False,
                sources=[{'url': s.url, 'title': s.title, 'checked': today} for s in sources])
    if data['area'] in AREA_NAMES:
        data['areaName'] = AREA_NAMES[data['area']]
    elif not data.get('areaName'):
        raise ValueError('新地區缺少 areaName')
    # Unknown IDs are review candidates only; never introduce IDs into the site catalogue.
    monthly = data.get('monthly')
    unknown = {}
    if isinstance(monthly, dict):
        for month, rows in monthly.items():
            if not isinstance(rows, list):
                continue
            keep = []
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get('c'), str) and row['c'] not in creatures:
                    if row.get('p') not in ('high', 'mid', 'low') or month not in MONTHS:
                        raise ValueError('未知生物的月份或 p 不合法')
                    item = unknown.setdefault(row['c'], {'name': row.get('name') or row['c'], 'en': row.get('en') or row['c'], 'months': []})
                    item['months'].append(int(month))
                else:
                    keep.append(row)
            monthly[month] = keep
    if unknown:
        if not isinstance(data.get('newCreatures', []), list):
            raise ValueError('newCreatures 不合法')
        data.setdefault('newCreatures', []).extend(unknown.values())
    return validate_spot(data, creatures)


def enrich_one(selected, root, creatures, key, caller, resolver, today):
    payload = build_request(selected, today, creatures, read_material(root, selected))
    for attempt in range(2):
        response, error = caller(key, payload)
        if error:
            raise RuntimeError(error)
        candidate, text = get_candidate(response)
        text = re.sub(r'^```json\s*\n(.*?)\n```$', r'\1', text, flags=re.S | re.I)
        try:
            raw = json.loads(text, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            break
        except (json.JSONDecodeError, ValueError) as error:
            if attempt:
                raise ValueError(f'JSON 解析失敗：{error}') from error
            payload['contents'].extend([
                {'role': 'model', 'parts': [{'text': text}]},
                {'role': 'user', 'parts': [{'text': 'JSON 解析失敗，請重新 Google Search 查證並只回傳完整合法 JSON。'}]}])
    sources = extract_sources(candidate, resolver)
    data = normalize_spot(raw, selected, creatures, sources, today)
    queue = root / 'data/spot_queue.md'
    verify_roadmap_task(queue, selected)
    path = root / 'data/spots' / (selected.slug + '.json')
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('輸出路徑是 symlink')
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
    mark_task_complete(queue, selected, today)
    print(f'✅ 已寫入 data/spots/{path.name}')


def run(spot_override=None, db_root=DB_ROOT, key=None, api_caller=call_gemini,
        resolver=resolve_source_url, creature_loader=load_creatures):
    queue = db_root / 'data/spot_queue.md'
    selected = select_spots(queue, spot_override)
    if not selected:
        notify_queue('⚠️ 潛點佇列已空，請補點')
        return 0
    creatures = creature_loader()  # Failure must precede every API call and write.
    if not creatures:
        raise ValueError('生物清單為空，拒絕寫入')
    key = key or os.environ.get('GEMINI_API_KEY')
    if not key:
        raise RuntimeError('缺少 GEMINI_API_KEY 環境變數')
    failed = False
    today = today_jst()
    for task in selected:
        try:
            enrich_one(task, db_root, creatures, key, api_caller, resolver, today)
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            failed = True
            print(f'::error::{task.slug}：{error}', file=sys.stderr)
    remaining = len(pending_tasks(queue))
    if remaining <= 10:
        notify_queue(f'⚠️ 潛點佇列剩 {remaining} 點，請補點' if remaining else '⚠️ 潛點佇列已空，請補點')
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Google Search grounding 潛點查證')
    parser.add_argument('--spot-override', help='佇列格式一行；不打勾佇列')
    args = parser.parse_args(argv)
    try:
        return run(spot_override=args.spot_override)
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        print(f'::error::{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
