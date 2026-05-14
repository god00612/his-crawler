# his-query Skill

查詢成功醫院 HIS 系統的病人臨床資料，自動執行爬蟲並整理成結構化回答。

## 觸發時機

使用者問任何與住院病人相關的臨床問題，例如：
- 「MI02 現在用什麼升壓劑？」
- 「查一下 MI01 大夜的生命徵象」
- 「MI 病房有哪些病人？」
- 「MI03 今天的交班紀錄是什麼？」

## 查詢指令

```python
import subprocess, json

# 列出病房所有病人
result = subprocess.run(['python', 'his_query.py', '--ward', 'MI'], capture_output=True)
data = json.loads(result.stdout.decode('utf-8'))

# 查詢單一床位完整資料
result = subprocess.run(['python', 'his_query.py', '--bed', 'MI02'], capture_output=True)
data = json.loads(result.stdout.decode('utf-8'))

# 依班別篩選（大夜/小夜/白天）
result = subprocess.run(['python', 'his_query.py', '--bed', 'MI02', '--shift', '大夜'], capture_output=True)
data = json.loads(result.stdout.decode('utf-8'))
```

工作目錄：`D:\Users\YUAN\Desktop\his_crawler`

## 問題焦點對應欄位

| 使用者問題 | 要看的欄位 |
|---|---|
| 升壓劑 | `護理紀錄`（班務記錄類型）→ 搜尋 norepinephrine/升壓劑關鍵字；`用藥清單` 看有無開立 |
| 鎮靜劑 pump 速率 | `護理紀錄`（班務記錄）→ 搜尋 fentanyl/midazolam/ml/hr；**不要**看 `pump記錄`（通常為空） |
| 醫師交班紀錄 | `交班紀錄`（來自 `get_medSummary` API，欄位：時間/醫師/類型/班別/內容） |
| 護理紀錄 | `護理紀錄`（⚠️ 只含當班紀錄，不含完整住院史） |
| 生命徵象 | `生命徵象` |
| 驗血 / 抽血 / 檢驗 | `累積檢驗`（含生化/血液/血清/鏡檢/培養，每筆有 value/unit/ref/organ_system/abnormal） |
| 用藥 | `用藥清單` |
| SOFA | `SOFA分數` |

### API 對應速查（Chrome MCP 直接 fetch）

| 資料需求 | API | 備註 |
|---|---|---|
| 護理紀錄 | `get_nursing_records?visitNo=` | 回傳完整住院史，需自行篩選日期 |
| 醫師交班 | `get_medSummary?visitNo=` | 小資料，直接 await |
| 治療處置 | `patient_treatments?visitNo=` | 含呼吸補助費等 |
| 累積檢驗 | `query_cumulative_lab_data?visitNo=` | 大資料用 background pattern |
| 用藥清單 | `patient_drugs?visitNo=` | |
| 病房名單 | Chrome MCP UI → 攔截 `get_inPatient` | 無法用 visitNo 直接呼叫 |

### 累積檢驗欄位結構

每筆檢驗紀錄包含：

| 欄位 | 說明 |
|---|---|
| `organ_system` | 器官系統分類（見下方） |
| `item` | 項目名稱（來自 `ShortName`） |
| `value` | 數值（TranCode 9）或空字串（TranCode 8 培養） |
| `unit` | 單位 |
| `ref` | 參考值範圍 |
| `report` | 培養/鏡檢文字報告（TranCode 8）或空字串 |
| `abnormal` | `true`/`false` |
| `lab_date` | 報告日期 |

**器官系統分類**：心臟、腎臟、肝臟、胰臟、血液、凝血、感染、代謝、電解質、ABG、尿液、微生物、感染血清、甲狀腺、鐵代謝、內分泌、其他

### 檢驗結果呈現格式（兩段式）

