import asyncio
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from playwright.async_api import async_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

USERNAME = os.environ.get("HIS_USERNAME", "你的員工帳號")
PASSWORD = os.environ.get("HIS_PASSWORD", "你的密碼")

TOKEN_FILE = Path(__file__).parent / "his_token.txt"
FALLBACK_TOKEN = "1778641978348"
HIS_URL = "https://hapi.csh.org.tw/orders/#/adm_patient_medical_info"


def _get_his_url() -> str:
    """每次查詢前動態讀取 token，允許 Claude 更新 his_token.txt 後立即生效。"""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return f"{HIS_URL}?su={token}"
    return f"{HIS_URL}?su={FALLBACK_TOKEN}"

# ── 所有要攔截的 API（對應畫面上的每個區塊）───────────────────
CAPTURE_ENDPOINTS = {
    # 病人基本資料區
    "patient_info":              "病人基本資料",
    "patient_body_record":       "身體測量",       # 體重/身高
    "med_allergy":               "過敏藥物",
    "allergy_cloud_query":       "雲端過敏",
    # 住院資訊
    "visit_history":             "就診記錄",
    "get_inPatient":             "住院資訊",
    "get_inPatient_divNoList":   "住院分科清單",
    "get_inPatient_wardList":    "病房名單",
    # 問題清單
    "patient_problems":          "問題清單",
    # 藥物
    "patient_drugs":             "用藥清單",
    "get_medSummary":            "交班紀錄",
    "get_pharmacyReview_record": "藥師審閱",
    "get_pre_admin_orders":      "入院前醫囑",
    # 醫囑 / 處置
    "patient_orders":            "醫囑",
    "patient_treatments":        "護理措施",
    "get_pre_admin_orders":      "入院前醫囑",
    # 生命徵象
    "get_vital_sign":            "生命徵象",
    "patient_body_record":       "身體測量",
    # 護理紀錄
    "get_nursing_records":       "護理紀錄",
    "get_personal_note":         "個人備注",
    # 輸出入 / 幫浦
    "get_io":                    "輸出入量",
    "get_pump_records":          "輸液幫浦",
    # 評分
    "SOFA_score":                "SOFA評分",
    # 影像
    "get_pacs_images":           "影像報告",
    # 檢驗
    "query_cumulative_lab_data": "累積檢驗",
}

# ── 檢驗分類 ──────────────────────────────────────────────────
CATEGORY_MAP = {
    "Blood culture": "血液培養",
    "Urine": "尿液培養", "Stool": "糞便培養", "Sputum": "痰液培養",
    "WBC": "血液常規", "Hgb": "血液常規", "Hct": "血液常規",
    "PLT": "血液常規", "Neutrophil": "血液常規", "RBC": "血液常規",
    "Na": "電解質", "K ": "電解質", "K,": "電解質", "Cl": "電解質",
    "BUN": "腎功能", "Creatinine": "腎功能", "eGFR": "腎功能",
    "ALT": "肝功能", "AST": "肝功能", "T-Bil": "肝功能", "ALP": "肝功能",
    "Glucose": "血糖",
    "CRP": "發炎指標", "PCT": "發炎指標", "Lactate": "發炎指標",
    "PT ": "凝血功能", "PT,": "凝血功能", "aPTT": "凝血功能",
    "INR": "凝血功能", "D-Dimer": "凝血功能",
    "Troponin": "心臟指標", "BNP": "心臟指標", "CK": "心臟指標",
    "ABG": "血氣分析", "pH": "血氣分析", "PaO2": "血氣分析", "PaCO2": "血氣分析",
    "HbA1c": "血糖", "Insulin": "血糖",
    "Ammonia": "肝功能", "Albumin": "肝功能",
    "Mg": "電解質", "Phos": "電解質", "Ca": "電解質",
}

ORGAN_SYSTEM_EXACT = {
    "NA": "電解質", "K": "電解質", "P": "電解質", "CL": "電解質",
    "CK": "心臟",
    "ANC": "血液",
    "PCT": "感染",
    "ALB": "肝臟",
    "TG": "代謝",
}

