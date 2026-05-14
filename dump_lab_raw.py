import asyncio, json, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Users\YUAN\Desktop\his_crawler")
from clinical_service import _get_his_url, _open_ward_list, CAPTURE_ENDPOINTS
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        captured = {}

        async def handle(resp):
            if "query_cumulative_lab_data" in resp.url:
                try:
                    captured["lab"] = await resp.json()
                except Exception as e:
                    sys.stderr.write(f"json error: {e}\n")

        page.on("response", handle)
        await page.goto(_get_his_url())
        await asyncio.sleep(8)

        lab = captured.get("lab", {})
        data = lab.get("data", lab) if isinstance(lab, dict) else lab
        if isinstance(data, list):
            sys.stderr.write(f"Total records: {len(data)}\n")
            for item in data[:5]:
                if isinstance(item, dict):
                    print(json.dumps(item, ensure_ascii=False))
        else:
            sys.stderr.write(f"Type: {type(data)}\n")
            print(json.dumps(lab, ensure_ascii=False)[:3000])

        await browser.close()

asyncio.run(main())