1. **第一段：原始報告** — 按 HIS 分類（生化/血液/血清/ABG/尿液/微生物等）各一張表，列出**全部項目**（含正常值），異常加 ⚠。同一項目多次測量用「→」表示趨勢。
2. **第二段：器官系統彙整** — 只列**異常項目**，按器官系統歸類，文字描述趨勢。

### ⚠️ 重要提醒

- **Pump 速率（ml/hr）在護理「班務記錄」的自由文字裡**，格式例如 "FENTANYL 維持 0.5 ml/hr"。`pump記錄` 欄位通常回傳 0 筆，不要從那裡找。
- **`get_nursing_records?visitNo=` 回傳完整住院史**（非只有當班）。直接用 `?visitNo=` fetch 後自行篩選日期即可，不需 encrypted URL。
- **`交班紀錄` = 醫師交班**。是 `get_medSummary` API 的回傳，不是藥物摘要。

## Token 過期處理

症狀：查詢回傳 `"無法載入 XX 病房名單"`

### 自動修復流程（computer-use）

1. 用 PowerShell 找並啟動 HIS 桌面程式：
   ```powershell
   $exe = Get-ChildItem -Path "$env:LOCALAPPDATA\Apps\2.0" -Recurse -Filter "hisclient.cloudclient.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
   Start-Process $exe.FullName
   ```
2. 對 `hisclient.cloudclient.exe` 呼叫 `request_access`（full tier）
3. 截圖確認畫面後，點選左下角 **H 按鈕** → 選單出現 → 點 **住院電子病歷**
4. 導航路徑：**護理站** → 右側下拉選單選單位（如 MI）→ 選病人 → 右鍵 → **住院病人首頁**
5. HIS 網頁在 Edge 開啟後，截圖 URL bar，讀取 `su=XXXXXXXXXX`
6. 將數字寫入 `his_token.txt`（只寫數字，不含 `su=` 前綴）
7. 重新執行查詢

> **登入注意**：若 HIS 網頁顯示登入畫面，選**帳號密碼登入**（憑證登入會出現「憑證登人失敗」）。
> 帳密存放在環境變數 `HIS_USERNAME` / `HIS_PASSWORD`。

## Chrome MCP 直接查詢（首選方式）

當使用者的 HIS 網頁已開啟（`hapi.csh.org.tw`），優先用 Chrome MCP 的 `javascript_tool` 直接呼叫 API，**不需要開新的 Playwright 瀏覽器**，速度更快。

### 前置條件

1. Chrome MCP 已連接（`list_connected_browsers` 可看到瀏覽器）
2. 有一個 tab 在 `hapi.csh.org.tw` 網域（⚠️ 不是 `his.csh.org.tw`，會 CORS 失敗）
3. 已知病人的 `visitNo`（見下方取得方式）

### 取得病房名單與 visitNo（Chrome MCP UI 操作）

不需要 Playwright。直接在現有 HIS 瀏覽器操控 UI：

```
1. find "更換名單 select"  → form_input 選 "changePatientList"
2. find "單位 select"      → form_input 選 "CCU"（或其他病房）
3. find "確定名單 button"  → javascript_tool 點擊
4. read_network_requests urlPattern="get_inPatient"  → 取得 URL
5. javascript_tool fetch 該 URL → 解析 VisitNo / RoomBed / PtName
```

```javascript
// Step 4–5: 從 network 抓到 URL 後直接 fetch
window._ward = null;
fetch('https://hapi.csh.org.tw/get_inPatient?encrypted=...&nonce=...', {credentials:'include'})
  .then(r=>r.json()).then(d=>{window._ward=d;});
// 取得後
JSON.stringify(window._ward.map(p=>({bed:p.RoomBed, name:p.PtName, visitNo:p.VisitNo})));
```

### 已知 MI 病房 visitNo（快取，入院異動後用上方流程重新取得）

