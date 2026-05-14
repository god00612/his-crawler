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

## 呼吸治療師（RT）專用情境

> **共同前提**：呼吸器設定（Mode、FiO2、PEEP、Tidal Volume、Rate、PIP）存在護理**班務記錄**自由文字中，格式因院區而異。以下 SOP 中凡涉及呼吸器設定解析，均需回醫院確認實際記錄格式後調整關鍵字。

---

### RT-1：全區危險訊號掃描（晨間快篩）

**目標**：找出今早有抽 ABG 且 PaCO2 > 50 或 SpO2 < 90% 的病人，附呼吸器 Mode 與 FiO2。

**步驟**：

**Phase 1 — 全病房並行掃描 ABG**：
```javascript
(async () => {
  const patients = [ /* 病房名單 + visitNo */ ];
  const today = '2026-05-14';
  const abgItems = ['PaCO2','pCO2','SpO2','SaO2','PaO2','pH'];

  const results = await Promise.all(patients.map(async pt => {
    window[`_lab_${pt.bed}`] = null;
    fetch(`https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json()).then(d=>{window[`_lab_${pt.bed}`]=d;});
    return pt.bed;
  }));
  return '已發射 ' + results.length + ' 個 fetch';
})();
```

等待後解析：
```javascript
(() => {
  const patients = [ /* 同上 */ ];
  const today = '2026-05-14';
  const alerts = [];
  for (const pt of patients) {
    const lab = window[`_lab_${pt.bed}`];
    if (!lab) continue;
    const todayAbg = lab.filter(d =>
      d.TranCode==='9' && (d.LabDate||'').startsWith(today) &&
      ['PaCO2','pCO2','SpO2','SaO2'].some(k => d.ShortName?.includes(k))
    );
    const paco2 = todayAbg.find(d => d.ShortName?.includes('CO2'));
    const spo2  = todayAbg.find(d => d.ShortName?.includes('SpO2')||d.ShortName?.includes('SaO2'));
    if ((paco2 && parseFloat(paco2.ReportValue) > 50) ||
        (spo2  && parseFloat(spo2.ReportValue)  < 90)) {
      alerts.push({bed: pt.bed, paco2: paco2?.ReportValue, spo2: spo2?.ReportValue});
    }
  }
  return JSON.stringify(alerts);
})();
```

**Phase 2 — 對警示床位查護理班務記錄取呼吸器設定**：
```javascript
// 針對 alerts 中每個床位
window._nursing = null;
fetch(`https://hapi.csh.org.tw/get_nursing_records?visitNo=XXXXXXXX`,
  {credentials:'include'}).then(r=>r.json()).then(d=>{window._nursing=d;});
// 篩今日最新班務，找 Mode/FiO2/PEEP 關鍵字
(() => {
  const today = '2026-05-14';
  return window._nursing
    .filter(r => r.RecordTime.startsWith(today) && r.RecordTypeName?.includes('班務'))
    .sort((a,b) => b.RecordTime.localeCompare(a.RecordTime))
    .slice(0,3)
    .map(r => r.Content);
})()
```

> ⚠️ 呼吸器 Mode/FiO2 關鍵字需回醫院確認班務記錄格式。

---

### RT-2：大夜班動態追蹤

**目標**：單一病人大夜班（23:00–07:00）的完整動態——呼吸器參數變動、STAT ABG、Portable CXR、抽痰紀錄。

**步驟（先廣後深）**：

**第一層 — 護理紀錄 + 醫師交班**（通常已足夠）：
```javascript
// 大夜班時間區間
const from = '2026-05-13T23:00';
const to   = '2026-05-14T07:00';

// 護理紀錄篩大夜
window._nursing
  .filter(r => r.RecordTime >= from && r.RecordTime <= to)
  .sort((a,b) => a.RecordTime.localeCompare(b.RecordTime))
  .map(r => `[${r.RecordTime.slice(11,16)}] ${r.RecordTypeName}: ${r.Content}`);
