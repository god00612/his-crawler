# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A Playwright-based crawler for the CSH (Cheng Sheng Hospital) HIS web system. It automates the browser to intercept background JSON API responses when a patient is selected, collecting all visible clinical data (lab results, vitals, nursing notes, medications, orders, etc.) and returning it as structured JSON.

## How to run

```bash
# List all patients in a ward
python his_query.py --ward MI

# Full data for one bed
python his_query.py --bed MI01

# Filter by shift (大夜/小夜/白天)
python his_query.py --bed MI01 --shift 大夜
```

Output is JSON on stdout; progress logs go to stderr. Claude reads stdout.

Subprocess pattern used by Claude to query and read results:
```python
import subprocess, json
result = subprocess.run(['python', 'his_query.py', '--bed', 'MI01'], capture_output=True)
data = json.loads(result.stdout.decode('utf-8'))
```

## Architecture

### Entry point
`his_query.py` — thin CLI wrapper. Parses `--ward`/`--bed`/`--shift` args, calls the appropriate async function from `clinical_service.py`, dumps JSON to stdout.

### Core engine: `clinical_service.py`

**Browser flow** (every query):
1. `page.goto(_get_his_url())` — reads `su=` token from `his_token.txt` (falls back to hardcoded value if file missing); no login needed if session is alive
2. `_open_ward_list(page, ward_prefix)` — triggers the **更換名單** UI flow:
   - Selects `<option value="changePatientList">` in a `<select>` to open the dialog
   - Clicks the 護理站 radio button
   - Selects the unit (e.g. `MI`) from a unit `<select>`
   - Clicks **確定名單** button
   - Reads the updated patient dropdown via DOM API (`opt.text_content()`) — NOT raw HTML regex, to avoid Big5/UTF-8 encoding issues
3. For `fetch_patient_all_data`: calls `select_option(internal_id)` on the patient dropdown, which triggers all background API calls
4. `page.on("response", handle_response)` intercepts every JSON response from `csh.org.tw` whose URL endpoint is in `CAPTURE_ENDPOINTS`
5. Waits 15 seconds for all APIs to finish, then closes browser

**API interception** (`CAPTURE_ENDPOINTS` dict in `clinical_service.py`):
All 23 APIs fired when a patient page loads are captured, including: `query_cumulative_lab_data`, `get_vital_sign` (1.7 MB), `get_nursing_records`, `patient_drugs`, `patient_orders`, `patient_treatments`, `get_pump_records`, `SOFA_score`, `patient_info`, `patient_body_record`, `med_allergy`, `get_pacs_images`, etc.

**Known API quirks**:
- `get_medSummary` → 醫師**交班紀錄** (NOT drug data). Fields: `Summary`, `ProgressType`, `RecordUser`, `RecordTime`, `ShiftType`. Parser: `_parse_med_summary()`.
- `get_nursing_records` → via encrypted URL (intercepted from network) returns only current shift. Via `?visitNo=` returns **full admission history** (10,000+ records for long-stay patients). Filter by date after fetch.
- `get_pump_records` → often returns 0 records. Actual pump infusion rates (ml/hr) are embedded in nursing **班務記錄** entries as free text (e.g. "FENTANYL 維持 0.5 ml/hr").

**Direct fetch pattern** (for supplemental queries inside a running browser session):
```python
url = "https://hapi.csh.org.tw/get_medSummary?visitNo=34472131"
resp = await page.evaluate(f"fetch('{url}', {{credentials:'include'}}).then(r=>r.json())")
```
This reuses the active browser session cookie — no re-auth needed. Also works for `get_nursing_records?encrypted=...&nonce=...`.

**Parsing pipeline**:
Each captured endpoint has a dedicated `_parse_*` function. Results are merged by `_combine_all()` into a single dict with Chinese-labelled keys. Shift filtering (`_filter_by_shift`) applies time windows: 白天 07–15, 小夜 15–23, 大夜 23–07 (crosses midnight).

