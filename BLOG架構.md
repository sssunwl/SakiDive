# DiveInOut 潛水平台資料庫 — BLOG + 行業最新資訊 架構

> 目的:把 SakiDive 從「潛點資料庫」升級成**潛水內容平台**——BLOG 是脊椎,同時是
> (1) 給受眾看的 SEO/生活風內容 (2) 未來帖文/影片的素材庫 (3) 生成器抓固定題材的來源。
> 站台維持現有首頁 https://sssunwl.github.io/SakiDive/ 深色海洋風,新增兩個區:📰 行業最新資訊 ｜ ✍️ BLOG。
> 最後更新:2026-07-18(規劃,尚未實作;SS 有 Token 時再蓋)

## 受眾定位(寫每篇文都要記得)

沿用 DiveInOut 品牌:**生活風 · 新手友善 · 泛亞洲潛旅 · 純養受眾(先不硬銷)**。
主要讀者三型:
- **計劃潛旅者**:在查亞洲潛點、簽證、預算、季節 → 目的地與行前文
- **潛水新手**:第一次潛水/考證/選裝備,怕雷 → 入門與裝備文
- **潛水生活者**:已入坑,關心保育、社群、生活感 → 生活風與產業文

## 內容分類(對齊現有 `下週題材建議.md` 的類型 + 資料庫目錄)

| 分類 | icon | 內容 | 餵入/連動資料庫 |
|------|:--:|------|------|
| 潛旅目的地 | 🌏 | 亞洲潛點攻略(帛琉/馬爾地夫/沖繩/菲律賓/印尼…):季節、生物、預算、簽證 | `01_潛水點` |
| 新手入門 | 🎓 | 第一次潛水、考證流程、選課、常見恐懼破解 | `02_課程證照` |
| 裝備指南 | 🤿 | 選購、保養、召回安全(如 HEAD 調節器)、潛水電腦怎麼選 | `03_技術裝備` |
| 安全知識 | 🛟 | 減壓、耳壓、流、氣量管理、緊急處理 | — |
| 海洋保育 | 🌱 | 鯊魚週、珊瑚、無痕潛水、保育組織 | — |
| 潛水生活 | 💙 | 送禮、女性潛水、社群活動、生活提案 | 社群週報 |
| 產業動態 | 📰 | 時效性新聞(SSI/PADI 政策、召回、賽事)→ 這類**同時**進「行業最新資訊」 | `00_每日簡報` |

## 兩個區的差別

- **📰 行業最新資訊** = 時效性列表(news feed)。短、帶日期、**每則必附來源連結**。資料來自「產業動態」類 + 每日簡報濃縮。頁面是倒序時間軸。
- **✍️ BLOG** = 常青長文(evergreen)。有封面、摘要、正文、相關潛點連結。是素材庫本體。

## 檔案結構(存在 SakiDive repo)

```
docs/diveinout/
  index.html            # 現有首頁(不動主結構,加兩個入口)
  news.html             # 📰 行業最新資訊 列表頁
  blog.html             # ✍️ BLOG 列表頁(可依分類篩選)
  article.html          # 單篇文章渲染器(?slug=... 讀 md)
content/                # 內容本體(agent 寫這裡)
  news.json             # 行業資訊陣列
  blog/
    index.json          # 文章清單(manifest)
    2026-07-18-palau-shark-guide.md
    ...
```

### 文章 md frontmatter schema
```yaml
---
title: "帛琉潛水全攻略:虎鯊、藍角、季節與預算"
slug: palau-shark-guide
category: 潛旅目的地        # 上表七類之一
audience: [計劃潛旅者]      # 計劃潛旅者 / 新手 / 潛水生活者
region: 帛琉               # 目的地文才填
date: 2026-07-18
sources:                  # 產業/時效內容必附;攻略文列參考來源
  - https://...
hero: images/palau.jpg
summary: 一句話摘要(列表頁+SEO用)
tags: [帛琉, 鯊魚, 亞洲潛旅]
related_spots: []         # 連 01_潛水點 的潛點
status: published         # draft / published
---
正文 markdown……
```

### news.json 單筆
```json
{ "date":"2026-07-18", "category":"產業動態",
  "title":"SSI 在美國 RSTC 成員資格變動",
  "summary":"…對潛水員的影響:…", "source":"https://…" }
```

## 渲染(合 GitHub Pages CSP,不吃外部 CDN)

- 列表頁 client-side fetch `content/blog/index.json` / `news.json` 算圖,沿用首頁深色海洋主題。
- 單篇 `article.html?slug=xxx` fetch 對應 md → 用**內嵌的小型 markdown 轉譯器**(自帶,不引 CDN)渲染。
- 首頁加兩個入口卡連到 news.html / blog.html。

## 排程(Saki agent)——建議改雙週

現行 `saki-monthly-dive-content` 是**每月一次**。SS 想密一點,**我建議雙週(每 14 天)**:
- 頻率夠養資料庫、又不會稀釋品質(潛水常青內容不需要每日更新)。
- **每次跑產出**:1 篇 BLOG 常青文(從 `下週題材建議.md` 挑最高熱度且尚未寫的)+ 更新 `news.json`(2–4 則產業動態,查證附源)+ 回填 diveinout 的 `social_pool.json`(見下)。
- 鐵則:潛水/安全/產業資訊**一律上網查證 + 附來源**(符合工作區資訊標準);缺資料寫【待補】不編造。
- 寫完把該篇重點濃縮進「素材庫」欄位,供帖文/影片與生成器取用。

> 若之後想更密,可拆成:news 每週、BLOG 雙週。先雙週跑順再說。

## 跟 Sona 品牌頁的關係(格式共用)

- DiveInOut 在 **Sona BRAND 中心**只有一張品牌頁:一眼看到品牌是什麼 + 帖文/廣告成效;
  它的「📰 行業最新資訊 / ✍️ BLOG」兩個 tab **直接連到這裡的 news.html / blog.html**,不在 Sona 重做。
- **OKIBLUES / OKIPLAYGROUND 的「📰 行業最新資訊」要參考本檔的格式**(news.json schema + 時間軸列表 + 必附來源),只是換行業(沖繩租車 / 沖繩旅遊)。

## 生成器如何用到這裡的內容

diveinout 的 `social_pool.json` 由排程 agent 依「本週新文章 + 行業資訊 + DNA」寫的 Threads/IG 草稿;
生成器「固定題材」則從已發佈 BLOG 的公司/常青重點抓(如潛點攻略要點)套版。詳見 SonaSNS 側 `內容平台架構.md`。