```

**第二層 — 確認 STAT ABG（有無夜間緊急抽血）**：
```javascript
(() => {
  const from = '2026-05-13T23:00';
  const to   = '2026-05-14T07:00';
  const abgItems = ['pH','pCO2','pO2','HCO3','BE','SaO2','PaO2','PaCO2'];
  return window._lab
    .filter(d => d.TranCode==='9' &&
      d.LabDate >= from && d.LabDate <= to &&
      abgItems.some(k => d.ShortName?.includes(k)))
    .map(d => `${d.LabDate.slice(11,16)} ${d.ShortName}: ${d.ReportValue} ${d.Unit}${d.IsAbnormal?' ⚠':''}`);
})()
```

**第三層 — Portable CXR（PACS 確認）**：
```javascript
// get_oracle_pacs_study_list 篩昨晚日期，看有無 Portable/緊急 CXR
studies.filter(s =>
  s.StudyDateTime >= '2026-05-13T23:00' &&
  s.StudyDateTime <= '2026-05-14T07:00' &&
  /chest|cxr|胸部/i.test(s.StudyDesc)
)
```

---

### RT-3：RT 專屬交班卡

**目標**：查房用一頁式摘要——插管天數、管路深度、最新呼吸器設定、今早 Gas、最近痰培養。

**組合查詢**：

```javascript
// 1. 基本資料 + 入院/插管日期
const info = await fetch(`https://hapi.csh.org.tw/patient_info?visitNo=XXXXXXXX`,
  {credentials:'include'}).then(r=>r.json());

// 2. 今早 ABG（lab，篩今日）
const abg = window._lab.filter(d =>
  d.TranCode==='9' && (d.LabDate||'').startsWith('2026-05-14') &&
  ['pH','pCO2','pO2','HCO3','BE','FiO2','SaO2'].some(k=>d.ShortName?.includes(k))
);

// 3. 最近痰培養
const sputum = window._lab.filter(d =>
  d.TranCode==='8' && /SPT|痰|sputum/i.test(d.SpecimenCode||d.Item||'')
).sort((a,b)=>b.LabDate.localeCompare(a.LabDate)).slice(0,2);

// 4. 最新班務記錄（呼吸器設定、管路深度）
const latestNote = window._nursing
  .filter(r => r.RecordTypeName?.includes('班務'))
  .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))[0];
```

**呈現格式**：
```
═══ RT 交班卡 MI01 王OO ═══
插管天數：第 5 天（插管日 2026-05-09）
管路深度：ETT 22 cm（唇齒處）          ← 來自最新班務記錄
─────────────────────────────
呼吸器設定（最新班務）：
  Mode: SIMV, FiO2: 40%, PEEP: 6, TV: 450, Rate: 12
─────────────────────────────
今早 ABG（08:15）：
  pH 7.38 | pCO2 42 | pO2 88 | HCO3 24 | BE -1
  P/F ratio: 220
─────────────────────────────
最近痰培養（2026-05-12）：
  Klebsiella pneumoniae ⚠ → 藥敏見完整報告
