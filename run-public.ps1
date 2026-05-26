# Запуск в локальной сети: ссылка http://ВАШ_IP:8000 (доступ с других ПК в той же Wi‑Fi)
$port = if ($env:PORT) { $env:PORT } else { "8000" }
Write-Host "Сервер: http://0.0.0.0:$port"
Write-Host "На этом ПК: http://127.0.0.1:$port"
try {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } | Select-Object -First 1).IPAddress
    if ($ip) { Write-Host "В локальной сети: http://${ip}:$port" }
} catch { }
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port $port
