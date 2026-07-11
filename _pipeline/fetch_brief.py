#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SakiDiveDB 潛水每日簡報抓取器
- 引擎:Gemini (google generativelanguage REST API)
- 只用標準庫,免 pip install
- key 從 ~/.config/gemini/credentials.json 讀,不寫死在碼裡
- 原則:只根據抓到的原文摘要,不腦補;時效性資料標 ⚠️需覆核
- 防呆:用程式帶入的正確 URL 覆蓋 Gemini 產出的來源連結
- 每天產出一則「綜合啟示」並累積進 啟示彙整.md

用法:
    python3 fetch_brief.py            # 抓今天,寫入 00_每日簡報/YYYY-MM-DD.md
    python3 fetch_brief.py --dry      # 只印出結果不寫檔(測試用)
    python3 fetch_brief.py --push     # 抓完後 git commit + push 到 GitHub
"""
import json, os, re, sys, time, subprocess
import urllib.request, urllib.error, urllib.parse, datetime

# ---- 設定 ----
MODEL = "gemini-2.5-flash"
CRED_PATH = os.path.expanduser("~/.config/gemini/credentials.json")
DB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # SakiDiveDB/
OUT_DIR = os.path.join(DB_ROOT, "00_每日簡報")

# 要掃描的來源(可自由增減)。標題只是給人看的備註。
# 排序 = 重要性:水肺潛水(scuba)/綜合為主,自由潛水為輔。
SOURCES = [
    # 中文・綜合/產業
    ("EZDIVE 易潛雜誌(水肺+自由+產業)",
     "https://www.ezdivemag.com/zh/"),
    ("BlueTrend 藍色脈動(潛水/海洋)",
     "https://bluetrend.media/"),
    ("潛進台灣 Taiwan Dive In(潛點/裝備/課程)",
     "https://www.taiwan-diving.org/"),
    # 中文・台灣時事(安全/法規)
    ("自由時報-自由潛水(自潛面向)",
     "https://news.ltn.com.tw/topic/%E8%87%AA%E7%94%B1%E6%BD%9B%E6%B0%B4"),
    # 國際・生活風/潛旅(英文,Gemini 會翻中)
    ("PADI Blog(國際潛旅/裝備/生活)",
     "https://blog.padi.com/"),
    ("DeeperBlue(國際新聞/自潛/潛旅)",
     "https://www.deeperblue.com/"),
    ("ScubaDiverLife(生活風)",
     "https://scubadiverlife.com/"),
]

MAX_CHARS_PER_SOURCE = 6000   # 控制送進 Gemini 的量 = 控制成本
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


def load_key():
    with open(CRED_PATH, encoding="utf-8") as f:
        return json.load(f)["api_key"]


def fetch_text(url):
    """抓網頁 → 粗略轉純文字。失敗回傳 None。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception as e:
        return None, f"抓取失敗: {e}"
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS_PER_SOURCE], None