```

> ⚠️ 插管日期需從護理紀錄找「插管」關鍵字確認（與入院日期不同）；呼吸器設定解析待確認格式。

---

### RT-4：脫離呼吸器候選名單（SBT 前篩選）

**目標**：全病房自動篩出符合 SBT 前置條件的病人。

**篩選條件**：插管 > 48 小時、FiO2 ≤ 40%、PEEP ≤ 5、無發燒、未使用升壓劑。

**步驟（全病房掃描）**：

```javascript
(async () => {
  const patients = [ /* 病房名單 */ ];
  const today = '2026-05-14';

  const results = await Promise.all(patients.map(async pt => {
    // 取今日最新班務記錄
    const nursing = await fetch(
      `https://hapi.csh.org.tw/get_nursing_records?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());
    const latestNote = nursing
      .filter(r => r.RecordTime.startsWith(today) && r.RecordTypeName?.includes('班務'))
      .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))[0];
    const content = latestNote?.Content || '';

    // 解析條件（關鍵字待確認實際格式）
    const fio2Match  = content.match(/FiO2[:\s]*(\d+)/i);
    const peepMatch  = content.match(/PEEP[:\s]*(\d+)/i);
    const fio2  = fio2Match  ? parseInt(fio2Match[1])  : null;
    const peep  = peepMatch  ? parseInt(peepMatch[1])  : null;
    const noVaso = !/norepinephrine|epinephrine|dopamine|vasopressin/i.test(content);

    return {
      bed: pt.bed, name: pt.name,
      fio2, peep, noVaso,
      candidate: fio2 <= 40 && peep <= 5 && noVaso
    };
  }));

  return results.filter(r=>r.candidate)
    .map(r=>`${r.bed} ${r.name}: FiO2=${r.fio2}% PEEP=${r.peep} 無升壓劑`);
})();
```

> ⚠️ FiO2/PEEP 解析的正規表達式需回醫院確認班務記錄的實際寫法。插管天數計算需先確認插管日期的記錄方式。

---

### RT-5：P/F Ratio 趨勢圖 + PEEP/I/O 對照

**目標**：過去 N 天的 P/F ratio 趨勢，對照 PEEP 調整與 I/O 平衡。

**步驟**：

1. **取 PaO2 + FiO2 歷史資料**（lab + 護理記錄）：
```javascript
// PaO2：來自 query_cumulative_lab_data TranCode:"9"
const pao2Records = window._lab
  .filter(d => d.TranCode==='9' && d.ShortName?.includes('PaO2'));

// FiO2：來自每日班務記錄自由文字（解析 FiO2 數值）
const fio2Records = window._nursing
  .filter(r => r.RecordTypeName?.includes('班務'))
  .map(r => {
    const m = r.Content?.match(/FiO2[:\s]*(\d+)/i);
    return m ? {date: r.RecordTime.slice(0,10), fio2: parseInt(m[1])/100} : null;
  }).filter(Boolean);
```

2. **計算每日 P/F ratio**（PaO2 / FiO2）並用 matplotlib 繪圖（參考情境十六）。

3. **I/O**：
> ⚠️ `get_io` API 格式待確認（見情境七）。

**呈現格式**：趨勢折線圖（P/F ratio + PEEP 雙軸），用 `Read` tool 顯示 PNG。

---

### RT-6：CXR 對比 + ETT 位置 + 抽痰狀況

**目標**：比較今日與昨日 CXR（浸潤變化、ETT 位置），並對照抽痰記錄。

**步驟**：

1. **取最近兩張 CXR 影像**（同情境一）→ PowerShell WADO → `Read` tool 並排呈現
2. **Claude 視覺判讀**：浸潤增加/減少、ETT tip 位置（距隆突距離）、有無新發現
3. **放射科文字報告**：⚠️ API 待確認（同情境六）
4. **同日抽痰紀錄**（護理記錄）：
```javascript
window._nursing
  .filter(r => r.RecordTime.startsWith('2026-05-14') &&
    /抽痰|suction|sputum|痰/i.test(r.Content||''))
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))
  .map(r=>`[${r.RecordTime.slice(11,16)}] ${r.Content}`);
```

**呈現格式**：
```
【昨日 CXR 2026-05-13】     【今日 CXR 2026-05-14】
[影像]                        [影像]

影像判讀：
  右下肺浸潤較昨日增加 ⚠
  ETT tip 約距隆突 4 cm，位置尚可

抽痰紀錄（今日）：
  [08:00] 黃綠色濃痰，量中，較昨日增多 ⚠
  [14:00] 白色稀痰，量少
```

---

### RT-7：拔管前氣道安全確認

**目標**：確認 Cuff leak test 結果、類固醇醫囑是否開立、最後給藥時間。

**步驟（先廣後深）**：

**第一層 — 護理紀錄找 Cuff leak**：
```javascript
window._nursing
  .filter(r => /cuff|leak|漏氣/i.test(r.Content||''))
  .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))
  .slice(0,3)
  .map(r=>`[${r.RecordTime}] ${r.Content}`);
```

**第二層 — 類固醇醫囑**：
```javascript
const drugs = await fetch(`https://hapi.csh.org.tw/patient_drugs?visitNo=XXXXXXXX`,
  {credentials:'include'}).then(r=>r.json());
// 篩類固醇（Dexamethasone、Solu-Medrol、Prednisolone）
drugs.filter(d =>
  /dexamethasone|dexa|solu.?medrol|methylprednisolone|prednisolone/i
  .test(d.DrugName||d.OrderName||'')
);
```

**第三層 — 最後給藥時間**（護理紀錄找給藥記錄）：
```javascript
window._nursing
  .filter(r => /dexamethasone|dexa|類固醇|steroi/i.test(r.Content||''))
  .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))
  .slice(0,1);
```

**呈現格式**：
```
MI02 拔管前確認清單

Cuff Leak Test：
  [2026-05-14 06:00] 漏氣量 180 mL，通過標準（>110 mL）✓

類固醇預防喉頭水腫：
  Dexamethasone 5mg IV Q6H × 4 dose（已開立）✓
  最後一劑預計：2026-05-14 18:00
  → 建議拔管時機：最後一劑後 1–2 小時

⚠️ 以上資料請再與主治醫師確認。
```

---

### RT-8：血氧驟降事件根因分析

**目標**：SpO2 突然下降時，從多源記錄找出時間軸與根本原因。

**步驟**：

指定事件時間（例如 10:00），往前後各 30 分鐘查：

```javascript
const eventTime = '2026-05-14T10:00';
const from = '2026-05-14T09:30';
const to   = '2026-05-14T10:30';

// 護理紀錄（抽痰、翻身、給藥、緊急處置）
const events = window._nursing
  .filter(r => r.RecordTime >= from && r.RecordTime <= to)
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))
  .map(r=>`[${r.RecordTime.slice(11,16)}] ${r.RecordTypeName}: ${r.Content}`);
```

**交叉比對清單**：
- 抽痰/翻身 → 護理紀錄關鍵字：抽痰、suction、翻身、reposit
- 鎮靜劑給予 → 護理紀錄關鍵字：fentanyl、midazolam、propofol、給藥
- 呼吸器警報 → 護理紀錄關鍵字：PIP、高壓、alarm、警報
- 生命徵象變化 → `get_vital_sign`（若 API 支援時間篩選）

> ⚠️ PIP 警報紀錄若未在護理記錄中，需確認是否有獨立的 alarm log API。

**呈現格式**：
```
MI03 SpO2 驟降事件時間軸（10:00 前後 30 分鐘）

09:35  處置 護士A：抽痰，黃綠色濃痰大量
09:50  班務 護士A：呼吸器 PIP 升至 38 cmH2O，通知醫師
10:00  ⚠️ SpO2 drop 85%
10:05  緊急 醫師B：聽診，右側呼吸音減弱，懷疑右側氣胸
10:10  處置 護士A：備 CXR Portable
10:20  處置 護士A：醫師執行胸腔穿刺減壓，SpO2 回升至 95%

根本原因：大量濃痰導致 PIP 升高 → 張力性氣胸
```

---

### RT-9：氣切時機追蹤（插管 14/21 天）

**目標**：列出插管即將滿 14 或 21 天的病人，附家屬/醫師討論紀錄。

**步驟**：

1. **取各病人插管日期**（護理紀錄找「插管」事件）：
```javascript
(async () => {
  const patients = [ /* 病房名單 */ ];
  const today = new Date('2026-05-14');

  const results = await Promise.all(patients.map(async pt => {
    const nursing = await fetch(
      `https://hapi.csh.org.tw/get_nursing_records?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());
    // 找最早的插管記錄
    const intubation = nursing
      .filter(r => /插管|intubat|ETT|氣管內管/i.test(r.Content||''))
      .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))[0];
    if (!intubation) return {...pt, days: null};
    const intubDate = new Date(intubation.RecordTime);
    const days = Math.floor((today - intubDate) / 86400000);
    return {...pt, days, intubDate: intubation.RecordTime.slice(0,10)};
  }));

  return results
    .filter(r => r.days >= 12)  // 12天以上開始追蹤
    .sort((a,b)=>b.days-a.days)
    .map(r=>`${r.bed} ${r.name}: 插管第 ${r.days} 天（${r.intubDate}）`);
})();
```

2. **家屬/氣切討論紀錄**（醫師交班中找關鍵字）：
```javascript
const summary = await fetch(`https://hapi.csh.org.tw/get_medSummary?visitNo=XXXXXXXX`,
  {credentials:'include'}).then(r=>r.json());
summary.filter(s => /氣切|tracheostomy|家屬|consent|DNR|RCW/i.test(s.Summary||''))
  .map(s=>`[${s.RecordTime}] ${s.Summary}`);
```

> ⚠️ 社工/家屬會談的正式紀錄若在獨立系統，API 尚未確認。

**呈現格式**：
```
氣切評估追蹤（2026-05-14）

⚠️ 即將滿 21 天：
  MI03 張OO — 插管第 19 天（2026-04-25）
    交班紀錄：「家屬已討論氣切，尚未決定」（2026-05-12）

⚠️ 即將滿 14 天：
  MI07 陳OO — 插管第 13 天（2026-05-01）
    交班紀錄：無氣切相關紀錄，建議啟動討論
```

---

### RT-10：耗材更換排程（Circuit / HME / Closed Suction）

**目標**：列出今日需更換耗材的床號。

**更換週期**：

| 耗材 | 更換週期 |
|---|---|
| 呼吸器管路（Circuit） | 每 7 天 |
| HME（人工鼻） | 每日（或依醫囑） |
| Closed Suction | 每 7 天 |

**步驟**：

找各床最後一次更換記錄：
```javascript
(async () => {
  const patients = [ /* 病房名單 */ ];
  const today = '2026-05-14';

  const results = await Promise.all(patients.map(async pt => {
    const nursing = await fetch(
      `https://hapi.csh.org.tw/get_nursing_records?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());

    const findLast = (keywords) => nursing
      .filter(r => keywords.some(k => r.Content?.toLowerCase().includes(k)))
      .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))[0];

    const circuitLast  = findLast(['circuit','管路更換','呼吸器管路']);
    const hmeLast      = findLast(['hme','人工鼻']);
    const closedLast   = findLast(['closed suction','密閉式','抽痰管更換']);

    const daysSince = (rec) => rec
      ? Math.floor((new Date(today)-new Date(rec.RecordTime.slice(0,10)))/86400000)
      : 999;

    return {
      bed: pt.bed, name: pt.name,
      circuit:  {days: daysSince(circuitLast),  due: daysSince(circuitLast)  >= 7},
      hme:      {days: daysSince(hmeLast),       due: daysSince(hmeLast)      >= 1},
      closed:   {days: daysSince(closedLast),    due: daysSince(closedLast)   >= 7},
    };
  }));

  return results.filter(r => r.circuit.due || r.hme.due || r.closed.due)
    .map(r => {
      const items = [];
      if (r.circuit.due) items.push(`Circuit（第${r.circuit.days}天）`);
      if (r.hme.due)     items.push(`HME（第${r.hme.days}天）`);
      if (r.closed.due)  items.push(`Closed Suction（第${r.closed.days}天）`);
      return `${r.bed} ${r.name}：${items.join('、')}`;
    });
})();
```

> ⚠️ 需確認護理紀錄中耗材更換的實際關鍵字寫法。

**呈現格式**：
```
今日耗材更換清單（2026-05-14）

待換 Circuit：
  MI01 王OO（第 7 天，今日到期）
  MI05 李OO（第 8 天，已逾期 ⚠）

待換 HME：
  MI03 張OO、MI07 陳OO、MI09 林OO

待換 Closed Suction：
  MI02 吳OO（第 7 天，今日到期）
```

---

### RT-11：多條件惡化篩查（FiO2/PEEP上升 + 膿痰 + 發炎指標）

**目標**：插管 > 48h 且同時符合三條件的床號——(1) 過去 24h FiO2 或 PEEP 需求上升、(2) 今日痰液轉黃綠膿痰、(3) 最新 WBC/CRP 上升。

**步驟（全病房 Promise.all）**：

```javascript
(async () => {
  const patients = [ /* 病房名單 */ ];
  const today = '2026-05-14';
  const yesterday = '2026-05-13';

  const results = await Promise.all(patients.map(async pt => {
    const [nursing, lab] = await Promise.all([
      fetch(`https://hapi.csh.org.tw/get_nursing_records?visitNo=${pt.visitNo}`,
        {credentials:'include'}).then(r=>r.json()),
      fetch(`https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=${pt.visitNo}`,
        {credentials:'include'}).then(r=>r.json())
    ]);

    // 條件一：FiO2/PEEP 上升（比較今日與昨日班務記錄）
    const getVentVal = (notes, date, key) => {
      const note = notes.filter(r=>r.RecordTime.startsWith(date)&&r.RecordTypeName?.includes('班務'))
        .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))[0];
      const m = note?.Content?.match(new RegExp(key+'[:\\s]*(\\d+)','i'));
      return m ? parseInt(m[1]) : null;
    };
    const fio2Today = getVentVal(nursing, today, 'FiO2');
    const fio2Yest  = getVentVal(nursing, yesterday, 'FiO2');
    const peepToday = getVentVal(nursing, today, 'PEEP');
    const peepYest  = getVentVal(nursing, yesterday, 'PEEP');
    const ventRising = (fio2Today > fio2Yest) || (peepToday > peepYest);

    // 條件二：今日膿痰
    const purulentSputum = nursing.some(r =>
      r.RecordTime.startsWith(today) &&
      /黃綠|膿痰|purulent|黃色|綠色|thick.*yellow|green/i.test(r.Content||''));

    // 條件三：WBC/CRP 上升（最新值 vs 前一筆）
    const getLabTrend = (labData, keyword) => {
      const items = labData.filter(d=>d.TranCode==='9'&&d.ShortName?.includes(keyword))
        .sort((a,b)=>b.LabDate.localeCompare(a.LabDate));
      if (items.length < 2) return null;
      return parseFloat(items[0].ReportValue) > parseFloat(items[1].ReportValue);
    };
    const wbcRising = getLabTrend(lab, 'WBC');
    const crpRising = getLabTrend(lab, 'CRP');
    const inflammRising = wbcRising || crpRising;

    return {
      ...pt,
      ventRising, purulentSputum, inflammRising,
      alert: ventRising && purulentSputum && inflammRising
    };
  }));

  return results.filter(r=>r.alert)
    .map(r=>`⚠️ ${r.bed} ${r.name}: FiO2/PEEP↑, 膿痰, WBC/CRP↑`);
})();
```

> ⚠️ FiO2/PEEP 解析格式待確認（見 RT-1）。

---

### RT-12：困難脫離病患 — I/O + CVP + RSBI 趨勢

**目標**：近三天累計 I/O 平衡、CVP 走勢，對照每日 SBT 的 RSBI 變化。

**資料來源**：

| 資料 | 來源 | 狀態 |
|---|---|---|
| 累計 I/O | `get_io?visitNo=` | ⚠️ API 格式待確認 |
| CVP | `get_vital_sign` 或護理班務記錄 | ⚠️ 需確認 CVP 是否在 vital sign API |
| RSBI (f/Vt) | 護理紀錄自由文字 | 需確認是否有記錄 |

**RSBI 從護理紀錄解析**（format 待確認）：
```javascript
window._nursing
  .filter(r => /RSBI|f\/Vt|淺快呼吸/i.test(r.Content||''))
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))
  .map(r => {
    const m = r.Content.match(/RSBI[:\s]*(\d+)/i);
    return {date: r.RecordTime.slice(0,10), rsbi: m?parseInt(m[1]):null, note: r.Content};
  });