**Lab record structure** (`_parse_lab()` output, per item):
- `organ_system` — classified by `_organ_system(item, specimen_code)` (see below)
- `item` — display name (from `ShortName` for TranCode 9; `Item` for TranCode 8)
- `value` — numeric result string (TranCode 9) or `""` (TranCode 8)
- `unit` — unit string
- `ref` — reference range string
- `report` — free-text report (TranCode 8 culture/microscopy) or `""`
- `abnormal` — bool (from `IsAbnormal` for TranCode 9; derived from `ReportText` for TranCode 8)
- `lab_date` — date string

`query_cumulative_lab_data` returns two record types: `TranCode:"8"` (culture/report, fields: `Item`/`ReportText`) and `TranCode:"9"` (numeric, fields: `ShortName`/`ReportValue`/`Unit`/`IsAbnormal`). Both branches are handled in `_parse_lab()`.

**Encoding**: Patient names in the HIS page are returned as proper Unicode by Playwright's DOM API. The scripts call `sys.stdout.reconfigure(encoding="utf-8")` at startup so JSON output is always UTF-8. Terminal display may appear garbled on Windows, but the bytes are correct.

### Key constants
- `his_token.txt` — contains the `su=` session token (one line, numbers only); update this file when session expires
- `HIS_URL` in `clinical_service.py` — base URL constant; `_get_his_url()` appends the token from `his_token.txt` at runtime
- `CAPTURE_ENDPOINTS` — add new API names here to capture additional endpoints
- `CATEGORY_MAP` — maps lab item name substrings to Chinese category labels (HIS display category, e.g. 生化/血液)
- `SHIFT_WINDOWS` — shift time boundaries
- `ORGAN_SYSTEM_EXACT` — dict mapping exact `ShortName` → organ system (e.g. `"NA" → "電解質"`). Use for items that would be ambiguously matched by substring.
- `ORGAN_SYSTEM_PATTERNS` — list of `([keywords], system)` tuples for case-insensitive substring matching. Keywords are all lowercase. Covers: 心臟/腎臟/肝臟/胰臟/血液/凝血/感染/代謝/電解質/ABG/微生物/感染血清/甲狀腺/鐵代謝/內分泌.
- `_organ_system(item, specimen_code)` — resolves organ system; checks SpecimenCode first (UR→尿液, SPT→微生物, BLD+culture→微生物), then EXACT dict, then PATTERNS, then returns `"其他"`.

**ABG SpecimenCode**: In this HIS, ABG records have `SpecimenCode = 'BLD'` (not `'BLDA'` or `'BLDV'` as in some HIS systems). Filter ABG by item name keywords (pH, pCO2, pO2, HCO3, BE, etc.), not by SpecimenCode alone.

### Chrome MCP fetch approach (preferred for live queries)

When the user's HIS browser is open (tab at `hapi.csh.org.tw`), use Chrome MCP `javascript_tool` for direct API calls — no new browser needed, reuses existing session cookies.

**Key discovery**: `internal_id` from the Playwright ward list = `visitNo` for all API calls.

**APIs accepting plain visitNo** (no encrypted params needed):
- `query_cumulative_lab_data?visitNo=XXXXXXXX`
- `get_medSummary?visitNo=XXXXXXXX`
- `patient_treatments?visitNo=XXXXXXXX`
- `patient_drugs?visitNo=XXXXXXXX`
- `get_nursing_records?visitNo=XXXXXXXX` ← returns full admission history (not just current shift)

**Prerequisite**: Chrome MCP tab must be at `hapi.csh.org.tw` domain. Fetch from `his.csh.org.tw` context to `hapi.csh.org.tw` fails with CORS error.

**45-second JS timeout**: `javascript_tool` has a 45s execution limit. For large responses (e.g. `query_cumulative_lab_data` slow on first fetch), use the background pattern — fire fetch with `.then()` storing result into `window._var`, return immediately, read `window._var` in a later call.

```javascript
// Step 1: background fetch (returns immediately)
window._lab = null;
fetch('https://hapi.csh.org.tw/query_cumulative_lab_data?visitNo=34360938',
  {credentials:'include'}).then(r=>r.json()).then(d=>{window._lab=d;});
'fetching...';

// Step 2: check if done
window._lab?.length ?? 'not ready';

// Step 3: parse latest day
(() => {
  const num = window._lab.filter(d=>d.TranCode==='9');
  const dates = [...new Set(num.map(d=>(d.LabDate||'').slice(0,10)))].filter(Boolean).sort().reverse();
  const latest = dates[0];
  return num.filter(d=>(d.LabDate||'').startsWith(latest))
    .map(d=>`${d.ShortName}|${d.ReportValue}|${d.Unit}|${d.IsAbnormal?'⚠':''}`).join('\n');
})()
```

