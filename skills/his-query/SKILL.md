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

## 核心查詢邏輯

**先廣後深**：大多數臨床問題，從護理紀錄 + 醫師交班就能得到足夠的答案。只有在需要更精確的數值或結構化資料時，才去打對應的專屬 API。

```
使用者問題
  │
  ▼
【第一層】護理紀錄 + 醫師交班（廣，通常夠用）
  get_nursing_records?visitNo=   → 發生了什麼事、做了什麼處置、pump 速率、灌食
  get_medSummary?visitNo=        → 醫師的判斷、治療計劃、病情變化摘要
  │
  ├─ 答案已足夠 → 直接回答
  │
  └─ 需要更精確 → 【第二層】專屬 API（精，結構化）
       檢驗數值  → query_cumulative_lab_data
       用藥清單  → patient_drugs
       治療處置  → patient_treatments
       影像      → PACS 流程
       IO        → get_io
```

**實際應用**：
- 「MI06 小夜班發生什麼事？」→ 直接查護理紀錄篩小夜班，通常不需再打其他 API
- 「MI06 用了什麼抗生素？」→ 護理班務記錄會提到藥名，若需要劑量/頻次才去查 `patient_drugs`
- 「MI05 有長菌嗎？」→ 護理紀錄可能提到培養結果，若要完整藥敏報告才去查 `query_cumulative_lab_data`

---

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

## 臨床情境 SOP

常見臨床問題的標準查詢流程。每個情境列出：需要哪些 API、怎麼篩選、怎麼呈現。

---

### 情境一：CXR 比較（最新兩張）

**目標**：取得病人最近兩次胸部 X 光，依序呈現在對話中供比較。

**步驟**：

1. 取得 `chartno`：
   ```javascript
   const info = await fetch('https://hapi.csh.org.tw/patient_info?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   const chartno = info.ChartNo;
   ```

2. 取影像清單，篩出最近兩次 CXR：
   ```javascript
   const studies = await fetch(
     `https://hapi.csh.org.tw/get_oracle_pacs_study_list?chartno=${chartno}`,
     {credentials:'include'}).then(r=>r.json());
   // 識別 CXR：StudyDesc 含 "Chest"、"CXR"、"胸部"
   const cxrs = studies
     .filter(s => /chest|cxr|胸部/i.test(s.StudyDesc))
     .sort((a,b) => b.StudyDateTime.localeCompare(a.StudyDateTime))
     .slice(0, 2);
   JSON.stringify(cxrs.map(s=>({date:s.StudyDateTime.slice(0,10), desc:s.StudyDesc})));
   ```

3. 各取一個 `sop_instance_uid`（對兩個日期各執行一次）：
   ```javascript
   const imgs = await fetch(
     `https://hapi.csh.org.tw/get_pacs_images?chartno=${chartno}&dt=2026-05-13`,
     {credentials:'include'}).then(r=>r.json());
   imgs[0].sop_instance_uid
   ```

4. PowerShell 下載兩張 JPEG（WADO 不需 cookie）：
   ```powershell
   foreach ($i in 1..2) {
     $uid = "..."  # 各自的 sop_instance_uid
     $url = "https://pacs.csh.org.tw/WebPush/WebPush.dll?PushWADO?requestType=WADO&contentType=image/jpeg&objectUID=$uid&rows=640"
     $resp = Invoke-WebRequest -Uri $url -UseBasicParsing
     [System.IO.File]::WriteAllBytes("D:\Users\YUAN\Desktop\his_crawler\tmp_cxr_$i.jpg", $resp.Content)
   }
   ```

5. 用 `Read` tool 依序讀取 `tmp_cxr_1.jpg`、`tmp_cxr_2.jpg`，各加上日期與 StudyDesc 說明。

**呈現格式**：
```
【第一張】2026-05-11 Chest AP
[影像]