```

**趨勢圖（matplotlib，待 I/O 和 CVP 資料確認後補完）**：
```python
# 三天資料繪製：
# 上圖：每日 I/O 平衡（正值=液體過多, 負值=液體不足）
# 中圖：CVP 走勢
# 下圖：RSBI（<105 = 可能可脫離）
```

> ⚠️ 此情境高度依賴 `get_io` 和 CVP 資料來源，回醫院確認後補完。

---

### RT-13：全區混合型酸鹼失衡 + Lactate + Anion Gap + 動態尿量

**目標**：篩出混合型酸鹼失衡病人（如代酸 + 呼酸），附 Lactate、Anion Gap 與近 8 小時尿量。

**步驟**：

**Phase 1 — 全病房 ABG 掃描**：
```javascript
(async () => {
  const patients = [ /* 病房名單 */ ];
  const today = '2026-05-14';

  const results = await Promise.all(patients.map(async pt => {
    const lab = await fetch(
      `https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=${pt.visitNo}`,
      {credentials:'include'}).then(r=>r.json());

    // 最新一次 ABG（今日）
    const todayLab = lab.filter(d=>d.TranCode==='9'&&(d.LabDate||'').startsWith(today));
    const get = (key) => parseFloat(
      todayLab.find(d=>d.ShortName?.includes(key))?.ReportValue||'NaN');

    const pH   = get('pH');
    const pCO2 = get('pCO2') || get('CO2');
    const hco3 = get('HCO3') || get('bicarbonate');
    const na   = get('Na');
    const cl   = get('Cl');
    const lactate = get('Lactate') || get('乳酸');

    // Anion Gap = Na - (Cl + HCO3)
    const ag = na - (cl + hco3);

    // 混合型酸鹼判斷
    const metAcidosis  = pH < 7.35 && hco3 < 22;   // 代謝性酸中毒
    const respAcidosis = pH < 7.35 && pCO2 > 45;    // 呼吸性酸中毒
    const mixedAcid    = metAcidosis && respAcidosis;
    // 其他混合型可依需求擴充（如呼酸+代鹼等）

    return {...pt, pH, pCO2, hco3, ag, lactate, mixedAcid};
  }));

  window._acidPts = results.filter(r=>r.mixedAcid);
  return window._acidPts.map(r=>
    `${r.bed}: pH=${r.pH} pCO2=${r.pCO2} HCO3=${r.hco3} AG=${r.ag} Lactate=${r.lactate}`);
})();
```

**Phase 2 — 對警示床位查近 8 小時尿量**：
```javascript
// 從護理紀錄找尿量記錄
const from = '2026-05-14T02:00';
const to   = '2026-05-14T10:00';
window._nursing
  .filter(r => r.RecordTime >= from && r.RecordTime <= to &&
    /尿量|urine|UO|foley|小便/i.test(r.Content||''))
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))
  .map(r=>`[${r.RecordTime.slice(11,16)}] ${r.Content}`);