**Ward-wide parallel scan** (`Promise.all` — works within 45s for `patient_treatments`):
```javascript
const patients = [{bed:'MI01',visitNo:33958572}, /* ... */];
const results = await Promise.all(patients.map(async pt => {
  const data = await fetch(`https://hapi.csh.org.tw/patient_treatments?visitNo=${pt.visitNo}`,
    {credentials:'include'}).then(r=>r.json());
  return {bed: pt.bed, data};
}));
return JSON.stringify(results);
```

**Getting ward list via Chrome MCP** (no Playwright needed):
Direct fetch works: `get_inPatient?ward=MI` returns all patients with `VisitNo`/`RoomBed`/`PtName`. No UI interaction needed.

**get_io API** (confirmed):
- URL: `get_io?visitNo=XXXXXXXX&detail=Y` (requires `detail=Y`, NOT just visitNo)
- `hasAnyIoData=Y` only checks existence, returns `{"hasAnyIoData":"Y"}`, not detail
- Fields: `IO_DT`(YYYYMMDD), `MainEventType`(INPUT/OUTPUT), `Shift`, `OccurDate`, `EventType`, `Value1`, `Unit1`, `Value2`, `Unit2`, `ItemName`

**patient_orders API** (confirmed):
- URL: `patient_orders?chartno=XXXXXX` (requires `ChartNo` from `patient_info[0].ChartNo`, NOT visitNo)
- Returns ALL orders across all admissions — filter by `VisitNo` for current admission
- Key field: `ReportText` contains full radiology/pathology text reports (English)
- Other fields: `ItemName`, `醫囑類別`, `ItemCode`, `報告時間`, `執行狀態`
- 醫囑類別 values: MRI, 一般攝影, CT, 生化檢查, 血液檢查, 細菌檢查, etc.

**patient_info returns array** (confirmed):
- Response is array-like: access as `info[0].ChartNo`, not `info.ChartNo`

**Ventilator settings in nursing records** (confirmed):
- Record type: `VITALSIGN` (NOT 班務記錄)
- Format: `呼吸器：機型:EV300, Mode:PACV,呼吸次數:14次/分鐘,氧氣濃度:35％,吐氣末陽壓:8cmH2O,壓力:22cmH2O`
- Keywords: `氧氣濃度`=FiO2, `吐氣末陽壓`=PEEP, `呼吸次數`=RR, `壓力`=PC pressure, `Mode:`=mode
- Generated hourly automatically

**Intubation date in nursing records** (confirmed):
- Record type: `TUBE`
- Keyword: `置入.*氣管內管` in Content
- `RecordTime` = intubation datetime

**RT treatment codes** (patient_treatments.ItemCode, confirmed):
- `57001` 呼吸補助使用費 (ventilator/HFNC general)
- `5700301` on O2 with Nasal cannula
- `5702301` on BiPAP
- `57024` 呼吸器噴霧吸入治療/天　`57021` 噴霧吸入/次
- `57031` 濕化高流量氧氣治療 Daily care (HFNC, day 2+)
- `47041` Suction　`47090` VEST/HFCWO　`ZD52` ETT care
- `42011` 物理治療:中度治療-複雜　`PTM5` Passive ROM　`PTM6` Stretching

**Selecting a patient fires all 22 APIs at once**: Use `form_input` to select a patient's `visitNo` in the patient dropdown — HIS immediately fires all background API calls. Capture all URLs via `read_network_requests` and fetch whichever ones are needed. This replaces the Playwright 15-second wait loop entirely. The 22 APIs include: `patient_info`, `visit_history`, `get_io`, `get_pump_records`, `get_personal_note`, `patient_treatments`, `get_vital_sign`, `patient_body_record`, `get_pre_admin_orders`, `get_pharmacyReview_record`, `patient_orders`, `get_medSummary`, `get_nursing_records`, `patient_problems`, `med_allergy`, `allergy_cloud_query`, `patient_drugs`, `query_cumulative_lab_data`.

**Limitations**:
- `document.cookie` blocked by Chrome MCP (privacy protection)
- `read_network_requests` only captures actual network requests — browser HTTP cache hits are invisible. If an API doesn't appear in the log after selecting a patient, use `?visitNo=` direct fetch instead.
- After first fetch warms the cache, subsequent fetches for same visitNo return instantly (~1ms)

### PACS image retrieval

**Step 1 — get study list** (`get_oracle_pacs_study_list`):
- Requires `chartno` (NOT `visitNo`). `chartno` appears in `patient_info` response as `ChartNo`.
- Date format: `YYYY-MM-DD` (e.g. `2026-05-13`), NOT `YYYYMMDD`.
- Returns studies with `ACCESSION_NO`, `StudyDesc`, `StudyDateTime`.

```javascript
const data = await fetch('https://hapi.csh.org.tw/get_oracle_pacs_study_list?chartno=2937482',
  {credentials:'include'}).then(r=>r.json());
