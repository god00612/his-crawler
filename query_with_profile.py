"""
Use Chrome with existing user profile to access HIS with active session.
Connects Playwright to Chromium using Chrome's user data directory to inherit cookies.
"""
import asyncio
import json
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

# Use the existing user profile to inherit cookies/session
CHROME_USER_DATA = r"C:\Users\YUAN\AppData\Local\Google\Chrome\User Data"

# Try several su tokens - get current time-based token
import time
SU_TOKEN = str(int(time.time() * 1000))

# The base HIS URL without su (will redirect to login if needed, but cookies should keep us logged in)
HIS_BASE = "https://hapi.csh.org.tw/orders/#/adm_patient_medical_info"

CAPTURE_ENDPOINTS = {
    "patient_info", "patient_drugs", "get_medSummary",
    "patient_orders", "patient_treatments", "get_pump_records",
    "get_vital_sign", "get_nursing_records", "get_io",
    "SOFA_score", "get_pacs_images", "query_cumulative_lab_data",
    "patient_problems", "med_allergy", "get_inPatient",
    "get_inPatient_wardList",
}

TARGET_BED = "MI01"
WARD_PREFIX = "MI"


async def main():
    captured = {}

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
            print(f"[攔截] {endpoint} size={len(str(data))}", file=sys.stderr)
        except Exception:
            pass

    async with async_playwright() as p:
        # Launch Chrome with existing user profile
        print("[1] 啟動 Chrome（使用現有 Profile）...", file=sys.stderr)
        try:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=CHROME_USER_DATA,
                channel="chrome",
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as e:
            print(f"[錯誤] 無法啟動: {e}", file=sys.stderr)
            print(json.dumps({"status": "error", "message": str(e)}))
            return

        # Get or create page
        pages = browser.pages
        if pages:
            page = pages[0]
        else:
            page = await browser.new_page()

        page.on("response", handle_response)

        print(f"[2] 導覽至 HIS...", file=sys.stderr)
        await page.goto(f"{HIS_BASE}?su={SU_TOKEN}")
        await page.wait_for_timeout(3000)

        title = await page.title()
        print(f"[2] 頁面標題: {title}", file=sys.stderr)
        url = page.url
        print(f"[2] 當前 URL: {url}", file=sys.stderr)

        # Check if we're on login page
        body_text = await page.evaluate("document.body.innerText")
        print(f"[2] 頁面文字前100字: {body_text[:100]}", file=sys.stderr)

        if "登入" in title or "login" in url.lower():
            print("[錯誤] 需要登入，session 已過期", file=sys.stderr)
            await browser.close()
            print(json.dumps({"status": "error", "message": "Session expired, please login"}))
            return

        # Try to open ward list
        print(f"[3] 嘗試開啟 {WARD_PREFIX} 病房名單...", file=sys.stderr)

        # Try to find changePatientList select
        try:
            cs = page.locator("select:has(option[value='changePatientList'])").first
            await cs.wait_for(state="attached", timeout=10000)
            await cs.select_option("changePatientList", force=True)
            await cs.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
            print("[3] 觸發更換名單成功", file=sys.stderr)
        except Exception as e:
            print(f"[3] 觸發失敗: {e}", file=sys.stderr)

        await page.wait_for_timeout(2000)

        # Select 護理站 radio
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
            print(f"[3] radio 失敗: {e}", file=sys.stderr)

        await page.wait_for_timeout(1000)

        # Select ward MI
        for attempt in range(8):
            try:
                selects = page.locator("select")
                count = await selects.count()
                found = False
                for i in range(count):
                    sel = selects.nth(i)
                    inner = await sel.inner_html()
                    if f">{WARD_PREFIX}<" in inner or f'value="{WARD_PREFIX}"' in inner:
                        await sel.select_option(WARD_PREFIX, force=True)
                        await sel.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
                        print(f"[3] 已選 {WARD_PREFIX}", file=sys.stderr)
                        found = True
                        break
                if found:
                    break
                await page.wait_for_timeout(800)
            except Exception as e:
                print(f"[3] 第 {attempt+1} 次失敗: {e}", file=sys.stderr)
                await page.wait_for_timeout(800)

        await page.wait_for_timeout(500)

        # Click 確定名單
        for btn_text in ["確定名單", "確定", "OK"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                await btn.wait_for(state="visible", timeout=2000)
                await btn.click(force=True)
                print(f"[3] 已點「{btn_text}」", file=sys.stderr)
                break
            except Exception:
                continue

        await page.wait_for_timeout(2000)

        # Find MI01 in patient list
        patients = []
        bed_re = re.compile(r"^(MI\d{2,3})\s*(.*)", re.IGNORECASE)
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
                val = (await opt.get_attribute("value") or "").strip()
                m = bed_re.match(text)
                if m:
                    found = True
                    patients.append({"bed": m.group(1).upper(), "name": m.group(2).strip(), "internal_id": val})
            if found:
                break

        print(f"[3] 找到 {len(patients)} 位病人", file=sys.stderr)

        if not patients:
            await browser.close()
            print(json.dumps({"status": "error", "message": "無法載入病房名單"}))
            return

        target = next((p for p in patients if p["bed"] == TARGET_BED), None)
        if not target:
            await browser.close()
            print(json.dumps({"status": "error", "message": f"找不到 {TARGET_BED}", "patients": patients}))
            return

        # Switch to target patient
        captured.clear()
        print(f"[4] 切換至 {TARGET_BED} {target.get('name', '')}...", file=sys.stderr)
        internal_id = target.get("internal_id", "")
        try:
            ts = page.locator(f"select:has(option[value='{internal_id}'])").first
            await ts.select_option(value=internal_id, force=True)
            await ts.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
        except Exception as e:
            print(f"[警告] 切換失敗: {e}", file=sys.stderr)

        print("[5] 等待 API 回傳（20 秒）...", file=sys.stderr)
        await page.wait_for_timeout(20000)
        await browser.close()

    print(json.dumps({"status": "success", "bed": TARGET_BED, "patient": target, "captured_endpoints": list(captured.keys()), "data": captured}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