```

**呈現格式**：
```
全區混合型酸鹼失衡（2026-05-14 早班）

MI03 張OO：
  ABG: pH 7.20 | pCO2 55 | HCO3 16（代酸＋呼酸 ⚠）
  Anion Gap: 18（升高，>12 ⚠）
  Lactate: 4.2 mmol/L ⚠
  近 8h 尿量：約 80 mL（少尿 ⚠）
  → 建議評估：敗血症/低灌流/腎功能惡化
```

---

### RT-14：HFCWO 醫囑 + 痰量分級 + PT 排程確認

**目標**：列出有開 HFCWO（高頻胸壁震盪/拍痰背心）的病人，比對痰量與 PT 頻率是否足夠。

**步驟**：

**1. 找 HFCWO 醫囑**（patient_treatments 或 patient_orders）：
```javascript
const tx = await fetch(`https://hapi.csh.org.tw/patient_treatments?visitNo=XXXXXXXX`,
  {credentials:'include'}).then(r=>r.json());
tx.filter(t => /HFCWO|拍痰背心|高頻|chest.*oscill|vest/i.test(t.ItemName||''));
```

> ⚠️ HFCWO 的醫囑代碼和所在 API（treatments 或 orders）待回醫院確認。

**2. 從護理紀錄分析痰量與黏稠度**：
```javascript
// 近三天痰量/黏稠度記錄
const threeDays = ['2026-05-12','2026-05-13','2026-05-14'];
window._nursing
  .filter(r => threeDays.some(d=>r.RecordTime.startsWith(d)) &&
    /痰量|sputum|抽痰|黏稠|稀薄|濃稠|量多|量少/i.test(r.Content||''))
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime))
  .map(r=>`[${r.RecordTime.slice(0,16)}] ${r.Content}`);
