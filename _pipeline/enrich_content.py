#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiveInOut 每日內容豐富化
- 引擎:Gemini generateContent REST API + Google Search grounding
- 只用標準庫,免 pip install
- key 只從 GEMINI_API_KEY 環境變數讀取
- 每次只寫 01/02/03 其中一個 Markdown 檔
- 腳本不執行任何 git 指令

用法:
    python3 enrich_content.py
    python3 enrich_content.py --task "平壓技巧與停止下潛時機"
    python3 enrich_content.py --dry-run
"""
import argparse
import datetime
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


MODEL = "gemini-2.5-flash"
API_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent")
DB_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIRS = {
    "spots": DB_ROOT / "01_潛水點",
    "courses": DB_ROOT / "02_課程證照",
    "tech": DB_ROOT / "03_技術裝備",
}
JST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

SYSTEM_PROMPT = """你是 Saki，DiveInOut 泛亞洲潛旅生活誌的內容作者。
你的唯一任務是針對指定題目，使用 Google 搜尋 grounding 查證後寫出一篇短篇 Markdown 正文。

嚴格規則：
1. 正文約 600–1200 字，使用繁體中文，語氣為泛亞洲潛旅生活風、新手友善且實用。
2. 最多使用 3 個搜尋查詢，優先採用原始或官方來源；不可憑模型記憶補寫安全、醫療、法規、價格、季節或潛店資訊。
3. 價格、季節、法規等時效資訊須標記「⚠️需覆核」。找不到可靠資料時清楚寫「未確認」，絕不編造。
4. 涉及安全的內容必須提醒讀者依現場專業人員、環境與自身訓練做判斷。
5. 只輸出文章正文，不要輸出檔名、來源清單、URL、前言、說明或 Markdown code fence。來源連結與抓取日會由程式依 groundingMetadata 加入。
6. 完成這一篇就停止，不延伸成其他題目，也不提出後續任務。
"""


@dataclass(frozen=True)
class SelectedTask:
    text: str
    line_number: Optional[int]
    original_line: Optional[str]


@dataclass(frozen=True)
class Source:
    number: int
    title: str
    url: str
    chunk_indices: tuple[int, ...]


def today_jst():
    return datetime.datetime.now(JST).date().isoformat()


def write_github_output(name, value):
    """安全寫入 GitHub Actions output；本機執行時不做事。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    value = str(value)
    marker = "SAKI_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    with open(path, "a", encoding="utf-8") as output:
        output.write(f"{name}<<{marker}\n{value}\n{marker}\n")


def select_task(roadmap, override=None):
    """回傳 override，或路線圖第一個未完成項目及其精確位置。"""
    if override is not None:
        task = override.strip()
        if not task:
            raise ValueError("--task 不可為空白")
        return SelectedTask(task, None, None)

    for line_number, line in enumerate(
            roadmap.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("- [ ] "):
            task = line.removeprefix("- [ ] ").strip()
            return SelectedTask(task, line_number, line)
    return None


def build_user_prompt(task, today):
    return f"""今天是 {today}。本次唯一指定題目：{task}

請直接撰寫符合 system instruction 的 Markdown 正文（約 600–1200 字）。先用 Google Search grounding 查證，最多 3 個搜尋查詢；不要自行輸出來源網址或來源清單。"""


def build_request(task, today):
    """建立單次 generateContent request；搜尋由 API grounding 完成。"""
    return {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [
            {"text": build_user_prompt(task, today)},
        ]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.3,
            # 2.5-flash 是 thinking 模型，思考 token 也計入此額度；
            # 1600 會讓 600 字正文被硬生生截斷，且截斷後長度剛好通過檢查。
            "maxOutputTokens": 8192,
        },
    }