| 床位 | visitNo  | | 床位 | visitNo  |
|------|----------|-|------|----------|
| MI01 | 33958572 | | MI12 | 34360527 |
| MI02 | 34472131 | | MI13 | 34459974 |
| MI03 | 34450442 | | MI15 | 34307995 |
| MI05 | 34115258 | | MI18 | 34360938 |
| MI06 | 34332529 | | MI19 | 34468550 |
| MI07 | 34321454 | | MI20 | 34476508 |
| MI08 | 34360844 | | MI21 | 34199589 |
| MI09 | 34286021 | | MI22 | 34361865 |
| MI10 | 34349044 |
| MI11 | 34317627 |

### 查詢模式

#### 模式 A：選病人 → 一次攔截所有 API（單一病人完整資料，最推薦）

`form_input` 選病人後，HIS 自動發出 **22 個 API**，全部進 network log，想要哪個 fetch 哪個：

```
1. find "選擇病人 patient select"  → form_input 選 visitNo
2. read_network_requests（等 3–5 秒）→ 取得所有 URL
3. javascript_tool fetch 所需 URL
```

選病人後會出現的 22 個 API：

| API endpoint | 內容 |
|---|---|
| `patient_info` | 病人基本資料 |
| `visit_history` | 住院史 |
| `get_io` | 輸入輸出量 |
| `get_pump_records` | Pump 記錄 |
| `get_personal_note` | 個人備註 |
| `patient_treatments` | 治療處置（含呼吸補助費等） |
| `get_vital_sign` | 生命徵象 |
| `patient_body_record` | 體重體高 |
| `get_pre_admin_orders` | 預定醫囑 |
| `get_pharmacyReview_record` | 藥師審核 |
| `patient_orders` | 醫囑（兩次，不同範圍） |
| `get_medSummary` | 醫師交班紀錄 |
| `get_nursing_records` | 護理紀錄（當班） |
| `patient_problems` | 護理問題 |
| `med_allergy` | 藥物過敏 |
| `allergy_cloud_query` | 雲端過敏查詢 |
| `patient_drugs` | 用藥清單 |
| `query_cumulative_lab_data` | 累積檢驗 |

```javascript
// 攔截後，fetch 所需 URL（以護理紀錄為例）
window._nursing = null;
fetch('https://hapi.csh.org.tw/get_nursing_records?encrypted=...&nonce=...', {credentials:'include'})
  .then(r=>r.json()).then(d=>{window._nursing=d;});
// 等完成後讀取
window._nursing?.length
```

#### 模式 B：直接 fetch（已知 visitNo，優先採用）

支援 `?visitNo=` 的 API 直接呼叫，不需 UI 操作：
`get_nursing_records`、`get_medSummary`、`patient_treatments`、`patient_drugs`、`query_cumulative_lab_data`

```javascript
// 護理紀錄（完整住院史，篩選今日）
window._nursing = null;
fetch('https://hapi.csh.org.tw/get_nursing_records?visitNo=33726789', {credentials:'include'})
  .then(r=>r.json()).then(d=>{window._nursing=d;});
// 取得後篩選今日
window._nursing?.filter(r=>r.RecordTime.startsWith('2026-05-14'))
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))
  .map(r=>`[${r.RecordTime.slice(11,16)}] ${r.RecordTypeName} ${r.CreateUser}: ${r.Content.slice(0,200)}`);
```

```javascript
// 醫師交班（小，直接 await）
const data = await fetch('https://hapi.csh.org.tw/get_medSummary?visitNo=34360938',
  {credentials:'include'}).then(r=>r.json());
JSON.stringify(data.slice(-5));
```

#### 模式 C：全病房並行掃描（`Promise.all`，適合 `patient_treatments`）

