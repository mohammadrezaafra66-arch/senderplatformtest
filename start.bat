@echo off
echo.
echo ========================================
echo    MMP - Multi Messaging Platform
echo ========================================
echo.

REM پیدا کردن IP داخلی سیستم
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127.0.0.1"') do (
    set LOCAL_IP=%%a
    goto :found
)
:found
set LOCAL_IP=%LOCAL_IP: =%
echo IP سیستم شما: %LOCAL_IP%
echo آدرس پنل: http://%LOCAL_IP%:8900
echo.

REM حذف container های قبلی
docker rm -f mmp-postgres mmp-redis mmp-backend mmp-celery mmp-frontend mmp-migrate 2>nul

REM build با IP داخلی
echo در حال build...
docker-compose build --build-arg NEXT_PUBLIC_API_URL=http://%LOCAL_IP%:8900 --no-cache

REM اجرای DB
echo در حال اجرای دیتابیس...
docker-compose up -d postgres redis
timeout /t 8 /nobreak >nul

REM migration
echo در حال اجرای migration...
docker-compose run --rm migrate

REM اجرای بقیه سرویس‌ها
echo در حال اجرای سرویس‌ها...
docker-compose up -d backend celery frontend

echo.
echo ========================================
echo  پروژه اجرا شد!
echo  آدرس پنل: http://%LOCAL_IP%:8900
echo  بقیه سیستم‌ها همین آدرس رو وارد کنن
echo ========================================
pause
