# Evals

驗證 his-query skill 實際表現用的測試案例。每個 eval 是一個獨立的 `.json` 檔，格式如下：

```json
{
  "id": "eval 唯一識別碼",
  "description": "測試目的說明",
  "input": "使用者提問（直接貼入對話）",
  "requires_network": true,
  "expected": {
    "must_contain": ["必須出現的關鍵字或數值"],
    "must_not_contain": ["不應出現的錯誤詞"],
    "format_check": "格式描述（自由文字）"
  },
  "notes": "備註（如需特定病房或班別）"
}
```

## 執行方式

回院內網路後，逐一執行（不要同時開多個瀏覽器，避免 session 衝突）：

1. 確認 `his_token.txt` 的 token 有效
2. 將 `input` 貼入對話，觀察 Claude 回覆
3. 對照 `expected` 核查結果
4. 紀錄通過/失敗與備註

## 案例清單

| 檔案 | 測試項目 |
|---|---|
| `eval_01_mi01_vasopressor.json` | MI01 升壓劑現況 |
| `eval_02_mi_ward_list.json` | MI 病房名單 |
| `eval_03_mi01_night_shift.json` | MI01 大夜班發生了什麼事 |
| `eval_04_mi01_abnormal_lab.json` | MI01 異常檢驗值 |
