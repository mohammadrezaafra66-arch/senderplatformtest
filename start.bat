@echo off
echo.
echo ========================================
echo    MMP - Multi Messaging Platform
echo ========================================
echo.

REM پیدا کردن IP داخلی سیستم
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set LOCAL_IP=%%a
    goto :found
)
:found
set LOCAL_IP=%LOCAL_IP: =%
echo IP سیستم شما: %LOCAL_IP%
echo.

REM آپدیت NEXT_PUBLIC_API_URL در docker-compose
powershell -Command "(Get-Content docker-compose.yml) -replace 'REPLACE_WITH_SERVER_IP', 'http://%LOCAL_IP%:8900' | Set-Content docker-compose.yml"

echo در حال اجرای سرویس‌ها...
docker-compose down --remove-orphans
docker-compose build --no-cache
docker-compose up -d migrate
timeout /t 10 /nobreak
docker-compose up -d

echo.
echo ========================================
echo  پروژه اجرا شد!
echo  آدرس پنل: http://%LOCAL_IP%:8900
echo  بقیه سیستم‌ها همین آدرس رو وارد کنن
echo ========================================
pause