```javascript
(async () => {
  const patients = [
    {bed:'MI01',visitNo:33958572},{bed:'MI02',visitNo:34472131},
    // ... 其他病人
  ];
  const results = await Promise.all(patients.map(async pt => {
    const data = await fetch(
      `https://hapi.csh.org.tw/patient_treatments?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());
    const hits = data.filter(item => item.ItemName?.includes('目標關鍵字'));
    return {bed: pt.bed, count: hits.length};
  }));
  window._scanResult = results;
})();
```

#### 大型資料（`query_cumulative_lab_data`）— background pattern

```javascript
// Step 1: 背景發送
window._lab = null;
fetch('https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=34360938',
  {credentials:'include'}).then(r=>r.json()).then(d=>{window._lab=d;});
'started';
// Step 2: 確認完成
window._lab ? window._lab.length + ' records' : 'still loading...';
// Step 3: 解析最新一天
(() => {
  const num = window._lab.filter(d=>d.TranCode==='9');
  const dates = [...new Set(num.map(d=>(d.LabDate||'').slice(0,10)))].filter(Boolean).sort().reverse();
  const latest = dates[0];
  return num.filter(d=>(d.LabDate||'').startsWith(latest))
    .map(d=>`${d.ShortName}: ${d.ReportValue} ${d.Unit}${d.IsAbnormal?' ⚠':''}`).join('\n');
})()
```

### 什麼時候還是要用 Playwright

| 情況 | 原因 |
|------|------|
| Chrome MCP 未連接 | fallback 到 `python his_query.py --bed MIxx` |

---

## PACS 影像查詢（在對話中顯示 X 光）

### 完整流程（三步驟）

**Step 1 — 取得影像清單**（需要 `chartno`，來自 `patient_info` 的 `ChartNo` 欄位）：

```javascript
// 在 hapi.csh.org.tw tab 執行
const studies = await fetch(
  'https://hapi.csh.org.tw/get_oracle_pacs_study_list?chartno=2937482',
  {credentials:'include'}).then(r=>r.json());
// 每筆包含：ACCESSION_NO, StudyDesc, StudyDateTime
JSON.stringify(studies.slice(0,5));
```

> ⚠️ 只能用 `chartno`，不能用 `visitNo`（API 會拒絕）。日期格式 `YYYY-MM-DD`，不是 `YYYYMMDD`。

**Step 2 — 取得 SOP instance UID**（需 `chartno` + `dt` 日期 YYYY-MM-DD）：

```javascript
const imgs = await fetch(
  'https://hapi.csh.org.tw/get_pacs_images?chartno=2937482&dt=2026-05-13',
  {credentials:'include'}).then(r=>r.json());
// 每筆包含 sop_instance_uid
imgs.map(i=>i.sop_instance_uid);
```

**Step 3 — 用 PowerShell 下載 JPEG（WADO 不需 cookie，直接可存）**：

```powershell
$uid = "1.2.392.200036.9107.307.35455.20260513.131829.1031420"
$url = "https://pacs.csh.org.tw/WebPush/WebPush.dll?PushWADO?requestType=WADO&contentType=image/jpeg&objectUID=$uid&rows=640"
$resp = Invoke-WebRequest -Uri $url -UseBasicParsing
[System.IO.File]::WriteAllBytes("D:\Users\YUAN\Desktop\his_crawler\tmp_cxr.jpg", $resp.Content)
```

然後用 `Read` tool 讀取 `tmp_cxr.jpg` → 影像直接顯示在對話裡。

> ⚠️ 從瀏覽器 fetch `pacs.csh.org.tw` 會被 CORS 擋住，**必須用 PowerShell**。

### ABG 注意事項

此 HIS 的 ABG SpecimenCode 是 `'BLD'`（不是 `BLDA` 或 `BLDV`）。
篩選 ABG 需用項目名稱關鍵字（pH、pCO2、pO2、HCO3、BE 等），不能單靠 SpecimenCode。

---

## 補充查詢（direct fetch）

在 Playwright 瀏覽器 session 中，可用 `page.evaluate` 直接呼叫 HIS API，複用 cookie 不需重新驗證：

```python
# 查醫師交班紀錄（需知道 visitNo）
url = "https://hapi.csh.org.tw/get_medSummary?visitNo=34472131"
resp = await page.evaluate(f"fetch('{url}', {{credentials:'include'}}).then(r=>r.json())")

