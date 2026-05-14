import asyncio
from playwright.async_api import async_playwright

# 目標：想要查詢的床號 (模擬未來從 LINE 傳來的指令)
TARGET_BED = "MI01"
# 想要攔截的 API 關鍵字 (例如生化檢驗數據)
TARGET_API_KEYWORD = "query_cumulative_lab_data"

async def handle_response(response):
    """監聽器：負責半路攔截 API 的 JSON 回應"""
    if TARGET_API_KEYWORD in response.url and response.status == 200:
        print(f"\n[🎯 成功攔截目標 API] {response.url}")
        try:
            data = await response.json()
            print(f"\n=== 【{TARGET_BED} 床】原始檢驗數據成功取得 ===")
            # 印出部分資料確認 (實務上這裡就是你要轉成 LINE 訊息的來源)
            print(str(data)[:500] + " ... (略)") 
        except Exception as e:
            print(f"JSON 解析失敗: {e}")

async def main():
    async with async_playwright() as p:
        # 啟動瀏覽器 (顯示介面方便觀察)
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # 啟動背景 API 監聽
        page.on("response", handle_response)

        # 1. 進入主系統網址
        print("正在進入系統...")
        await page.goto("https://hapi.csh.org.tw/orders/#/adm_patient_medical_info?su=1778635214255")
        
        # ⚠️ 實務提醒：如果進入此網址會先跳轉到登入頁，程式需在此處先執行輸入帳密登入的動作！
        # await page.fill("input[name='username']", "帳號")
        # await page.fill("input[name='password']", "密碼")
        # await page.click("button[type='submit']")

        # 2. 等待病人選單元件載入完成
        # 注意：避免使用 input_70 這種動態生成的 ID，改用 name 屬性定位更穩健
        select_locator = page.locator("select[name='select_62']")
        await select_locator.wait_for(state="attached")
        print("選單已載入，正在解析床號清單...")

        # 3. 獲取選單內所有的 <option> 元素
        options = await select_locator.locator("option").all()
        
        # 建立映射表字典 mapping = {'MI01': '33958572', 'MI03': '34450442', ...}
        bed_mapping = {}
        for opt in options:
            text = await opt.text_content()
            val = await opt.get_attribute("value")
            # 確保選項有值，且開頭符合床號格式 (如 MI)
            if val and text and text.strip().startswith("MI"):
                # 提取前面第一個單字當床號 (例如 "MI01 黃寶墩" -> "MI01")
                bed_no = text.strip().split(" ")[0]
                bed_mapping[bed_no] = val

        print(f"目前線上的病人清單對應：{bed_mapping}")

        # 4. 執行切換動作
        if TARGET_BED in bed_mapping:
            target_value = bed_mapping[TARGET_BED]
            print(f"準備查詢 {TARGET_BED}，對應內部流水號為 {target_value}，正在觸發選單切換...")
            
            # 讓 Playwright 選擇該指定流水號，這會立即觸發網頁發送背景 API 請求！
            await select_locator.select_option(value=target_value)
            
            # 給予一點緩衝時間讓 API 回應並觸發 handle_response 印出資料
            await page.wait_for_timeout(3000) 
        else:
            print(f"找不到指定的床號：{TARGET_BED}")

        # 測試完畢先暫停，讓你檢視終端機畫面
        await page.pause()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())