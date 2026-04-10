【主要介面】一般網頁 + Cloudflare Pages（不使用 AI，僅彙整並顯示相關新聞）

------------------------------------------------------------
雲端運行（每小時自動抓稿 + 使用者開頁／按鈕更新）
------------------------------------------------------------
目標流程：
1. GitHub Actions「Hourly News Update」依 cron（預設每小時整點 UTC）執行 python main.py，
   將新聞寫入 data/ 並 push 回倉庫（不需要 GEMINI_API_KEY）。
2. 同一 push 若變更 data/，會觸發「Deploy Cloudflare Pages」建置 npm run build 並部署 dist/。
3. 使用者開啟網站：頁面每 5 分鐘自動向伺服器重新讀取 data/*.json；切回分頁或視窗取得焦點也會刷新。
4. 使用者按「立即更新」：呼叫 Cloudflare 上的 /api/generate（不需 API Key），
   以目前靜態檔中的稿件即時產生「純新聞清單」（寫入瀏覽器暫存，與伺服器上的清單合併顯示）。

請在 GitHub 設定：CLOUDFLARE_API_TOKEN、CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_PAGES_PROJECT（給部署）。

Cloudflare／Wrangler 注意：必須使用「wrangler pages deploy …」部署 Pages；勿使用「wrangler deploy」（那是 Workers，會出現 Workers-specific command 錯誤）。
若用 Cloudflare 儀表板連 Git：建置指令填「npm run build」、輸出「dist」即可；勿把「wrangler deploy」當建置指令。
GitHub Actions 部署步驟已改為「npx wrangler pages deploy dist --project-name=$CLOUDFLARE_PAGES_PROJECT」並使用 package.json 內的 wrangler 4.x。

------------------------------------------------------------
本機開發
------------------------------------------------------------
1) 安裝 Node 依賴並建置靜態檔（會複製 web/ 與 data/*.json 到 dist/）：
   npm install
   npm run build

2) 本機預覽（含 Workers Functions，不需 API 金鑰）：
   npm run pages:dev

3) 部署到 Cloudflare Pages（本機手動）：
   npm run pages:deploy

4) GitHub Actions 自動部署（push 至 main/master 且變更 web/functions/data 等路徑時）：
   在倉庫 Secrets 新增：CLOUDFLARE_API_TOKEN、CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_PAGES_PROJECT
   其中 CLOUDFLARE_PAGES_PROJECT 要填 Cloudflare Pages 上「實際專案名稱」。

------------------------------------------------------------
Python 後端（RSS 聚合、寫入 data/，與 GitHub Actions 相同）
------------------------------------------------------------
pip install -r requirements.txt
python main.py

環境變數：可選 TELEGRAM_*、LINE_NOTIFY_TOKEN。

------------------------------------------------------------
GitHub Actions 與 Workflow 自動化部署操作說明書
------------------------------------------------------------
建立日期：2026-04-05
適用系統：macOS / Windows
目的：將本地代碼推送至 GitHub 並啟用 GitHub Actions 自動化執行

第一階段：準備 GitHub 存取金鑰 (Token)
由於 GitHub 不支援密碼上傳，必須使用 Personal Access Token (PAT)：
1. 登入 GitHub > Settings > Developer settings。
2. 選擇 Personal access tokens > Tokens (classic)。
3. 點擊 Generate new token (classic)。
4. 設定名稱，並務必勾選：repo (全部)、workflow。
5. 產生後複製 ghp_ 開頭代碼並妥善保存。

第二階段：設定專案 Secrets (API Keys)
1. 進入倉庫 Settings > Security > Secrets and variables > Actions。
2. New repository secret：CLOUDFLARE_API_TOKEN 等。

第三階段：本地 Git 上傳
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<帳號>/<倉庫>.git
git push -u origin master

第四階段：驗證 Workflow
倉庫 Actions 分頁可檢視 Hourly News Update、Deploy Cloudflare Pages 執行狀態。