def call_gemini(key, payload, retries=2):
    """單次 API request 最長 120 秒；暫時性失敗最多再試 2 次。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = "未知錯誤"
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read()), None
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "ignore")[:500]
            last_error = f"Gemini HTTP {error.code}: {detail}"
            retryable = error.code in (429, 500, 502, 503, 504)
            if not retryable or attempt >= retries:
                return None, last_error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = f"Gemini 呼叫失敗: {error}"
            if attempt >= retries:
                return None, last_error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return None, f"Gemini 回應不是合法 JSON: {error}"

        # 每分鐘限制的建議等待約 60 秒，6/12 秒等於白等一輪。
        wait = 30 * (attempt + 1)
        print(f"  …Gemini 暫時失敗，{wait}s 後重試（{attempt + 1}/{retries}）")
        time.sleep(wait)
    return None, last_error


def get_candidate(response):
    try:
        candidate = response["candidates"][0]
        parts = candidate["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError):
        raise ValueError("Gemini 回應格式異常，可能被安全過濾或沒有候選內容")
    if not text:
        raise ValueError("Gemini 沒有回傳文字內容")
    # 截斷的正文長度可能剛好落在合格區間，必須靠 finishReason 才擋得住。
    finish = candidate.get("finishReason")
    if finish and finish != "STOP":
        raise ValueError(f"Gemini 未正常結束（finishReason={finish}），拒絕寫入不完整內容")
    return candidate, strip_code_fence(text)


def strip_code_fence(text):
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", text,
                         flags=re.S | re.I)
    return match.group(1).strip() if match else text.strip()


def resolve_source_url(url):
    """跟隨 grounding 轉址，取得實際來源網址；失敗時保留 API URI。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Range": "bytes=0-0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resolved = response.geturl()
    except Exception as error:
        print(f"  ⚠️ 來源轉址解析失敗，保留 grounding URL: {error}")
        resolved = url
    clean = urllib.parse.urlsplit(resolved)._replace(fragment="").geturl()
    return clean


def extract_sources(candidate, resolver=resolve_source_url):
    """只信任 groundingMetadata 內的來源，並記住 chunk 與來源的對應。"""
    metadata = candidate.get("groundingMetadata") or {}
    chunks = metadata.get("groundingChunks") or []
    supports = metadata.get("groundingSupports") or []
    used = []
    for support in supports:
        for index in support.get("groundingChunkIndices") or []:
            if isinstance(index, int) and index not in used:
                used.append(index)
    if not used:
        used = list(range(len(chunks)))

    source_data = []
    by_url = {}
    for index in used:
        if not 0 <= index < len(chunks):
            continue
        web = chunks[index].get("web") or {}
        raw_url = web.get("uri")
        if not raw_url:
            continue
        url = resolver(raw_url)
        if not url:
            continue
        title = html.unescape((web.get("title") or "").strip())
        if not title:
            title = urllib.parse.urlparse(url).netloc.replace("www.", "")
        title = re.sub(r"\s+", " ", title).replace("[", "［").replace("]", "］")
        if url in by_url:
            source_data[by_url[url]]["indices"].add(index)
            continue
        by_url[url] = len(source_data)
        source_data.append({"title": title, "url": url, "indices": {index}})

    return [Source(number=number, title=item["title"], url=item["url"],
                   chunk_indices=tuple(sorted(item["indices"])))
            for number, item in enumerate(source_data, 1)]


def source_by_chunk(sources):
    return {index: source for source in sources for index in source.chunk_indices}