【第二張】2026-05-13 Chest AP
[影像]
```

---

### 情境二：升壓劑 / 鎮靜劑現況

**目標**：知道病人目前使用什麼升壓劑和鎮靜劑、速率多少、最後記錄時間。

**步驟**：

1. 取今日護理班務記錄：
   ```javascript
   window._nursing = null;
   fetch('https://hapi.csh.org.tw/get_nursing_records?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json()).then(d=>{window._nursing=d;});
   // 等完成後篩今日班務記錄
   (() => {
     const today = '2026-05-14';
     return window._nursing
       .filter(r => r.RecordTime.startsWith(today) && r.RecordTypeName?.includes('班務'))
       .sort((a,b) => b.RecordTime.localeCompare(a.RecordTime))
       .map(r => `[${r.RecordTime.slice(11,16)}] ${r.Content}`);
   })()
   ```

2. 在回傳的自由文字中搜尋關鍵字：

   | 類型 | 關鍵字 |
   |---|---|
   | 升壓劑 | norepinephrine、epinephrine、dopamine、vasopressin、levophed |
   | 鎮靜劑 | fentanyl、midazolam、propofol、dexmedetomidine、precedex |
   | 速率格式 | `X ml/hr`、`X mcg/kg/min` |

3. 交叉確認有無開立（看 `patient_drugs?visitNo=`）。

**呈現格式**：
```
升壓劑：
  Norepinephrine 5 ml/hr（記錄於 14:30）

鎮靜劑：
  Fentanyl 2 ml/hr（記錄於 14:30）
  Midazolam 3 ml/hr（記錄於 14:30）
```

> ⚠️ pump 速率在護理**班務記錄**自由文字，`get_pump_records` 通常為空。

---

### 情境三：培養菌種

**目標**：確認病人有無培養出菌、菌種為何、藥敏結果。

**步驟**：

1. 取累積檢驗，篩培養類（TranCode:"8"）：
   ```javascript
   window._lab = null;
   fetch('https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json()).then(d=>{window._lab=d;});
   // 等完成後篩培養
   (() => {
     const cultures = window._lab.filter(d => d.TranCode === '8');
     return cultures.map(d => ({
       date: (d.LabDate||'').slice(0,10),
       specimen: d.SpecimenCode,
       item: d.Item,
       report: d.ReportText,
       abnormal: d.IsAbnormal
     }));
   })()
   ```

2. 按檢體分類：

   | SpecimenCode | 檢體 |
   |---|---|
   | BLD | 血液 |
   | UR | 尿液 |
   | SPT | 痰液 |
   | 其他 | 依 Item 名稱判斷 |

3. 判斷有無長菌：`ReportText` 含菌種名稱（非 "No growth" / "陰性"）即為陽性。

**呈現格式**：
```
血液培養（2026-05-10）：No growth
痰液培養（2026-05-12）：Klebsiella pneumoniae ⚠
  藥敏報告：[ReportText 完整內容]
尿液培養：本次住院無送檢紀錄
```

---

### 情境四：醫師治療計劃

**目標**：了解醫師目前的治療方向與計劃。

**步驟**：

1. 取醫師交班紀錄（目前最接近 progress note 的資料）：
   ```javascript
   const data = await fetch('https://hapi.csh.org.tw/get_medSummary?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   // 最近 3 筆
   JSON.stringify(data.slice(-3).map(d=>({
     time: d.RecordTime,
     type: d.ProgressType,
     doctor: d.RecordUser,
     content: d.Summary
   })));
   ```

**呈現格式**：
```
【2026-05-14 08:00】主治醫師 王OO（白班交班）
[Summary 內容]

【2026-05-13 20:00】主治醫師 王OO（小夜交班）
[Summary 內容]
```

> ⚠️ **待確認**：`get_medSummary` 是交班紀錄，不一定等於完整治療計劃。回醫院後確認是否有更接近 progress note 的 API。

---

### 情境五：生化檢驗

**目標**：呈現病人最新一次（或近幾天）的生化檢驗結果，異常項目標記。

**步驟**：

1. 取累積檢驗，篩數值類（TranCode:"9"）：
   ```javascript
   window._lab = null;
   fetch('https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json()).then(d=>{window._lab=d;});
   // 等完成後解析
   (() => {
     const num = window._lab.filter(d => d.TranCode === '9');
     // 生化關鍵字
     const bioKeywords = ['ALT','AST','Bil','T-Bil','D-Bil','Cr','BUN','Na','K','Cl','Ca','Mg','P',
       'Glucose','AC','PC','Albumin','TP','ALP','GGT','LDH','Amylase','Lipase','Uric'];
     const bio = num.filter(d => bioKeywords.some(k => d.ShortName?.includes(k)));
     // 取最新日期
     const dates = [...new Set(bio.map(d=>(d.LabDate||'').slice(0,10)))].filter(Boolean).sort().reverse();
     const latest = dates[0];
     return bio.filter(d=>(d.LabDate||'').startsWith(latest))
       .map(d=>`${d.ShortName}: ${d.ReportValue} ${d.Unit} [${d.RefRange||''}]${d.IsAbnormal?' ⚠':''}`)
       .join('\n');
   })()
   ```

2. 若需要近幾天趨勢，改取最近 N 天日期，每個項目列出多個時間點的值。

**生化項目分類**：

| 類別 | 項目 |
|---|---|
| 肝功能 | ALT、AST、T-Bil、D-Bil、ALP、GGT、Albumin、TP |
| 腎功能 | Cr、BUN、Uric Acid |
| 電解質 | Na、K、Cl、Ca、Mg、P |
| 血糖 | Glucose、AC、PC、HbA1c |
| 胰臟 | Amylase、Lipase |
| 其他 | LDH |

**呈現格式**：
```
生化檢驗（2026-05-14）

肝功能：
  ALT: 45 U/L [≤40] ⚠
  AST: 38 U/L [≤40]
  T-Bil: 1.2 mg/dL [≤1.5]

腎功能：
  Cr: 2.1 mg/dL [0.6–1.2] ⚠
  BUN: 35 mg/dL [8–25] ⚠

電解質：
  Na: 138 mEq/L [136–145]
  K: 3.8 mEq/L [3.5–5.0]
```

---

### 情境六：檢查報告（影像文字報告）

> ⚠️ **待確認**：目前 PACS 流程只能取得影像 JPEG，放射科醫師的文字報告（impression/findings）尚不知道從哪個 API 取得。

**已知**：
- `get_oracle_pacs_study_list` 回傳的 `StudyDesc` 只是檢查名稱，不含報告內容
- WADO 只提供影像檔

**待確認事項**（回醫院後）：
- HIS 裡點開影像報告時，瀏覽器發出哪個 API request？
- 回傳格式是什麼（純文字 / JSON / PDF）？
- 是否用 `chartno` 或 `ACCESSION_NO` 查詢？

---

### 情境七：IO（輸入 / 輸出量）

> ⚠️ **待確認**：`get_io` API 存在於 22 個自動觸發的 API 清單中，但尚未確認：
> - 是否支援 `?visitNo=` 直接查詢
> - 回傳的欄位結構為何
> - 時間範圍怎麼篩（日期 / 班別）

**暫用方法**（模式 A，選病人後攔截）：

```
1. form_input 選病人 visitNo → HIS 自動發出 get_io
2. read_network_requests 取得 get_io 的完整 URL
3. javascript_tool fetch 該 URL → 檢視回傳結構
```

回醫院確認後補上完整 SOP 與欄位說明。

---

### 情境八：入院經過 / 入 ICU 經過 / 手術紀錄

**目標**：整理病人為何入院、為何入 ICU、本次住院進行了哪些手術。

**步驟**：

1. 取基本資料（入院診斷、入院日期）：
   ```javascript
   const info = await fetch('https://hapi.csh.org.tw/patient_info?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   // 關注欄位：AdmDiagnosis、AdmDate、ChartNo、PtName
   JSON.stringify({
     name: info.PtName,
     admDate: info.AdmDate,
     diagnosis: info.AdmDiagnosis
   });
   ```

2. 取醫師交班紀錄，找最早幾筆（入院/入ICU時的紀錄）與最近紀錄：
   ```javascript
   const summary = await fetch('https://hapi.csh.org.tw/get_medSummary?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   // 最早 3 筆（入科經過）+ 最近 3 筆（目前狀況）
   JSON.stringify({
     early: summary.slice(0, 3),
     recent: summary.slice(-3)
   });
   ```

3. 手術相關資料 — 查 `patient_treatments`，篩手術/OR 相關項目：
   ```javascript
   const tx = await fetch('https://hapi.csh.org.tw/patient_treatments?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   // 篩手術相關費用項目（關鍵字：手術、OR、麻醉、刀）
   tx.filter(t => /手術|OR|麻醉|刀|開刀/.test(t.ItemName||''));
   ```

> ⚠️ **待確認**：正式手術紀錄（術式、術者、麻醉方式）可能在獨立的手術系統，不在上述 API 中。回醫院後確認：選病人後 network log 是否有手術相關 API（如 `get_operation`、`or_record` 等）。

**呈現格式**：
```
病人：王OO，入院日期：2026-05-10
入院診斷：Septic shock, pneumonia

入ICU經過（最早交班）：
  [2026-05-10 10:00] 因發燒、血壓下降由急診收入 MICU...

目前狀況（最近交班）：
  [2026-05-14 08:00] 血壓穩定，升壓劑逐漸減量...

手術紀錄：
  [2026-05-12] 氣管切開術（tracheostomy）
```

---

### 情境九：特定班別發生了什麼事

**目標**：快速了解某班別（大夜/小夜/白天）期間的重要事件，包含病情變化、緊急處置、醫師指示。

**時間區間**：
| 班別 | 時間 |
|---|---|
| 白天 | 07:00–15:00 |
| 小夜 | 15:00–23:00 |
| 大夜 | 23:00–07:00（跨午夜） |

**步驟**：

1. 取護理紀錄，篩指定班別時間：
   ```javascript
   window._nursing = null;
   fetch('https://hapi.csh.org.tw/get_nursing_records?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json()).then(d=>{window._nursing=d;});
   // 大夜班（昨晚23:00 ~ 今日07:00）
   (() => {
     const from = '2026-05-13T23:00';
     const to   = '2026-05-14T07:00';
     return window._nursing
       .filter(r => r.RecordTime >= from && r.RecordTime <= to)
       .sort((a,b) => a.RecordTime.localeCompare(b.RecordTime))
       .map(r => `[${r.RecordTime.slice(11,16)}] ${r.RecordTypeName} ${r.CreateUser}: ${r.Content}`);
   })()
   ```

2. 取該班的醫師交班紀錄：
   ```javascript
   const summary = await fetch('https://hapi.csh.org.tw/get_medSummary?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   // 篩大夜班交班（ShiftType 含「大夜」）
   summary.filter(s => s.ShiftType?.includes('大夜'))
     .map(s => `[${s.RecordTime}] ${s.RecordUser}: ${s.Summary}`);
   ```

3. 整合兩個資料來源，依時間排序呈現。

**呈現格式**：
```
MI03 大夜班（2026-05-13 23:00 ~ 2026-05-14 07:00）

護理紀錄：
  [23:15] 班務記錄 護士A：血壓下降至 80/50，通知值班醫師...
  [23:30] 緊急處置 護士A：開始 norepinephrine 0.1 mcg/kg/min...
  [02:00] 班務記錄 護士B：血壓回穩 110/70，升壓劑維持...
  [06:00] 班務記錄 護士B：生命徵象穩定，準備交班...

醫師大夜交班：
  [07:00] 主治醫師 陳OO：昨晚血壓不穩，給予補液及升壓劑後改善...
```

---

### 情境十：用病歷號（chartno）查病人目前所在單位

**目標**：已知病歷號，不知道病人目前在哪個病房/單位。

> ⚠️ **待確認**：目前不知道是否有 `search_patient?chartno=` 類型的 API。

**待確認事項**（回醫院後）：
- 在 HIS 搜尋框輸入病歷號時，瀏覽器發出哪個 API？
- 回傳內容是否包含目前所在病房（Ward、RoomBed）？
- 是否可以用 `chartno` 直接取得目前的 `visitNo`？

**暫用方法**：
- 如果知道病人大約在哪個病房，用該病房名單（`get_inPatient`）掃描 `ChartNo` 欄位比對

---

### 情境十一：查特定醫囑是否開立（order code）

**目標**：確認病人是否有開立特定醫囑，例如呼吸器相關 order（代碼 57001）。

> ⚠️ **待確認**：`patient_orders` API 是否支援 `?visitNo=` 直接查，以及欄位結構。

**待確認事項**（回醫院後）：
- 選病人後攔截 `patient_orders` URL → 確認是否可直接用 `?visitNo=`
- 回傳欄位中醫囑代碼的欄位名稱（`OrderCode`？`ItemCode`？`OrderNo`？）
- 篩選方式：用代碼精確比對，或用醫囑名稱關鍵字搜尋

**暫用方向**：
```javascript
// 取 patient_orders（URL 從 network log 攔截）
const orders = await fetch('https://hapi.csh.org.tw/patient_orders?encrypted=...&nonce=...',
  {credentials:'include'}).then(r=>r.json());
// 篩特定代碼或名稱
orders.filter(o => o.OrderCode === '57001' || o.OrderName?.includes('呼吸器'));
```

---

### 情境十二：腦部影像（Brain CT / MRI）

**目標**：取得病人最近的 Brain CT 或 MRI 影像（及文字報告，如可取得）。

**影像部分**（流程同 CXR，已確認可行）：

1. 取 `chartno`（從 `patient_info`）
2. 取影像清單，篩 Brain CT / MRI：
   ```javascript
   const studies = await fetch(
     `https://hapi.csh.org.tw/get_oracle_pacs_study_list?chartno=${chartno}`,
     {credentials:'include'}).then(r=>r.json());
   // 識別關鍵字：Brain、Head、CT、MRI、頭部、顱
   const brain = studies
     .filter(s => /brain|head|MRI|CT brain|頭部|顱/i.test(s.StudyDesc))
     .sort((a,b) => b.StudyDateTime.localeCompare(a.StudyDateTime));
   JSON.stringify(brain.slice(0,3).map(s=>({date:s.StudyDateTime.slice(0,10), desc:s.StudyDesc, accNo:s.ACCESSION_NO})));
   ```
3. 取 `sop_instance_uid` → PowerShell WADO 下載 → `Read` tool 呈現（同 CXR 流程）

**文字報告部分**：
> ⚠️ **待確認**（同情境六）：放射科文字報告（findings / impression）的 API 尚未找到。回醫院後在 HIS 點開報告時攔截 network request 確認。

**呈現格式**：
```
Brain CT（2026-05-12）
[影像]

Brain MRI（2026-05-10）
[影像]

放射科報告：⚠️ 待確認 API
```

---

### 情境十三：全病房掃描 — 呼吸器病人 + 最近 CXR

**目標**：一次列出整個病房所有使用呼吸器的病人，並附上各人最近一張 CXR。

**步驟**：

**Phase 1 — 取病房名單與 visitNo**（Chrome MCP UI 操作或用快取表）

**Phase 2 — 並行掃描，篩呼吸器病人**：
```javascript
(async () => {
  const patients = [
    {bed:'MI01',visitNo:33958572,name:'王OO'},
    // ... 其他病人
  ];
  const results = await Promise.all(patients.map(async pt => {
    const data = await fetch(
      `https://hapi.csh.org.tw/patient_treatments?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());
    const hasVent = data.some(t =>
      t.ItemName?.includes('呼吸補助使用費') || t.ItemName?.includes('呼吸器'));
    return {...pt, hasVent};
  }));
  window._ventPts = results.filter(r => r.hasVent);
  return window._ventPts.map(p => p.bed + ' ' + p.name);
})();
```

**Phase 3 — 取各呼吸器病人的 chartno**：
```javascript
(async () => {
  const infos = await Promise.all(window._ventPts.map(async pt => {
    const info = await fetch(
      `https://hapi.csh.org.tw/patient_info?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());
    return {...pt, chartno: info.ChartNo};
  }));
  window._ventPts = infos;
  return infos.map(p => `${p.bed} chartno=${p.chartno}`);
})();
```

**Phase 4 — 取各人最近 CXR 的 sop_instance_uid**：
```javascript
(async () => {
  const cxrList = await Promise.all(window._ventPts.map(async pt => {
    const studies = await fetch(
      `https://hapi.csh.org.tw/get_oracle_pacs_study_list?chartno=${pt.chartno}`,
      {credentials:'include'}).then(r=>r.json());
    const latest = studies
      .filter(s => /chest|cxr|胸部/i.test(s.StudyDesc))
      .sort((a,b) => b.StudyDateTime.localeCompare(a.StudyDateTime))[0];
    if (!latest) return {...pt, uid: null};
    const imgs = await fetch(
      `https://hapi.csh.org.tw/get_pacs_images?chartno=${pt.chartno}&dt=${latest.StudyDateTime.slice(0,10)}`,
      {credentials:'include'}).then(r=>r.json());
    return {...pt, cxrDate: latest.StudyDateTime.slice(0,10), uid: imgs[0]?.sop_instance_uid};
  }));
  window._cxrList = cxrList.filter(p => p.uid);
  return window._cxrList.map(p => `${p.bed} ${p.cxrDate} ${p.uid}`);
})();
```

**Phase 5 — PowerShell 下載所有 CXR（根據 Phase 4 輸出的 uid 清單）**：
```powershell
$patients = @(
  @{bed="MI01"; uid="1.2.392..."},
  @{bed="MI03"; uid="1.2.392..."}
)
foreach ($pt in $patients) {
  $url = "https://pacs.csh.org.tw/WebPush/WebPush.dll?PushWADO?requestType=WADO&contentType=image/jpeg&objectUID=$($pt.uid)&rows=640"
  $resp = Invoke-WebRequest -Uri $url -UseBasicParsing
  [System.IO.File]::WriteAllBytes("D:\Users\YUAN\Desktop\his_crawler\tmp_cxr_$($pt.bed).jpg", $resp.Content)
}
```

**Phase 6 — 逐一 `Read` tool 呈現**，每張標上床位與日期。

---

### 情境十四：全病房掃描 — 特定呼吸支持模式（BIPAP / HFNC）

**目標**：列出病房中使用 BIPAP（57023）或 HFNC（57030、57031）的病人。

**掃描方式**（同情境十三 Phase 1–2，改篩條件）：

```javascript
(async () => {
  const patients = [
    {bed:'CCU01', visitNo:XXXXXXXX},
    // ... CCU 全部病人
  ];
  const TARGET_CODES = ['57023', '57030', '57031'];
  const TARGET_NAMES = ['BIPAP', 'HFNC', '高流量'];

  const results = await Promise.all(patients.map(async pt => {
    const data = await fetch(
      `https://hapi.csh.org.tw/patient_treatments?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());
    const hits = data.filter(t =>
      TARGET_CODES.some(c => t.ItemCode === c) ||
      TARGET_NAMES.some(n => t.ItemName?.includes(n))
    );
    return {...pt, hits: hits.map(t=>t.ItemName)};
  }));

  return results
    .filter(r => r.hits.length > 0)
    .map(r => `${r.bed}: ${r.hits.join(', ')}`);
})();
```

> ⚠️ **待確認**：57023（BIPAP）、57030、57031（HFNC）是否在 `patient_treatments` 中，還是在 `patient_orders` 中？回醫院後用模式 A（選病人攔截）確認這些代碼出現在哪個 API、欄位名稱為何（`ItemCode`？`OrderCode`？）。

**呈現格式**：
```
CCU 呼吸支持現況（2026-05-14）

使用呼吸器：
  CCU01 王OO、CCU05 李OO

使用 BIPAP（57023）：
  CCU03 張OO

使用 HFNC（57030/57031）：
  CCU07 陳OO、CCU09 林OO
```

---

### 情境十五：特定班別的特殊處置

**目標**：找出某班別期間護理記錄中的「處置」類型紀錄（與情境九的差別：情境九看全部事件，這裡只看處置）。

**步驟**：

```javascript
window._nursing = null;
fetch('https://hapi.csh.org.tw/get_nursing_records?visitNo=XXXXXXXX',
  {credentials:'include'}).then(r=>r.json()).then(d=>{window._nursing=d;});

// 小夜班（15:00–23:00）處置紀錄
(() => {
  const from = '2026-05-14T15:00';
  const to   = '2026-05-14T23:00';
  return window._nursing
    .filter(r =>
      r.RecordTime >= from && r.RecordTime <= to &&
      (r.RecordTypeName?.includes('處置') || r.RecordTypeName?.includes('治療'))
    )
    .sort((a,b) => a.RecordTime.localeCompare(b.RecordTime))
    .map(r => `[${r.RecordTime.slice(11,16)}] ${r.RecordTypeName} ${r.CreateUser}: ${r.Content}`);
})()
```

> 若要看全部事件（不限處置），參考情境九。

**呈現格式**：
```
MI06 小夜班特殊處置（2026-05-14 15:00–23:00）

  [16:30] 處置 護士A：抽血送培養（血液 × 2 套）
  [18:00] 治療 護士A：更換中央靜脈導管敷料
  [21:00] 處置 護士B：插入鼻胃管，確認位置後開始灌食
```

---

### 情境十六：檢驗趨勢圖（matplotlib）

**目標**：將多天的檢驗數值（如腎功能、電解質）繪製成折線圖，在對話中顯示。

**適用場景**：近 N 天/兩周/一個月的趨勢分析。

**步驟**：

1. Chrome MCP 取原始資料（以腎功能為例）：
   ```javascript
   window._lab = null;
   fetch('https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json()).then(d=>{window._lab=d;});
   // 等完成後，篩腎功能項目
   (() => {
     const renal = ['Cr', 'BUN', 'Na', 'K', 'Cl', 'HCO3', 'Uric'];
     return JSON.stringify(
       window._lab
         .filter(d => d.TranCode==='9' && renal.some(k => d.ShortName?.includes(k)))
         .map(d => ({date: (d.LabDate||'').slice(0,10), item: d.ShortName,
                     value: parseFloat(d.ReportValue), unit: d.Unit, abnormal: d.IsAbnormal}))
     );
   })()
   ```

2. 將資料貼入 Python，用 matplotlib 生成圖表：
   ```python
   import json, matplotlib
   matplotlib.use('Agg')
   import matplotlib.pyplot as plt
   import matplotlib.dates as mdates
   from datetime import datetime
   
   # 貼上步驟1的 JSON 輸出
   raw = json.loads('...')
   
   # 分項目整理
   items = {}
   for r in raw:
       items.setdefault(r['item'], []).append((r['date'], r['value']))
   
   fig, axes = plt.subplots(len(items), 1, figsize=(10, 3*len(items)), sharex=True)
   if len(items) == 1: axes = [axes]
   
   for ax, (item, pts) in zip(axes, items.items()):
       pts.sort()
       dates = [datetime.strptime(d, '%Y-%m-%d') for d, _ in pts]
       vals  = [v for _, v in pts]
       ax.plot(dates, vals, 'o-', linewidth=2)
       ax.set_ylabel(item)
       ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
       ax.grid(True, alpha=0.3)
   
   plt.suptitle('腎功能趨勢（近兩周）', fontsize=14)
   plt.tight_layout()
   plt.savefig(r'D:\Users\YUAN\Desktop\his_crawler\tmp_trend.png', dpi=120)
   plt.close()
   print('done')
   ```

3. 用 `Read` tool 讀取 `tmp_trend.png` → 圖表顯示在對話中。

**可繪製的趨勢組合**：

| 主題 | 項目 |
|---|---|
| 腎功能 | Cr、BUN |
| 電解質 | Na、K、Cl、HCO3（或 CO2） |
| 肝功能 | ALT、AST、T-Bil |
| 感染指標 | WBC、CRP、PCT |
| ABG | pH、pCO2、pO2、HCO3、BE |

> 若需要中文字型（項目名稱有中文），需先確認系統有安裝中文字型，或改用英文縮寫。

---

### 情境十七：心導管（CATH）報告

**目標**：取得心導管檢查的影像與文字報告。

**影像部分**（流程同 CXR，已確認可行）：

1. 取 `chartno`（從 `patient_info`）
2. 取影像清單，篩心導管相關：
   ```javascript
   const studies = await fetch(
     `https://hapi.csh.org.tw/get_oracle_pacs_study_list?chartno=${chartno}`,
     {credentials:'include'}).then(r=>r.json());
   // 識別關鍵字：CATH、Coronary、Angio、心導管、冠狀動脈
   const cath = studies
     .filter(s => /cath|coronary|angio|心導管|冠狀/i.test(s.StudyDesc))
     .sort((a,b) => b.StudyDateTime.localeCompare(a.StudyDateTime));
   JSON.stringify(cath.slice(0,3).map(s=>({date:s.StudyDateTime.slice(0,10), desc:s.StudyDesc})));
   ```
3. 取 `sop_instance_uid` → PowerShell WADO 下載 → `Read` tool 呈現

**文字報告（心導管結果、病灶描述）**：
> ⚠️ **待確認**（同情境六）：放射科/心臟科文字報告 API 尚未找到。回醫院後攔截 network 確認。

---

### 情境十八：全病房 CXR 影像判讀篩查（氣胸等）

**目標**：下載全病房最新 CXR，由 Claude 視覺判讀，回報有異常發現的病人。

> ⚠️ **重要聲明**：Claude 的影像判讀為**輔助篩查**，不能取代放射科醫師診斷。發現可疑異常時，需請醫師確認。

**步驟**：

**Phase 1–4**：同情境十三（全病房掃描取 CXR），取得所有病人的 `sop_instance_uid`

**Phase 5 — PowerShell 批次下載**：
```powershell
$cxrList = @(
  @{bed="MI01"; uid="1.2.392..."},
  @{bed="MI03"; uid="1.2.392..."}
  # ... 從 Phase 4 輸出填入
)
foreach ($pt in $cxrList) {
  $url = "https://pacs.csh.org.tw/WebPush/WebPush.dll?PushWADO?requestType=WADO&contentType=image/jpeg&objectUID=$($pt.uid)&rows=640"
  $resp = Invoke-WebRequest -Uri $url -UseBasicParsing
  [System.IO.File]::WriteAllBytes(
    "D:\Users\YUAN\Desktop\his_crawler\tmp_cxr_$($pt.bed).jpg", $resp.Content)
}
```

**Phase 6 — Claude 逐張判讀**：
用 `Read` tool 讀取每張 JPG，描述：
- 有無氣胸跡象（肺紋消失、胸膜線、縱膈移位）
- 有無大量肋膜積液
- 其他明顯異常（新發浸潤、心臟擴大等）

**呈現格式**：
```
MI 病房 CXR 篩查（2026-05-14）

MI01 王OO [2026-05-13]：
  ⚠️ 左側疑似氣胸——左上肺野肺紋稀少，建議確認

MI03 張OO [2026-05-14]：
  兩側浸潤，無明顯氣胸

MI05 李OO [2026-05-12]：
  右側少量肋膜積液，無氣胸

---
⚠️ 以上為 AI 輔助篩查，請醫師確認後判讀。
```

**常見氣胸判讀線索**：
- 肺紋（lung markings）消失的區域
- 可見胸膜線（visceral pleural line）
- 縱膈向對側移位（張力性氣胸）
- 單側肺野透光度明顯增加

---

### 情境十九：目前使用的抗生素

**目標**：列出病人目前開立中的抗生素，包含藥名、劑量、給藥頻次。

**步驟**：

```javascript
const drugs = await fetch('https://hapi.csh.org.tw/patient_drugs?visitNo=XXXXXXXX',
  {credentials:'include'}).then(r=>r.json());

// 抗生素關鍵字（可擴充）
const abxKeywords = [
  'vancomycin','meropenem','imipenem','ertapenem',
  'piperacillin','tazobactam','cefazolin','ceftriaxone','cefepime',
  'levofloxacin','ciprofloxacin','azithromycin','clindamycin',
  'metronidazole','fluconazole','micafungin','amphotericin',
  'colistin','tigecycline','linezolid','daptomycin','rifampin',
  'trimethoprim','sulfamethoxazole','TMP','SMX'
];

const abx = drugs.filter(d =>
  abxKeywords.some(k => (d.DrugName||d.OrderName||'').toLowerCase().includes(k.toLowerCase()))
);
JSON.stringify(abx.map(d=>({
  drug: d.DrugName || d.OrderName,
  dose: d.Dose,
  route: d.Route,
  freq: d.Frequency,
  startDate: d.StartDate
})));
```

> ⚠️ **待確認**：`patient_drugs` 的欄位名稱（`DrugName`？`OrderName`？`Dose`？）需回醫院實際查看確認。

**呈現格式**：
```
MI06 目前抗生素（2026-05-14）

  Vancomycin 1g IV Q12H（自 2026-05-10）
  Meropenem 1g IV Q8H（自 2026-05-12）
  Fluconazole 400mg IV QD（自 2026-05-13）
```

---

### 情境二十：目前營養狀況

**目標**：整合多個資料來源，評估病人的營養狀態（體重、白蛋白、灌食/TPN 情況）。

**步驟**：

1. **體重 / BMI**（`patient_body_record`）：
   ```javascript
   const body = await fetch('https://hapi.csh.org.tw/patient_body_record?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   // 關注：Weight、Height、BMI 及測量日期
   JSON.stringify(body.slice(-3));
   ```

2. **營養相關檢驗**（`query_cumulative_lab_data`）— 篩 Albumin、Prealbumin、Total Protein：
   ```javascript
   (() => {
     const nutriItems = ['Albumin','Prealbumin','TP','Total Protein','Transferrin'];
     const nutri = window._lab
       .filter(d => d.TranCode==='9' &&
         nutriItems.some(k => d.ShortName?.toLowerCase().includes(k.toLowerCase())));
     const dates = [...new Set(nutri.map(d=>(d.LabDate||'').slice(0,10)))]
       .filter(Boolean).sort().reverse().slice(0,3);
     return nutri.filter(d => dates.some(dt => (d.LabDate||'').startsWith(dt)))
       .map(d=>`${d.LabDate.slice(0,10)} ${d.ShortName}: ${d.ReportValue} ${d.Unit}${d.IsAbnormal?' ⚠':''}`);
   })()
   ```

3. **灌食/TPN 速率**（護理班務記錄自由文字）：
   ```javascript
   // 篩今日班務記錄，搜尋灌食相關關鍵字
   (() => {
     const today = '2026-05-14';
     const feedKeywords = ['灌食','tube feed','NG','TPN','TNA','lipid','胺基酸','營養'];
     return window._nursing
       .filter(r => r.RecordTime.startsWith(today) && r.RecordTypeName?.includes('班務'))
       .filter(r => feedKeywords.some(k => r.Content?.toLowerCase().includes(k.toLowerCase())))
       .map(r => `[${r.RecordTime.slice(11,16)}] ${r.Content}`);
   })()
   ```

4. **營養相關醫囑/處置**（`patient_treatments`）— 篩 TPN/灌食相關：
   ```javascript
   const tx = await fetch('https://hapi.csh.org.tw/patient_treatments?visitNo=XXXXXXXX',
     {credentials:'include'}).then(r=>r.json());
   tx.filter(t => /TPN|TNA|灌食|營養|tube|feed/i.test(t.ItemName||''));
   ```

**呈現格式**：
```
MI07 營養狀況（2026-05-14）

體位：體重 65 kg，BMI 23.5（2026-05-10 測量）

營養指標：
  Albumin:    2.8 g/dL [3.5–5.0] ⚠（2026-05-13）
  Prealbumin: 12 mg/dL [16–35]  ⚠（2026-05-13）

營養支持方式：
  TPN（TNA）：護理記錄顯示 50 ml/hr 持續輸注
  腸道灌食：NG tube，目標量 1500 kcal/day，目前耐受良好

評估：低白蛋白血症，TPN 中，建議確認熱量目標是否達成。
```

---

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