ORGAN_SYSTEM_PATTERNS = [
    # 心臟
    (["troponin", "nt-probnp", "bnp", "ck-mb"], "心臟"),
    # 腎臟
    (["bun", "cre", "egfr", "uric acid", "creatinine"], "腎臟"),
    # 肝臟
    (["ast", "got", "alt", "gpt", "bilirubin", "albumin", "alp", "ggt", "ldh",
      "ammonia", "r-gt", "γ-gt"], "肝臟"),
    # 胰臟
    (["lipase", "amylase", "amy"], "胰臟"),
    # 血液（CBC）
    (["w.b.c count", "r.b.c count", "hb.", "ht.", "hematocrite", "hct",
      "mcv", "mch", "mchc", "platelet", "neutrophil", "lymphocyte",
      "monocyte", "basophil", "eosinophil", "reticulocyte", "band",
      "normoblast", "blast"], "血液"),
    # 凝血
    (["pro-time", "aptt", "a.p.t.t", "ptt", "d-dimer", "fibrinogen", "inr", "act"], "凝血"),
    # 感染
    (["c.r.p", "crp", "procalcitonin", "esr", "il-6"], "感染"),
    # 代謝
    (["lacticacid", "lactic", "glucose", "hba1c", "insulin", "ketone",
      "one touch", "triglyceride", "eag"], "代謝"),
    # 電解質
    (["calcium", "magnesium", "phospho", "chloride", "bicarbonate",
      "potassium", "sodium", "parathyroid"], "電解質"),
    # ABG（含靜脈血氣 Vein 變體）
    (["pco2", "po2", "so2", "base excess", "hco3", "o2sat", "tco2", "be", "ph"], "ABG"),
    # 微生物
    (["culture", "培養", "gram stain", "mdr", "stool for screen",
      "blood culture", "aerobic", "anaerobic", "fungus"], "微生物"),
    # 感染血清
    (["mycoplasma", "chlamydia", "influenza", "covid", "igm", "igg",
      "antibody", "streptococcus", "legionella", "rsv", "adenovirus",
      "rna", "sars", "pcr"], "感染血清"),
    # 甲狀腺
    (["tsh", "free t4", "free-t4", "t3", "thyroid"], "甲狀腺"),
    # 鐵代謝
    (["ferritin", "iron", "tibc", "transferrin"], "鐵代謝"),
    # 內分泌
    (["acth", "cortisol", "insulin", "aldosterone"], "內分泌"),
]


def _organ_system(item: str, specimen_code: str = "") -> str:
    # 尿液檢體 → 尿液
    if specimen_code.upper() in ("UR", "URI", "URINE"):
        return "尿液"
    # 痰液/培養檢體
    if specimen_code.upper() in ("SPT", "SPUTUM"):
        return "微生物"
    # 血液培養
    if specimen_code.upper() in ("BLOOD", "BLD", "BLDC") and "culture" in item.lower():
        return "微生物"

    name = item.strip()
    name_lower = name.lower()
    # 完全符合（區分大小寫）
    if name in ORGAN_SYSTEM_EXACT:
        return ORGAN_SYSTEM_EXACT[name]
    # 子字串比對（不分大小寫，第一個命中優先）
    for keywords, system in ORGAN_SYSTEM_PATTERNS:
        if any(kw in name_lower for kw in keywords):
            return system
    return "其他"


SHIFT_WINDOWS = {
    "白天": (7, 0, 15, 0),
    "小夜": (15, 0, 23, 0),
    "大夜": (23, 0, 7, 0),
}

LAB_DATE_FORMATS = [
    "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y%m%d %H%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M",
    "%Y/%m/%d", "%Y-%m-%d",
]

# 生命徵象欄位中英對照
VITAL_FIELD_MAP = {
    "Temperature": "體溫", "Temp": "體溫",
    "SBP": "收縮壓", "DBP": "舒張壓", "BloodPressure": "血壓",
    "HeartRate": "心跳", "Pulse": "脈搏",
    "RespiratoryRate": "呼吸", "RR": "呼吸",
    "SpO2": "血氧", "OxygenSaturation": "血氧",
    "MAP": "平均動脈壓",
    "MeasureTime": "測量時間", "RecordTime": "記錄時間",
    "OccurDate": "發生時間",
    "GCS": "GCS意識",
    "Pain": "疼痛評分",
    "CVP": "中心靜脈壓",
}


