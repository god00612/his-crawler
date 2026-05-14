"""
探索腳本：記錄切換到 MI01 時所有 HIS API 回應的 endpoint 和資料結構。
執行：python discover_apis.py
"""
import asyncio
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

HIS_URL = "https://hapi.csh.org.tw/orders/#/adm_patient_medical_info?su=1778635214255"
TARGET_INTERNAL_ID = "33958572"  # MI01


async def discover():
    captured = {}

    async def handle_response(response):
        url = response.url
        if "csh.org.tw" not in url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = await response.json()
            key = url.split("?")[0].split("/")[-1]
            size = len(str(data))
            if isinstance(data, dict):
                top_keys = list(data.keys())[:8]
                sample = {k: str(data[k])[:80] for k in list(data.keys())[:3]}
            elif isinstance(data, list):
                top_keys = f"list[{len(data)}]"
                sample = str(data[0])[:120] if data else "(empty)"
            else:
                top_keys = type(data).__name__
                sample = str(data)[:80]

            captured[key] = {
                "url": url,
                "size": size,
                "top_keys": top_keys,
                "sample": sample,
            }
            print(f"[API] {key:45s}  size={size:7d}", file=sys.stderr)
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        page.on("response", handle_response)

        print("[1] 載入頁面...", file=sys.stderr)
        await page.goto(HIS_URL)
        await page.wait_for_timeout(3000)

        print("[2] 更換名單 → MI → 確定名單...", file=sys.stderr)
        change_select = page.locator("select:has(option[value='changePatientList'])").first
        await change_select.select_option("changePatientList", force=True)
        await change_select.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
        await page.wait_for_timeout(1500)

        await page.locator("input[type='radio']").first.click(force=True)
        await page.wait_for_timeout(500)

        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            sel = selects.nth(i)
            inner = await sel.inner_html()
            if ">MI<" in inner or 'value="MI"' in inner:
                await sel.select_option("MI", force=True)
                await sel.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
                print("[2] 已選 MI", file=sys.stderr)
                break
        await page.wait_for_timeout(500)

        try:
            await page.locator("button:has-text('確定名單')").first.click(force=True)
            print("[2] 已點確定名單", file=sys.stderr)
        except Exception as e:
            print(f"[2] 確定名單失敗: {e}", file=sys.stderr)
        await page.wait_for_timeout(2000)

        print(f"[3] 切換至 MI01 (id={TARGET_INTERNAL_ID})...", file=sys.stderr)
        try:
            target_select = page.locator(f"select:has(option[value='{TARGET_INTERNAL_ID}'])").first
            await target_select.select_option(TARGET_INTERNAL_ID, force=True)
            await target_select.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
        except Exception as e:
            print(f"[3] 切換失敗: {e}", file=sys.stderr)

        print("[4] 等待 12 秒讓所有 API 載入...", file=sys.stderr)
        await page.wait_for_timeout(12000)
        await browser.close()

    print(json.dumps(captured, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(discover())