```

**3. PT 排程**（patient_orders 或 patient_treatments 找物理治療相關）：
```javascript
tx.filter(t => /物理治療|PT|胸腔|chest.*physio|呼吸治療/i.test(t.ItemName||''));
```

> ⚠️ PT 排程的 API 和頻次欄位待確認。

---

### RT-15：心臟術後/心衰竭 — SBT 期間心肺交互監測

**目標**：PEEP 調降或 SBT 期間，偵測 PVC、心跳過速、MAP 掉落 > 20%。

> ⚠️ **高難度情境**：需要時序精確的心律/MAP 資料，目前有以下限制：

**可行部分（護理紀錄）**：
```javascript
// 找 SBT 期間護理記錄的異常描述
const sbtTime = '2026-05-14T09:00';  // SBT 開始時間
const from = sbtTime;
const to   = '2026-05-14T10:00';
window._nursing
  .filter(r => r.RecordTime >= from && r.RecordTime <= to &&
    /PVC|心律|arrhythmia|心跳過速|tachycardia|MAP|血壓下降|PEEP/i.test(r.Content||''))
  .map(r=>`[${r.RecordTime.slice(11,16)}] ${r.Content}`);
```

**待確認**：
- `get_vital_sign` 是否有足夠時間解析度記錄 MAP 變化？
- PVC/心律不整是否有獨立的心電圖/telemetry API？
- 若上述均無，則此情境只能從護理自由文字間接判斷

---

### RT-16：轉送 CT 前氧氣消耗試算 + 鎮靜醫囑

**目標**：依 Ve 與 FiO2 估算氧氣鋼瓶消耗時間，並確認轉送前鎮靜加強醫囑。

**步驟**：

**1. 從護理紀錄取目前呼吸器設定（Ve、FiO2）**：
```javascript
const latestNote = window._nursing
  .filter(r=>r.RecordTypeName?.includes('班務'))
  .sort((a,b)=>b.RecordTime.localeCompare(a.RecordTime))[0];