# ── 工具函式 ──────────────────────────────────────────────────

def _parse_date(s: str):
    if not s:
        return None
    s = str(s).strip()
    for fmt in LAB_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _detect_abnormal(item_name: str, report_text: str) -> bool:
    if not report_text:
        return False
    t = report_text.strip()
    if re.search(r"\bH\b|\bL\b", t):
        return True
    if any(m in t for m in ["↑", "↓", "*", "PANIC", "CRITICAL", "陽性", "Positive", "positive"]):
        return True
    lower_item = item_name.lower()
    if any(x in lower_item for x in ["culture", "培養", "screen"]) and "No growth" not in t and "no growth" not in t.lower() and "陰性" not in t:
        return True
    return False


def _shift_window(shift: str):
    if shift not in SHIFT_WINDOWS:
        return None, None
    today = datetime.now().replace(microsecond=0)
    sh, sm, eh, em = SHIFT_WINDOWS[shift]
    if shift == "大夜":
        return (
            (today - timedelta(days=1)).replace(hour=sh, minute=sm, second=0),
            today.replace(hour=eh, minute=em, second=0),
        )
    return today.replace(hour=sh, minute=sm, second=0), today.replace(hour=eh, minute=em, second=0)


def _in_shift(dt, ws, we) -> bool:
    if dt is None or ws is None:
        return True
    return ws <= dt < we


def _categorize_lab(item_name: str) -> str:
    for key, cat in CATEGORY_MAP.items():
        if key.lower() in item_name.lower():
            return cat
    return "其他"


def _flatten(obj, prefix="", depth=0) -> dict:
    """遞迴展開 dict/list 取第一層有意義的值。"""
    if depth > 3:
        return {prefix: str(obj)[:100]}
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k, depth + 1))
    elif isinstance(obj, list):
        if obj:
            out[prefix] = f"[{len(obj)} 筆]"
    else:
        out[prefix] = str(obj)[:200] if obj is not None else ""
    return out


# ── 各區塊解析函式 ────────────────────────────────────────────

