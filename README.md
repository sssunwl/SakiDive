# 🐬 Saki 潛水資料庫(SakiDiveDB)

> 負責人:**Saki**(AIoffice 掛名)｜抓取引擎:**Gemini 2.5 Flash**
> 排程:**launchd 每週一 09:00 自動跑**(零 Claude token)
> 建立日:2026-07-11

## 這是什麼

「先囤好、之後便宜地問」的知識庫。潛水資料一次消化成乾淨 MD,
以後問東西時 Claude 只讀這裡的檔就能準確回答,不用每次重爬網(省 token)。

**範疇**:負責人 Saki 是專業**水肺潛水(scuba)**員、同時也玩**自由潛水**。
本庫以**水肺潛水為主軸**,涵蓋自由潛水、產業、海洋生態、裝備、潛點——**不只自由潛水**。

## ⚠️ 重要:實體位置與捷徑

- **實體檔案在** `~/SakiDiveDB/`(家目錄,非保護)
- **vault 內是捷徑**:`~/Downloads/claude/_ideas/SakiDiveDB` → symlink 指向上面
- **為什麼**:vault 在 `~/Downloads`(macOS 受保護),launchd 自動排程無權寫入。
  搬到非保護位置 + 捷徑,Obsidian 照樣看得到,排程也能寫。

## 資料夾地圖

| 資料夾 | 內容 | 更新 |
|---|---|---|
| `00_每日簡報/` | 產業新聞+社群,一天一檔 | **每週一自動**(Gemini) |
| `01_潛水點/` | 季節/海況/能見度/看得到什麼/難度 | 持續補、定期覆核 |
| `02_課程證照/` | AIDA/PADI 課程、費用、報名資源 | 每週 |
| `03_技術裝備/` | 技術、訓練、裝備知識 | 每週 |
| `_pipeline/` | `fetch_brief.py` 抓取腳本 + `run.log` | — |

## 自動化架構(C 混合:倉管+顧問)

```
每週一 09:00
  └─ launchd 叫起 python3 fetch_brief.py
       ├─ 抓來源網頁(自由時報自潛版、EZDIVE…)
       ├─ 丟 Gemini 2.5 Flash 摘要(只根據原文,不腦補)
       └─ 寫入 00_每日簡報/YYYY-MM-DD.md
SS 要問問題時 → 換 Claude 讀本庫回答(準)
```

## 手動操作

```bash
# 立刻抓一次(寫檔)
python3 ~/SakiDiveDB/_pipeline/fetch_brief.py
# 只預覽不寫檔
python3 ~/SakiDiveDB/_pipeline/fetch_brief.py --dry
# 看排程狀態
launchctl list | grep sakidivedb
```

## 資料紀律(SS 要求「準確來源」)

1. 每筆事實附來源 `[來源](URL)` + `抓取:YYYY-MM-DD`
2. 時效性(價格/海況/季節/賽事)標 `⚠️需覆核`
3. 不確定就寫「未確認」,不腦補。寧缺勿錯。

## 憑證

- Gemini API key 在 `~/.config/gemini/credentials.json`(權限 600,不在 git 路徑)
- 洩漏時到 https://aistudio.google.com/apikey 重新產生

## 待決定 / 可調

- [ ] 主力地區:目前**沖繩**為樣本,可換/加(台灣小琉球、綠島、菲律賓…)
- [ ] 來源清單可增減:改 `_pipeline/fetch_brief.py` 的 `SOURCES`
- [ ] 要不要把 01/02/03 也自動化(目前只有 00 每日簡報自動)