// 解析：Ve（分鐘通氣量）、FiO2
const veMatch  = latestNote?.Content?.match(/Ve[:\s]*([\d.]+)/i);
const fio2Match = latestNote?.Content?.match(/FiO2[:\s]*(\d+)/i);
const ve   = veMatch  ? parseFloat(veMatch[1])  : null;  // L/min
const fio2 = fio2Match? parseInt(fio2Match[1])/100 : null;
```

**2. 氧氣消耗試算（Python）**：
```python
# 呼吸器使用純氧補充公式（簡化估算）
# 實際 O2 flow ≈ Ve × FiO2（若空氣與O2混合）
# 或依呼吸器機型查混合比

ve   = 8.0   # L/min（從護理記錄取得）
fio2 = 0.50  # 50%

# 標準氧氣鋼瓶規格
cylinders = {
  'E瓶': 660,    # 容積約 660L（常見攜帶型）
  'D瓶': 415,
}

# 估算 O2 消耗速率（近似：FiO2 > 21% 的額外需氧量）
o2_flow_lpm = ve * fio2  # 粗估
for name, vol in cylinders.items():
  minutes = vol / o2_flow_lpm
  print(f"{name}：約可用 {minutes:.0f} 分鐘（{minutes/60:.1f} 小時）")

# 建議：CT 轉送含等待時間約 30–60 分鐘，至少備 2 倍餘裕
```

**3. 確認鎮靜加強醫囑**：
```javascript
// patient_drugs 篩鎮靜相關 STAT 或 PRN 醫囑
const drugs = await fetch(`https://hapi.csh.org.tw/patient_drugs?visitNo=XXXXXXXX`,
  {credentials:'include'}).then(r=>r.json());