def _parse_patient_info(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {"raw": str(data)[:200]}

    def pick(*keys):
        for k in keys:
            for dk, dv in data.items():
                if k.lower() == dk.lower() and dv:
                    return str(dv)
        return ""

    return {
        "姓名":   pick("PatientName", "Name", "CName", "patName"),
        "病歷號": pick("PatientID", "ChartNo", "MedicalNo", "chartNo"),
        "性別":   pick("Sex", "Gender"),
        "年齡":   pick("Age"),
        "體重":   pick("Weight", "BW"),
        "血型":   pick("BloodType", "ABO"),
        "診斷":   pick("Diagnosis", "AdmDiagnosis", "admDiag"),
        "科別":   pick("Division", "DivName", "divName"),
        "主治醫師": pick("AttendingDoctor", "DoctorName", "drName", "attendDr"),
        "入院日期": pick("AdmDate", "AdmissionDate", "admDate"),
        "住院天數": pick("AdmDays", "LOS"),
    }


def _parse_body_record(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if v and str(v).strip()}


def _parse_allergy(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if not data:
        return {"過敏": "無記錄"}
    if isinstance(data, list) and len(data) == 0:
        return {"過敏": "無"}
    return {"過敏資料": str(data)[:500]}


def _parse_visit_history(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    out = []
    for r in records[:10]:  # 最近 10 筆
        if isinstance(r, dict):
            date = r.get("VisitDate") or r.get("Date") or r.get("AdmDate") or ""
            diag = r.get("Diagnosis") or r.get("DiagName") or r.get("admDiag") or ""
            dept = r.get("DivName") or r.get("Department") or ""
            out.append({"日期": str(date), "科別": str(dept), "診斷": str(diag)[:80]})
    return {"就診記錄": out, "筆數": len(records)}


def _parse_problems(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if not data:
        return {"問題清單": "無"}
    records = data if isinstance(data, list) else [data]
    out = []
    for r in records:
        if isinstance(r, dict):
            prob = r.get("Problem") or r.get("ProblemName") or r.get("Content") or str(r)[:80]
            date = r.get("OnsetDate") or r.get("Date") or ""
            out.append({"問題": str(prob), "日期": str(date)})
    return {"問題清單": out}


def _parse_drugs(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    drugs = []
    for r in records:
        if not isinstance(r, dict):
            continue
        name  = r.get("DrugName") or r.get("ItemName") or r.get("Name") or r.get("drugName") or ""
        dose  = r.get("Dose") or r.get("Dosage") or r.get("dose") or ""
        freq  = r.get("Frequency") or r.get("Freq") or r.get("freq") or ""
        route = r.get("Route") or r.get("route") or ""
        start = r.get("StartDate") or r.get("OrderDate") or ""
        if name:
            drugs.append({"藥名": str(name), "劑量": str(dose), "頻率": str(freq),
                          "途徑": str(route), "開始": str(start)})
    return {"用藥清單": drugs, "藥物數": len(drugs)}


def _parse_med_summary(raw) -> dict:
    # get_medSummary returns 醫師交班紀錄, not drug data
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        out.append({
            "時間": str(r.get("RecordTime", "")),
            "醫師": str(r.get("RecordUser", "")),
            "類型": str(r.get("ProgressType", "")),
            "班別": str(r.get("ShiftType", "")),
            "內容": str(r.get("Summary", ""))[:2000],
        })
    return {"交班紀錄": out, "筆數": len(out)}


def _parse_orders(raw, shift=None) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    ws, we = _shift_window(shift) if shift else (None, None)
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        date_str = r.get("OrderDate") or r.get("Date") or r.get("orderDate") or ""
        dt = _parse_date(date_str)
        if shift and not _in_shift(dt, ws, we):
            continue
        item  = r.get("OrderName") or r.get("ItemName") or r.get("Content") or r.get("orderName") or ""
        freq  = r.get("Frequency") or r.get("Freq") or ""
        status = r.get("Status") or r.get("OrderStatus") or ""
        out.append({"時間": str(date_str), "醫囑": str(item)[:120],
                    "頻率": str(freq), "狀態": str(status)})
    return {"醫囑": out, "筆數": len(out)}


def _parse_treatments(raw, shift=None) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    ws, we = _shift_window(shift) if shift else (None, None)
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        date_str = r.get("Date") or r.get("RecordDate") or r.get("OrderDate") or ""
        dt = _parse_date(date_str)
        if shift and not _in_shift(dt, ws, we):
            continue
        name   = r.get("TreatmentName") or r.get("ItemName") or r.get("Content") or r.get("orderName") or ""
        freq   = r.get("Frequency") or r.get("Freq") or ""
        status = r.get("Status") or ""
        out.append({"時間": str(date_str), "處置": str(name)[:120],
                    "頻率": str(freq), "狀態": str(status)})
    return {"護理措施": out, "筆數": len(out)}


def _parse_vital_signs(raw, shift=None) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    ws, we = _shift_window(shift) if shift else (None, None)

    shift_records = []
    all_latest = None

    for r in records:
        if not isinstance(r, dict):
            continue
        date_str = r.get("MeasureTime") or r.get("RecordTime") or r.get("OccurDate") or r.get("Date") or ""
        dt = _parse_date(date_str)
        all_latest = r  # 保留最後一筆（通常 list 由舊到新）
        if shift and not _in_shift(dt, ws, we):
            continue
        # 只取有意義的欄位並翻譯
        clean = {}
        for k, v in r.items():
            if v is None or str(v).strip() in ("", "null", "None"):
                continue
            label = VITAL_FIELD_MAP.get(k, k)
            clean[label] = str(v)
        if len(clean) > 1:
            shift_records.append(clean)

    # 最新一筆（整體）
    latest_clean = {}
    if all_latest:
        for k, v in all_latest.items():
            if v is None or str(v).strip() in ("", "null", "None"):
                continue
            latest_clean[VITAL_FIELD_MAP.get(k, k)] = str(v)

    return {
        "最新生命徵象": latest_clean,
        "班別內筆數": len(shift_records),
        "班別內記錄": shift_records[-5:] if shift_records else [],  # 最近 5 筆
        "總筆數": len(records),
    }


def _parse_nursing_records(raw, shift=None) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    ws, we = _shift_window(shift) if shift else (None, None)
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        date_str = r.get("RecordTime") or r.get("Date") or r.get("NursingDate") or r.get("CreateTime") or ""
        dt = _parse_date(date_str)
        if shift and not _in_shift(dt, ws, we):
            continue
        content = (r.get("Content") or r.get("NursingContent") or r.get("Note")
                   or r.get("Record") or r.get("Description") or "")
        nurse   = r.get("NurseName") or r.get("Nurse") or r.get("Creator") or ""
        out.append({"時間": str(date_str), "護士": str(nurse), "內容": str(content)[:500]})
    return {"護理紀錄": out, "筆數": len(out)}


def _parse_pump_records(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        drug  = r.get("DrugName") or r.get("Name") or r.get("FluidName") or ""
        rate  = r.get("Rate") or r.get("FlowRate") or r.get("Dose") or ""
        start = r.get("StartTime") or r.get("Date") or ""
        pump  = r.get("PumpNo") or r.get("PumpID") or ""
        out.append({"幫浦": str(pump), "藥物": str(drug)[:80],
                    "速率": str(rate), "開始": str(start)})
    return {"幫浦記錄": out, "筆數": len(out)}


def _parse_sofa(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, list):
        data = data[-1] if data else {}
    if not isinstance(data, dict):
        return {"SOFA": str(data)[:200]}
    score = data.get("TotalScore") or data.get("Total") or data.get("SOFAScore") or ""
    return {"SOFA總分": str(score), "詳細": {k: str(v)[:30] for k, v in data.items() if v}}


def _parse_io(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if not data:
        return {"輸出入量": "無資料"}
    if isinstance(data, list) and len(data) == 0:
        return {"輸出入量": "無記錄"}
    return {"輸出入量": str(data)[:500]}


def _parse_pacs(raw) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    out = []
    for r in records[:10]:
        if not isinstance(r, dict):
            continue
        exam  = r.get("ExamName") or r.get("ItemName") or r.get("Name") or ""
        date  = r.get("ExamDate") or r.get("Date") or ""
        report = r.get("Report") or r.get("Impression") or r.get("Finding") or ""
        out.append({"日期": str(date), "項目": str(exam)[:60], "報告": str(report)[:200]})
    return {"影像報告": out, "筆數": len(records)}


def _parse_lab(raw, shift=None) -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    records = data if isinstance(data, list) else []
    ws, we = _shift_window(shift) if shift else (None, None)
    categories: dict = {}
    abnormal = []
    for entry in records:
        if not isinstance(entry, dict):
            continue
        lab_date = entry.get("LabDate", "")
        dt = _parse_date(lab_date)
        if shift and not _in_shift(dt, ws, we):
            continue

        specimen_code = entry.get("SpecimenCode", "")
        tran = str(entry.get("TranCode", ""))
        if tran == "8":
            # 培養/報告類：Item + ReportText
            item   = entry.get("Item", "")
            report = entry.get("ReportText", "").replace("\r\n", " ").strip()
            value, unit, ref = "", "", ""
            is_ab  = _detect_abnormal(item, report) or bool(entry.get("IsMDR"))
            cat    = _categorize_lab(item)
        else:
            # 數值檢驗類：ShortName + ReportValue + Unit
            item  = entry.get("ShortName", "") or entry.get("Item", "")
            value = str(entry.get("ReportValue", ""))
            unit  = entry.get("Unit", "")
            lo    = entry.get("MinValue", "")
            hi    = entry.get("MaxValue", "")
            ref   = f"{lo}–{hi}" if (lo or hi) else ""
            report = f"{value} {unit}  [{ref}]".strip() if ref else f"{value} {unit}".strip()
            is_ab  = bool(entry.get("IsAbnormal")) or bool(entry.get("IsMaxPanicValue")) or bool(entry.get("IsMinPanicValue"))
            cat    = entry.get("MedTypeGroup", "") or _categorize_lab(item)

        organ = _organ_system(item, specimen_code)
        rec = {
            "organ_system": organ,
            "item": item,
            "value": value,
            "unit": unit,
            "ref": ref,
            "report": report,
            "lab_date": lab_date,
            "abnormal": is_ab,
        }
        categories.setdefault(cat, []).append(rec)
        if is_ab:
            abnormal.append(f"{item}: {report[:200]}")
    return {
        "lab_categories": categories,
        "abnormal_summary": abnormal,
        "total_items": sum(len(v) for v in categories.values()),
        "abnormal_count": len(abnormal),
    }


def _combine_all(captured: dict, shift=None) -> dict:
    """整合所有攔截到的 API。"""
    r: dict = {}

    parsers = {
        "patient_info":              lambda d: ("病人基本資料", _parse_patient_info(d)),
        "patient_body_record":       lambda d: ("身體測量",     _parse_body_record(d)),
        "med_allergy":               lambda d: ("過敏藥物",     _parse_allergy(d)),
        "allergy_cloud_query":       lambda d: ("雲端過敏",     _parse_allergy(d)),
        "visit_history":             lambda d: ("就診記錄",     _parse_visit_history(d)),
        "get_inPatient":             lambda d: ("住院資訊",     {"資料": {k: str(v)[:80] for k, v in (d.get("data", d) if isinstance(d, dict) else {}).items() if v}}),
        "get_inPatient_divNoList":   lambda d: ("住院分科清單", {"raw": str(d)[:300]}),
        "patient_problems":          lambda d: ("問題清單",     _parse_problems(d)),
        "patient_drugs":             lambda d: ("用藥清單",     _parse_drugs(d)),
        "get_medSummary":            lambda d: ("交班紀錄",     _parse_med_summary(d)),
        "get_pharmacyReview_record": lambda d: ("藥師審閱",     {"raw": str(d)[:300]}),
        "get_pre_admin_orders":      lambda d: ("入院前醫囑",   {"raw": str(d)[:300]}),
        "patient_orders":            lambda d: ("醫囑",         _parse_orders(d, shift)),
        "patient_treatments":        lambda d: ("護理措施",     _parse_treatments(d, shift)),
        "get_vital_sign":            lambda d: ("生命徵象",     _parse_vital_signs(d, shift)),
        "get_nursing_records":       lambda d: ("護理紀錄",     _parse_nursing_records(d, shift)),
        "get_personal_note":         lambda d: ("個人備注",     {"備注": str(d)[:300]}),
        "get_io":                    lambda d: ("輸出入量",     _parse_io(d)),
        "get_pump_records":          lambda d: ("輸液幫浦",     _parse_pump_records(d)),
        "SOFA_score":                lambda d: ("SOFA評分",     _parse_sofa(d)),
        "get_pacs_images":           lambda d: ("影像報告",     _parse_pacs(d)),
        "query_cumulative_lab_data": lambda d: ("累積檢驗",     _parse_lab(d, shift)),
    }

    for endpoint, fn in parsers.items():
        if endpoint in captured:
            try:
                label, parsed = fn(captured[endpoint])
                r[label] = parsed
            except Exception as e:
                r[endpoint] = {"parse_error": str(e)}

    r["已攔截API"] = list(captured.keys())
    return r


# ── 登入 ──────────────────────────────────────────────────────

async def _login_if_needed(page):
    try:
        pwd = page.locator("input[type='password']")
        await pwd.wait_for(state="attached", timeout=3000)
        if await pwd.is_visible():
            print("[登入] 填寫帳密...", file=sys.stderr)
            await page.locator("input[type='text'], input[name*='user']").first.fill(USERNAME)
            await pwd.fill(PASSWORD)
            await page.locator("button[type='submit'], button:has-text('登入')").first.click()
            await page.wait_for_timeout(2000)
    except Exception:
        print("[登入] 系統已登入。", file=sys.stderr)


# ── 更換名單流程 ───────────────────────────────────────────────

async def _open_ward_list(page, ward_prefix: str) -> list[dict]:
    ward_prefix = ward_prefix.upper()

    print(f"[更換名單] 觸發對話框...", file=sys.stderr)
    try:
        cs = page.locator("select:has(option[value='changePatientList'])").first
        await cs.select_option("changePatientList", force=True)
        await cs.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
    except Exception as e:
        print(f"[更換名單] 觸發失敗: {e}", file=sys.stderr)
        return []
    await page.wait_for_timeout(1500)

    print(f"[更換名單] 選護理站 radio...", file=sys.stderr)
    try:
        radios = page.locator("input[type='radio']")
        count = await radios.count()
        for i in range(count):
            r = radios.nth(i)
            rid = await r.get_attribute("id") or ""
            label_text = ""
            if rid:
                try:
                    label_text = await page.locator(f"label[for='{rid}']").inner_text(timeout=500)
                except Exception:
                    pass
            if "護理站" in label_text or i == 0:
                await r.click(force=True)
                break
    except Exception as e:
        print(f"[更換名單] radio 失敗: {e}", file=sys.stderr)
    await page.wait_for_timeout(500)

    print(f"[更換名單] 選單位 {ward_prefix}...", file=sys.stderr)
    for attempt in range(8):
        try:
            selects = page.locator("select")
            count = await selects.count()
            for i in range(count):
                sel = selects.nth(i)
                inner = await sel.inner_html()
                if f">{ward_prefix}<" in inner or f'value="{ward_prefix}"' in inner:
                    await sel.select_option(ward_prefix, force=True)
                    await sel.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
                    print(f"[更換名單] 已選 {ward_prefix}。", file=sys.stderr)
                    break
            else:
                await page.wait_for_timeout(800)
                continue
            break
        except Exception as e:
            print(f"[更換名單] 第 {attempt+1} 次失敗: {e}", file=sys.stderr)
            await page.wait_for_timeout(800)
    await page.wait_for_timeout(500)

    print(f"[更換名單] 確定名單...", file=sys.stderr)
    for btn_text in ["確定名單", "確定", "OK"]:
        try:
            btn = page.locator(f"button:has-text('{btn_text}')").first
            await btn.wait_for(state="visible", timeout=2000)
            await btn.click(force=True)
            print(f"[更換名單] 已點「{btn_text}」。", file=sys.stderr)
            break
        except Exception:
            continue
    await page.wait_for_timeout(2000)

    patients = []
    bed_re = re.compile(r"^(" + re.escape(ward_prefix) + r"\d{2,3})\s*(.*)", re.IGNORECASE)
    selects = page.locator("select")
    sel_count = await selects.count()
    for si in range(sel_count):
        sel = selects.nth(si)
        options = sel.locator("option")
        opt_count = await options.count()
        found = False
        for oi in range(opt_count):
            opt = options.nth(oi)
            text = (await opt.text_content() or "").strip()
            val  = (await opt.get_attribute("value") or "").strip()
            m = bed_re.match(text)
            if m:
                found = True
                patients.append({"bed": m.group(1).upper(), "name": m.group(2).strip(), "internal_id": val})
        if found:
            break

    print(f"[更換名單] 擷取到 {len(patients)} 位病人。", file=sys.stderr)
    return sorted(patients, key=lambda x: x["bed"])


async def _open_ward_list_with_retry(page, ward_prefix: str, max_retries: int = 2) -> list[dict]:
    """_open_ward_list 的 retry 包裝：回傳空清單時重新載入頁面再試。"""
    for attempt in range(max_retries + 1):
        patients = await _open_ward_list_with_retry(page, ward_prefix)
        if patients:
            return patients
        if attempt < max_retries:
            print(f"[更換名單] 第 {attempt + 1} 次取得 0 位病人，重試...", file=sys.stderr)
            await page.goto(_get_his_url())
            await _login_if_needed(page)
            await page.wait_for_timeout(2000)
    return []


# ── 公開 API ──────────────────────────────────────────────────

async def fetch_ward_patient_list(ward_prefix: str) -> dict:
    ward_prefix = ward_prefix.upper()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await (await browser.new_context()).new_page()
        print("[1] 載入 HIS 頁面...", file=sys.stderr)
        await page.goto(_get_his_url())
        await _login_if_needed(page)
        await page.wait_for_timeout(2000)
        patients = await _open_ward_list_with_retry(page, ward_prefix)
        await browser.close()
    return {
        "query_type": "ward_list",
        "ward": ward_prefix,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": "success",
        "patient_count": len(patients),
        "patients": patients,
    }


async def fetch_patient_lab_data(target_bed: str, shift: str | None = None) -> dict:
    """向後相容。"""
    return await fetch_patient_all_data(target_bed, shift=shift)


async def fetch_patient_all_data(target_bed: str, shift: str | None = None) -> dict:
    """
    完整撈取指定床號所有畫面可見資料。
    攔截全部 API：檢驗、生命徵象、護理紀錄、用藥、醫囑、幫浦、SOFA、影像等。
    """
    target_bed = target_bed.upper()
    ward_match = re.match(r"[A-Za-z]+", target_bed)
    ward_prefix = ward_match.group().upper() if ward_match else target_bed[:2].upper()

    captured: dict = {}

    async def handle_response(response):
        url = response.url
        if "csh.org.tw" not in url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        endpoint = url.split("?")[0].split("/")[-1]
        if endpoint not in CAPTURE_ENDPOINTS:
            return
        try:
            data = await response.json()
            captured[endpoint] = data
            label = CAPTURE_ENDPOINTS[endpoint]
            print(f"[攔截] {label}（{endpoint}）size={len(str(data))}", file=sys.stderr)
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        page.on("response", handle_response)

        print(f"[1] 載入 HIS 頁面（目標：{target_bed}）...", file=sys.stderr)
        await page.goto(_get_his_url())
        await _login_if_needed(page)
        await page.wait_for_timeout(2000)

        patients = await _open_ward_list_with_retry(page, ward_prefix)
        if not patients:
            await browser.close()
            return {"query_type": "bed_all", "bed": target_bed, "status": "error",
                    "message": f"無法載入 {ward_prefix} 病房名單"}

        target_patient = next((p for p in patients if p["bed"] == target_bed), None)
        if not target_patient:
            await browser.close()
            return {"query_type": "bed_all", "bed": target_bed, "status": "error",
                    "message": f"找不到床號 {target_bed}，目前病房：{[p['bed'] for p in patients]}"}

        # 保留頁面初始載入的 API（su= URL 可能已預載目標病人資料）
        initial_captured = dict(captured)
        captured.clear()

        print(f"[3] 切換至 {target_bed} {target_patient.get('name','')}...", file=sys.stderr)
        internal_id = target_patient.get("internal_id", "")
        try:
            ts = page.locator(f"select:has(option[value='{internal_id}'])").first
            await ts.select_option(value=internal_id, force=True)
            await ts.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
        except Exception as e:
            print(f"[警告] 切換失敗: {e}", file=sys.stderr)

        print("[4] 等待所有 API 回傳（15 秒）...", file=sys.stderr)
        await page.wait_for_timeout(15000)
        await browser.close()

    if not captured:
        if initial_captured:
            # su= URL 預載的就是目標病人，直接使用初始資料
            print(f"[提示] 使用頁面初始載入資料（共 {len(initial_captured)} 個 API）", file=sys.stderr)
            captured.update(initial_captured)
        else:
            return {"query_type": "bed_all", "bed": target_bed, "status": "error",
                    "message": "未攔截到任何 API，請確認 session 是否有效"}

    result = _combine_all(captured, shift=shift)
    result.update({
        "query_type": "bed_all",
        "bed": target_bed,
        "patient_name": target_patient.get("name"),
        "status": "success",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })
    if shift and shift in SHIFT_WINDOWS:
        ws, we = _shift_window(shift)
        result["shift_filter"] = shift
        result["shift_window"] = f"{ws.strftime('%Y-%m-%dT%H:%M')} ~ {we.strftime('%Y-%m-%dT%H:%M')}"
    return result


if __name__ == "__main__":
    import json
    result = asyncio.run(fetch_patient_all_data("MI01"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