def insert_grounding_citations(markdown, candidate, sources, today):
    """依 groundingSupports 的段落終點插入程式控制的正確來源 URL。"""
    supports = (candidate.get("groundingMetadata") or {}).get("groundingSupports") or []
    by_chunk = source_by_chunk(sources)
    insertions = {}
    for support in supports:
        segment = support.get("segment") or {}
        end = segment.get("endIndex")
        segment_text = segment.get("text") or ""
        matches_index = (isinstance(end, int) and 0 <= end <= len(markdown)
                         and markdown[max(0, end - len(segment_text)):end] == segment_text)
        if not matches_index:
            found = markdown.find(segment_text) if segment_text else -1
            end = found + len(segment_text) if found >= 0 else None
        if end is None:
            continue
        cited = insertions.setdefault(end, [])
        for index in support.get("groundingChunkIndices") or []:
            source = by_chunk.get(index)
            if source and source.number not in cited:
                cited.append(source.number)

    for end, cited in sorted(insertions.items(), reverse=True):
        if cited:
            links = " ".join(
                f"[來源 {number}]({sources[number - 1].url})" for number in cited
            )
            citation = f" {links}（抓取：{today}）"
            markdown = markdown[:end] + citation + markdown[end:]
    return markdown


def normalized_host(url):
    return urllib.parse.urlparse(url).netloc.casefold().removeprefix("www.")


def fix_source_urls(markdown, sources):
    """用 grounding URL 覆蓋模型可能抄錯編碼的連結；未知連結不落盤。"""
    canonical = {source.url: source for source in sources}

    def replace(match):
        label, model_url = match.group(1), match.group(2)
        if model_url in canonical:
            return match.group(0)
        label_folded = re.sub(r"\s+", "", label).casefold()
        title_matches = [source for source in sources
                         if label_folded and label_folded in
                         re.sub(r"\s+", "", source.title).casefold()]
        if len(title_matches) == 1:
            return f"[{label}]({title_matches[0].url})"
        host_matches = [source for source in sources
                        if normalized_host(source.url) == normalized_host(model_url)]
        if host_matches:
            return f"[{label}]({host_matches[0].url})"
        return label

    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", replace, markdown)


def body_length(markdown):
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown)
    plain = re.sub(r"[#*_>`~-]", "", plain)
    return len(re.sub(r"\s+", "", plain))


def choose_content_dir(task):
    explicit = next((path for path in CONTENT_DIRS.values() if path.name in task), None)
    if explicit:
        return explicit
    course_words = ("課程", "證照", "考證", "PADI", "SSI", "AIDA", "Molchanovs", "費用")
    spot_words = ("潛點", "攻略", "交通", "海況", "入水", "岸潛", "夜潛", "船潛", "季節", "生態")
    tech_words = ("平壓", "浮力", "減壓", "潛水電腦", "裝備", "技術", "訓練", "安全", "覆核", "資料品質")
    if any(word.casefold() in task.casefold() for word in course_words):
        return CONTENT_DIRS["courses"]
    if any(word.casefold() in task.casefold() for word in spot_words):
        return CONTENT_DIRS["spots"]
    if any(word.casefold() in task.casefold() for word in tech_words):
        return CONTENT_DIRS["tech"]
    return CONTENT_DIRS["spots"]


def filename_from_task(task):
    short = re.split(r"[（(：:]", unicodedata.normalize("NFKC", task), maxsplit=1)[0]
    slug = re.sub(r"[^\w-]+", "-", short, flags=re.UNICODE).strip("-_.")[:60]
    if not slug:
        slug = "內容-" + hashlib.sha256(task.encode("utf-8")).hexdigest()[:10]
    return slug + ".md"


def verify_roadmap_task(roadmap, selected):
    """寫檔前再次核對精確行號與整行原文，避免誤打勾。"""
    if selected.line_number is None:
        return
    lines = roadmap.read_text(encoding="utf-8").splitlines()
    index = selected.line_number - 1
    if not 0 <= index < len(lines) or lines[index] != selected.original_line:
        raise ValueError(f"路線圖第 {selected.line_number} 行已變更，拒絕誤打勾")