drugs.filter(d => /fentanyl|midazolam|propofol|ketamine|versed|dormicum/i
  .test(d.DrugName||d.OrderName||''));
```

**呈現格式**：
```
MI07 轉送 Chest CT 氧氣評估

目前設定：Mode SIMV, FiO2 50%, Ve 8.0 L/min

氧氣消耗估算：
  E 瓶（660L）：約可用 99 分鐘 → 轉送 60 分鐘尚足夠，建議備 1 瓶
  若等待超過 90 分鐘：需備 2 瓶 E 瓶

轉送前鎮靜醫囑：
  Midazolam 2mg IV PRN（已開立）✓
  → 確認是否需追加 STAT 劑量，請與醫師確認
```

---

### RT-17：Bedside Bronchoscopy 術後即時監測

**目標**：支氣管鏡術後追蹤 FiO2 需求、PIP 趨勢、Vte 變化。

**步驟**：

```javascript
// 指定術後監測時間窗（如 11:00 後 2 小時）
const procEnd = '2026-05-14T11:00';
const monEnd  = '2026-05-14T13:00';

// 護理紀錄篩術後監測記錄
const postProc = window._nursing
  .filter(r => r.RecordTime >= procEnd && r.RecordTime <= monEnd)
  .sort((a,b)=>a.RecordTime.localeCompare(b.RecordTime));

// 三個警示條件
const fio2Rising = postProc.some(r=>/FiO2.*上升|FiO2.*升高|FiO2.*increase|需氧上升/i.test(r.Content||''));
const pipHigh    = postProc.some(r=>/PIP.*高壓|高壓.*警報|PIP.*limit|PIP.*top|PIP[:\s]*[4-9]\d/i.test(r.Content||''));
const vteDown    = postProc.some(r=>/Vte.*下降|通氣量.*減少|tidal.*drop|Vte[:\s]*[1-2]\d{2}[^\d]/i.test(r.Content||''));

// 解析數值變化
const events = postProc.map(r=>`[${r.RecordTime.slice(11,16)}] ${r.RecordTypeName}: ${r.Content}`);
```

**警示判斷**：

| 警示 | 判斷條件 | 臨床意義 |
|---|---|---|
| FiO2 急遽上升 | 術後 FiO2 較術前增加 ≥ 10% | 術後低氧、出血、分泌物堵塞 |
| PIP 持續頂高壓上限 | PIP > 設定上限 | 氣道阻力增加、分泌物殘留 |
| Vte 顯著衰退 | 自發通氣量 < 術前 20% | 呼吸肌疲勞、鎮靜過深 |

**呈現格式**：
```
第二床 Bronchoscopy 術後監測（11:00–13:00）

⚠️ 警示：
  FiO2 由 40% 升至 55%（術後 30 分鐘）⚠
  PIP 達 38 cmH2O（高壓上限 40）⚠
  Vte 維持穩定（無明顯衰退）✓

事件時間軸：
  [11:05] FiO2 調升至 50%
  [11:20] PIP 升至 35，通知醫師
  [11:35] 抽痰，大量血性分泌物
  [11:50] PIP 回降至 28，FiO2 調回 45%

建議：持續監測，若 PIP 再升考慮重複支氣管鏡。
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