# 查護理紀錄完整歷史（需從 HIS 頁面取得 encrypted URL）
url = "https://hapi.csh.org.tw/get_nursing_records?encrypted=...&nonce=..."
resp = await page.evaluate(f"fetch('{url}', {{credentials:'include'}}).then(r=>r.json())")
```

## 已知限制

- `--ward MI` 偶爾回傳 0 位病人（更換名單 UI flow 偶爾失敗，重試即可）
- `get_nursing_records` 只回傳**當班**護理紀錄
- `get_pump_records` 通常為空，pump 速率要從護理班務記錄的自由文字解讀

## Troubleshooting

### 查詢結果某個欄位全部空白

**症狀**：例如 `累積檢驗` 裡 item / report 全是空字串，但筆數不是 0。

**診斷**：API 回傳了多種 JSON 結構，parser 只對應其中一種欄位名稱。

**步驟**：
1. 寫一個小腳本攔截原始 API response，印出前幾筆的所有 key：
   ```python
   # dump_lab_raw.py — 參考 his_crawler/ 目錄下的版本
   async def handle(resp):
       if "目標endpoint" in resp.url:
           raw = await resp.json()
           data = raw.get("data", raw)
           for item in (data if isinstance(data, list) else [data])[:3]:
               print(json.dumps(item, ensure_ascii=False))
   ```
2. 比對 `clinical_service.py` 裡對應的 `_parse_*` 函式用的欄位名稱
3. 依實際欄位名稱補上分支處理

**已知案例**：`query_cumulative_lab_data` 混了兩種結構：
- `TranCode:"8"` 培養類 → 用 `Item` + `ReportText`
- `TranCode:"9"` 數值類 → 用 `ShortName` + `ReportValue` + `Unit` + `IsAbnormal`

---

### 器官系統顯示「其他」

**症狀**：檢驗項目的 `organ_system` 欄位顯示 `"其他"`，未被正確分類。

**診斷**：`ORGAN_SYSTEM_EXACT`（精確名稱匹配）或 `ORGAN_SYSTEM_PATTERNS`（關鍵字子字串匹配）未覆蓋到該項目名稱。

**步驟**：
1. 確認項目的 `ShortName` 實際字串（注意大小寫、空格、連字號）
2. 在 `clinical_service.py` 的 `ORGAN_SYSTEM_EXACT` 或 `ORGAN_SYSTEM_PATTERNS` 加入對應規則
3. 關鍵字比對已全部轉小寫（`name.lower()`），新增 pattern 也用小寫

**已知易誤判**：
- `A.P.T.T.`（凝血）— 縮寫含點號，需明確加入 pattern
- `BE (Vein)` / `pH (Vein)` — 後綴括號，不能用精確匹配，用子字串
- `Free-T4`（甲狀腺）— 連字號版本需與空格版本都列入

---

### 查詢回傳 0 筆但預期有資料

**可能原因與對策**：

| 現象 | 可能原因 | 對策 |
|---|---|---|
| `"無法載入 XX 病房名單"` | Token 過期 | 見上方 Token 過期處理 |
| 護理紀錄只有 2–3 筆 | 用了 encrypted URL（只給當班） | 改用 `get_nursing_records?visitNo=` 取完整住院史，再篩日期 |
| pump 記錄 0 筆 | `get_pump_records` 通常為空 | 改找護理班務記錄自由文字 |
| `--ward` 回傳 0 病人 | 更換名單 UI flow 偶發失敗 | 重新執行一次 |

---

### stderr 出現 `radio 失敗: Element is outside of the viewport`

**說明**：更換名單時護理站 radio button 不在可視範圍，但後續程式有 fallback（直接選單位），**不影響查詢結果**，可忽略。