def build_prompt(today, blocks):
    joined = "\n\n".join(f"### 來源:{name}\nURL: {url}\n內文節錄:\n{txt}"
                         for name, url, txt in blocks)
    return f"""你是潛水資料庫的資料整理員,今天是 {today}。
本資料庫負責人 Saki 是**專業水肺潛水(scuba)潛水員,同時也玩自由潛水**。
涵蓋範疇以**水肺潛水為主軸**,並包含自由潛水、潛水產業、海洋生態、裝備、潛點;
**不要只偏重自由潛水**——scuba 相關(考證、裝備、船潛、深潛、水下攝影、海洋保育)同等重要。
本簡報服務「**DiveInOut 潛旅生活誌**」——走**生活風、新手友善、泛亞洲潛旅**;
重點多放潛旅、潛點體驗、裝備開箱、入門知識、海洋生活,別只有硬邦邦的新聞。
**部分來源是英文站,請翻成流暢的繁體中文。**
以下是我抓到的幾個潛水相關網頁的節錄文字。請整理成一份「潛水每日簡報」的 Markdown。

嚴格規則(很重要):
1. 只能根據下面提供的節錄內容,不可自行編造事實或數字。
2. 每個重點後面要附上它來自哪個來源(用 Markdown 連結 [來源名](URL))與「抓取:{today}」。
3. 價格、海況、季節、賽事日期等會變動的資訊,句尾加「⚠️需覆核」。
4. 若某來源沒有實質的潛水新聞,就跳過它,不要硬湊。
5. 全部用繁體中文。語氣簡潔,重點導向。

輸出格式(直接輸出這個 Markdown,不要多加說明):

# 潛水每日簡報 — {today}

## 🔑 今日重點
(數個重點,每個含來源;每點可附一行「→ 對我方啟示:」)

## 📌 值得追蹤 / 待深讀
(可選,列出值得之後深入的線索)

## 💡 今日綜合啟示
用「條列 3~5 點」,**依重要性由高到低排序**;每點以 `**粗體關鍵詞**：` 開頭,再接一句話講清楚。
這是 SS 每天只想快速掃過的重點,不要寫成長段落。

--- 以下是抓到的原文節錄 ---
{joined}
"""


