Write-Host "Starting Financial Report Copilot..."

# Kill whatever is on port 8000
$entry = netstat -ano | Select-String ":8000\s.*LISTENING"
if ($entry) {
    $pid_ = ($entry -split "\s+")[-1]
    taskkill /PID $pid_ /F 2>$null
    Start-Sleep 1
}

# API
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; .venv\Scripts\uvicorn copilot.api:app --port 8000"

Start-Sleep 2

# Frontend
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; .venv\Scripts\streamlit run frontend.py --server.port 8501 --server.headless true --browser.gatherUsageStats false"

Start-Sleep 2

Start-Process "http://localhost:8501"
Write-Host "Done. Frontend: http://localhost:8501"