```

**Step 2 — get SOP instance UIDs** (`get_pacs_images`):
- Requires `chartno` + `dt` (YYYY-MM-DD).
- Returns array of objects with `sop_instance_uid`.

```javascript
const imgs = await fetch('https://hapi.csh.org.tw/get_pacs_images?chartno=2937482&dt=2026-05-13',
  {credentials:'include'}).then(r=>r.json());
```

**Step 3 — download JPEG via WADO** (no auth required — public endpoint):

```powershell
$uid = "1.2.392.200036.9107.307.35455.20260513.131829.1031420"
$url = "https://pacs.csh.org.tw/WebPush/WebPush.dll?PushWADO?requestType=WADO&contentType=image/jpeg&objectUID=$uid&rows=640"
$resp = Invoke-WebRequest -Uri $url -UseBasicParsing
[System.IO.File]::WriteAllBytes("tmp_cxr.jpg", $resp.Content)
```

The WADO endpoint does **not** require session cookies — PowerShell fetch works directly. Then use the `Read` tool on the saved JPG to display the image inline in chat.

**CORS note**: Fetching `pacs.csh.org.tw` from `hapi.csh.org.tw` browser context fails with CORS error. Use PowerShell for step 3.

### Reference / prototype
`csh_crawler.py` — original prototype (no login handling, no data parsing). Useful as reference for the raw Playwright select-option approach.

`discover_apis.py` — one-off script that logs all API endpoints fired during a patient page load. Run this to find new endpoints when the HIS system updates.

## Dependencies

Requires `playwright` (async API). Install browsers with:
```bash
pip install playwright
playwright install chromium
```

No `requirements.txt` exists; only dependency is `playwright`.

## HIS session

The `su=` token is generated by the HIS desktop application. If it expires (symptom: `"無法載入 XX 病房名單"`), renew it as follows:

**Token renewal procedure (computer-use)**:
1. Launch `%LOCALAPPDATA%\Apps\2.0\<hash>\hisclient.cloudclient.exe` via PowerShell (use computer-use to find and launch it, or use `Start-Process` with `Get-ChildItem -Recurse -Filter "hisclient.cloudclient.exe"`).
2. Request computer-use access to `hisclient.cloudclient.exe` (full tier).
3. Click the **H** button at bottom-left → a menu appears → click **住院電子病歷**.
4. Navigate: 護理站 → 選擇單位 → 選擇病人 → 右鍵 → **住院病人首頁** → HIS web opens in Edge.
5. Screenshot Edge URL bar; read the `su=XXXXXXXXXX` value.
6. Write the number to `his_token.txt` (one line, no prefix).

**Login**: If HIS web shows a login screen, use username/password (not certificate — certificate login fails with "憑證登人失敗"). Credentials are stored in `HIS_USERNAME` / `HIS_PASSWORD` env vars.

**Manual renewal**: same navigation as above; copy `su=XXXXXXXXXX` from Edge URL bar and paste the number into `his_token.txt`.