def call_gemini(key, prompt, retries=4):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={key}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    last = "未知錯誤"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=120).read()
            d = json.loads(resp)
            return d["candidates"][0]["content"]["parts"][0]["text"], None
        except urllib.error.HTTPError as e:
            last = f"Gemini HTTP {e.code}: {e.read().decode('utf-8','ignore')[:160]}"
            if e.code in (429, 500, 503) and attempt < retries - 1:
                wait = 6 * (attempt + 1)
                print(f"  …Gemini 忙碌({e.code}),{wait}s 後重試({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            return None, last
        except (KeyError, IndexError):
            return None, "Gemini 回應格式異常(可能被安全過濾或空回應)"
        except Exception as e:
            last = f"Gemini 呼叫失敗: {e}"
            if attempt < retries - 1:
                time.sleep(6 * (attempt + 1))
                continue
            return None, last
    return None, last


def fix_source_urls(md, sources):
    """用程式帶入的正確 URL 覆蓋 Gemini 產出的來源連結(防 AI 抄錯網址)。"""
    dmap = {}
    for name, url in sources:
        dmap[urllib.parse.urlparse(url).netloc.replace("www.", "")] = url

    def repl(m):
        text, url = m.group(1), m.group(2)
        d = urllib.parse.urlparse(url).netloc.replace("www.", "")
        return f"[{text}]({dmap[d]})" if d in dmap else m.group(0)

    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", repl, md)


def extract_insight(md):
    """從簡報中抽出『今日綜合啟示』那一段。"""
    m = re.search(r"##\s*💡[^\n]*\n+(.+?)(?=\n##\s|\n---|\n<sub|\Z)", md, re.S)
    return m.group(1).strip() if m else ""


def append_insight(db_root, today, insight):
    """把當日綜合啟示累積進 啟示彙整.md(最新在上,重跑不重複)。"""
    if not insight:
        return None
    path = os.path.join(db_root, "啟示彙整.md")
    header = "# 💡 每日綜合啟示彙整\n\n> 每天一則,最新在上。SS 每天只想看的那一點就在這。\n\n"
    body = ""
    if os.path.exists(path):
        raw = open(path, encoding="utf-8").read()
        body = raw.split("就在這。", 1)[-1].lstrip("\n") if "就在這。" in raw else raw
        # 移除今天的舊段落,避免重跑重複
        body = re.sub(rf"##\s*{re.escape(today)}\b.*?(?=\n##\s|\Z)", "", body, flags=re.S).strip()
    entry = f"## {today}\n{insight}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write((header + entry + ("\n" + body if body else "")).rstrip() + "\n")
    return path


def recent_briefs_text(out_dir, n=4):
    """讀最近 n 份每日簡報當作題材建議的素材。"""
    if not os.path.isdir(out_dir):
        return ""
    files = sorted(f for f in os.listdir(out_dir) if f.endswith(".md"))[-n:]
    parts = [open(os.path.join(out_dir, f), encoding="utf-8").read() for f in files]
    return "\n\n".join(parts)[:20000]


def build_topics_prompt(today, ctx):
    return f"""根據以下最近的潛水每日簡報,為「DiveInOut 潛旅生活誌」提出**下週最值得做的 9 個內容題目**。
今天是 {today}。定位:生活風、新手友善、泛亞洲潛旅。

要求:
- 依「預估熱度」由高到低排序。
- 預估熱度是 0–100 的分數(你的預測,之後會拿實際數據對比,請認真評估、拉開差距)。
- 每題一句「依據」(為什麼會紅)。
- 類型只能填:裝備 / 潛旅 / 安全 / 保育 / 入門 / 產業 / 生活。
- 只根據提供的情報,不要編造。

直接輸出這個 Markdown,不要多加說明:

# 🔥 下週題材建議 — {today}

> 熱度為 AI 預估(0–100),日後填入「實際表現」以校準預測準度。

| # | 題目 | 類型 | 預估熱度 | 依據 | 實際表現 |
|:-:|------|:--:|:--:|------|:--:|
(9 列,熱度由高到低,實際表現先留「—」)

--- 最近情報 ---
{ctx}
"""


def generate_topics(key, today, out_dir, db_root):
    ctx = recent_briefs_text(out_dir)
    if not ctx:
        return None
    md, err = call_gemini(key, build_topics_prompt(today, ctx))
    if err:
        print("⚠️ 題材建議產生失敗:", err)
        return None
    path = os.path.join(db_root, "下週題材建議.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md.rstrip() + "\n")
    print(f"✅ 下週題材建議 → {path}")
    return path


def git_push(db_root, today):
    """git add/commit/push。需先 git init 並設好 remote。"""
    def run(*args):
        return subprocess.run(args, cwd=db_root, capture_output=True, text=True)
    run("git", "add", "-A")
    c = run("git", "commit", "-m", f"每日簡報更新 {today}")
    p = run("git", "push")
    if p.returncode == 0:
        print("✅ 已 push 到 GitHub")
    else:
        print("⚠️ push 失敗:", (p.stderr or c.stderr or "").strip()[:200])


def main():
    dry = "--dry" in sys.argv
    push = "--push" in sys.argv
    today = datetime.date.today().isoformat()
    key = load_key()

    blocks, notes = [], []
    for name, url in SOURCES:
        txt, err = fetch_text(url)
        if err or not txt:
            notes.append(f"- ⚠️ {name}:{err or '無內容'}")
            continue
        blocks.append((name, url, txt))
        notes.append(f"- ✅ {name}:抓到 {len(txt)} 字")

    print("抓取狀況:")
    print("\n".join(notes))

    if not blocks:
        print("❌ 沒有任何來源抓成功,中止。")
        sys.exit(1)

    md, err = call_gemini(key, build_prompt(today, blocks))
    if err:
        print("❌", err)
        sys.exit(1)

    # 防呆:用正確 URL 覆蓋 Gemini 可能抄錯的來源連結
    md = fix_source_urls(md, [(n, u) for n, u, _ in blocks])

    md += "\n\n---\n<sub>抓取來源狀況:\n" + "\n".join(notes) + "\n</sub>\n"

    if dry:
        print("\n===== 產出預覽(--dry,未寫檔) =====\n")
        print(md)
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{today}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n✅ 已寫入 {out}")

    ipath = append_insight(DB_ROOT, today, extract_insight(md))
    if ipath:
        print(f"✅ 綜合啟示已累積 → {ipath}")

    generate_topics(key, today, OUT_DIR, DB_ROOT)

    if push:
        git_push(DB_ROOT, today)


if __name__ == "__main__":
    main()