def mark_task_complete(roadmap, selected, today):
    if selected.line_number is None:
        return False
    lines = roadmap.read_text(encoding="utf-8").splitlines(keepends=True)
    index = selected.line_number - 1
    if not 0 <= index < len(lines) or lines[index].rstrip("\r\n") != selected.original_line:
        raise ValueError(f"路線圖第 {selected.line_number} 行已變更，拒絕誤打勾")
    ending = "\n" if lines[index].endswith("\n") else ""
    lines[index] = f"- [x] {today} {selected.text}{ending}"
    atomic_write(roadmap, "".join(lines))
    return True


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as tmp:
            tmp.write(text.rstrip() + "\n")
            temp_name = tmp.name
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def render_markdown(task, body, candidate, sources, today):
    body = insert_grounding_citations(body, candidate, sources, today)
    body = fix_source_urls(body, sources).strip()
    source_lines = [f"- [來源 {source.number}：{source.title}]({source.url})（抓取：{today}）"
                    for source in sources]
    return f"# {task}\n\n{body}\n\n## 來源\n\n" + "\n".join(source_lines) + "\n"


def run(task_override=None, dry_run=False, db_root=DB_ROOT, key=None,
        api_caller=call_gemini, resolver=resolve_source_url):
    roadmap = db_root / "內容路線圖.md"
    selected = select_task(roadmap, task_override)
    if selected is None:
        print("::notice::內容路線圖沒有未完成項目，本次跳過")
        write_github_output("has_task", "false")
        write_github_output("wrote_content", "false")
        return None

    today = today_jst()
    write_github_output("has_task", "true")
    write_github_output("task", selected.text)
    write_github_output("from_roadmap", str(selected.line_number is not None).lower())
    print(f"本次題目：{selected.text}")

    key = key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("缺少 GEMINI_API_KEY 環境變數")
    response, error = api_caller(key, build_request(selected.text, today))
    if error:
        raise RuntimeError(error)
    candidate, body = get_candidate(response)
    length = body_length(body)
    # 只擋過短(空殼或殘缺)。過長不是缺陷,模型對中文字數指示本來就不精確,
    # 真正的守門是 finishReason 未截斷、有 grounding 來源、不覆寫既有檔。
    if length < 300:
        raise ValueError(f"Gemini 正文只有 {length} 字，過短，拒絕寫入")
    if length > 2000:
        print(f"  ⚠️ 正文 {length} 字，超出建議的 600–1200 字，仍照常寫入")
    sources = extract_sources(candidate, resolver)
    if not sources:
        raise ValueError("Gemini 回應沒有可用的 groundingMetadata 來源，拒絕寫檔")
    markdown = render_markdown(selected.text, body, candidate, sources, today)

    path = choose_content_dir_for_root(db_root, selected.text) / filename_from_task(selected.text)
    if path.exists() and path.is_symlink():
        raise ValueError("推導出的輸出路徑是 symlink，拒絕寫入")
    if path.exists() and not dry_run:
        raise ValueError(
            f"{path.name} 已存在，拒絕覆寫既有內容。"
            "若要重寫請先人工確認並移除該檔，或改用別的題目。")
    if dry_run:
        print(f"\n===== 產出預覽（--dry-run，未寫檔）=====\n目標：{path}\n")
        print(markdown)
        write_github_output("wrote_content", "false")
        return path

    verify_roadmap_task(roadmap, selected)
    atomic_write(path, markdown)
    marked = mark_task_complete(roadmap, selected, today)
    write_github_output("output_path", str(path.relative_to(db_root)))
    write_github_output("wrote_content", "true")
    print(f"✅ 已寫入 {path.relative_to(db_root)}")
    if marked:
        print(f"✅ 已標記內容路線圖第 {selected.line_number} 行")
    return path


def choose_content_dir_for_root(db_root, task):
    """測試時可替換 repo root；分類規則與正式 CONTENT_DIRS 相同。"""
    chosen = choose_content_dir(task).name
    return db_root / chosen


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="以 Gemini Search grounding 豐富一筆內容")
    parser.add_argument("--task", help="覆寫路線圖題目；使用時不打勾路線圖")
    parser.add_argument("--dry-run", action="store_true", help="呼叫 API 並預覽，不寫任何檔案")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        run(task_override=args.task, dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
