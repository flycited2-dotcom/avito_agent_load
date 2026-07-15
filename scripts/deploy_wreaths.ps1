# Деплой профилей на VPS + первая выкладка фида венков. Запуск из корня avito-bridge:
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_wreaths.ps1
# Схема из CLAUDE.md: scp на VPS не работает -> tar + ssh 'cat > file'.
# Кондиционерный конвейер защищён снапшот-тестом (tests/test_feed_snapshot.py);
# скрипт в конце перезапускает avito-bridge.service и показывает его статус.

$ErrorActionPreference = "Stop"
$VPS = "root@213.109.202.45"
$KEY = Join-Path $HOME ".ssh\climat_simf_deploy"
$SSH = "ssh -i `"$KEY`" -o StrictHostKeyChecking=accept-new $VPS"

Write-Host "[1/5] Пакуем src + config + profiles..."
tar -czf deploy_profiles.tgz src config profiles
if ($LASTEXITCODE -ne 0) { throw "tar не собрался" }

Write-Host "[2/5] Заливаем на VPS..."
cmd /c "ssh -i `"$KEY`" -o StrictHostKeyChecking=accept-new $VPS `"cat > /tmp/deploy_profiles.tgz`" < deploy_profiles.tgz"
if ($LASTEXITCODE -ne 0) { throw "заливка не удалась" }
Remove-Item deploy_profiles.tgz

Write-Host "[3/5] Распаковываем и собираем фид венков..."
$remote = "cd /opt/avito-bridge && tar -xzf /tmp/deploy_profiles.tgz && " +
          "PYTHONPATH=src .venv/bin/python -m avito_bridge --config profiles/wreaths.yaml"
cmd /c "ssh -i `"$KEY`" $VPS `"$remote`""
if ($LASTEXITCODE -ne 0) { throw "сборка фида венков на VPS не удалась" }

Write-Host "[4/5] Публикуем фид венков в nginx staticfiles..."
$publish = "cp /opt/avito-bridge/feed_out/wreaths.xml /opt/oasis/staticfiles/avito-feed-wreaths.xml && " +
           "ls -la /opt/oasis/staticfiles/avito-feed-wreaths.xml"
cmd /c "ssh -i `"$KEY`" $VPS `"$publish`""
if ($LASTEXITCODE -ne 0) { throw "копирование фида в staticfiles не удалось" }

Write-Host "[5/5] Перезапускаем кондиционерный сервис (проверка, что новый код жив)..."
cmd /c "ssh -i `"$KEY`" $VPS `"systemctl start avito-bridge.service; systemctl status avito-bridge.service --no-pager -l | head -12`""

Write-Host ""
Write-Host "Готово. URL фида венков для ЛК Автозагрузки аккаунта №2:"
Write-Host "  https://splithome.ru/static/avito-feed-wreaths.xml"
Write-Host "Проверка в браузере: файл должен открываться и содержать <Ad> с венками."
