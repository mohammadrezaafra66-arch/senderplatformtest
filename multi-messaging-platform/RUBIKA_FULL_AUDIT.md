مأموریت تو این است که کل مسیر Rubika در پروژه Sender Platform را End-to-End، عمیق، شواهد‌محور و Read-Only بررسی کنی و یک گزارش کامل بدهی.

محدوده قطعی

فقط Rubika را بررسی کن.

مسیر پروژه:

F:\senderplatformtest\multi-messaging-platform

اگر فرانت جداست:

F:\senderplatformtest\frontend

هیچ بررسی‌ای روی این موارد انجام نده مگر فقط جایی که مستقیماً dependency مسیر Rubika هستند:

WhatsApp
Evolution API
Telegram
Bale
Soroush
سایر platformها

هیچ فایل unrelated را تغییر نده.

قانون اصلی

ابتدا فقط Audit کن. هیچ تغییری ایجاد نکن.

ممنوع:

ارسال واقعی پیام
Retry واقعی
Queue mutation
DB mutation
اجرای campaign
Resume campaign
تغییر status
حذف Queue
پاک‌کردن Redis
Migration
Restart سرویس‌ها
تغییر .env
تغییر کد

فقط Read-Only بررسی کن.

1. معماری کامل Rubika را استخراج کن

تمام اجزای مرتبط با Rubika را پیدا کن و برای هرکدام مسیر فایل دقیق بده:

API Routeها
Serviceها
Domain logic
Repository / DB access
Models
Schemas
Workerها
Pool Worker
Multi Account Worker
Queue consumer
Retry system
delayed retry
inflight handling
campaign dispatch
preflight
quota
rate limit
lifecycle
readiness
sender account discovery
session loading
Rubika transport/client
recipient validation
account binding
campaign assignment
scheduler
schedules/time windows
workdays
Redis keys
DB tables
Celery tasks
background jobs
config/env
frontend pages/components/hooks/services
campaign UI
account UI
schedule UI
preflight UI
logs/observability
tests

برای هر فایل فقط وجودش را گزارش نکن؛ مشخص کن آیا واقعاً در Runtime استفاده می‌شود یا dead/legacy است.

2. Runtime Path واقعی را پیدا کن

باید مسیر واقعی یک پیام Rubika را از UI تا Delivery ردیابی کنی.

دقیقاً Trace کن:

Frontend
→
API
→
Campaign creation
→
Recipient
→
Sender account selection
→
Preflight
→
Lifecycle policy
→
Schedule check
→
Quota reservation
→
Redis queue
→
Rubika worker
→
Session
→
Rubika transport
→
Delivery result
→
DB status update
→
Frontend status

برای هر مرحله:

فایل
تابع
class
input
output
status transition
failure path
retry behavior

را بنویس.

3. تمام Status Transitionها را بررسی کن

تمام وضعیت‌های مرتبط با Rubika را استخراج کن.

مخصوصاً:

PENDING
QUEUED
SENDING
DELIVERED
FAILED_RETRYABLE
FAILED_PERMANENT
PAUSED
CANCELLED
یا هر status دیگری

یک State Machine واقعی بساز.

مشخص کن:

چه کسی status را تغییر می‌دهد
چه تابعی
چه شرطی
آیا transition غیرقانونی ممکن است
آیا status ممکن است بدون Queue job باقی بماند
آیا Queue job ممکن است بدون DB state باقی بماند

به‌خصوص این سناریو را Audit کن:

FAILED_RETRYABLE ثبت شود ولی هیچ delayed retry یا queue item باقی نماند.

4. Retry System را عمیق بررسی کن

کامل بررسی کن:

FAILED_RETRYABLE
delayed retry
retry scheduler
ZSET
retry_at
flush_due_delayed_retries
worker drain
redis key naming
duplicate retry
lost retry
stuck retry
retry after restart
retry after Redis restart
retry after worker crash
retry after API restart

مشخص کن:

Retry کجا تولید می‌شود؟
چه کسی آن را در Redis می‌گذارد؟
چه کسی آن را دوباره به Queue منتقل می‌کند؟
اگر Worker خاموش باشد چه می‌شود؟
اگر Redis restart شود چه می‌شود؟
اگر DB status retryable باشد ولی Redis خالی باشد چه می‌شود؟
آیا سیستم self-healing دارد؟
5. Rubika Worker را کامل Audit کن

این‌ها را دقیق بررسی کن:

startup
Redis connect
Postgres connect
dynamic account discovery
assigned accounts
polling
queue read
one-message-at-a-time behavior
concurrency
crash recovery
reconnect
delayed retry flushing
graceful shutdown
duplicate processing
idempotency
message acknowledgement
exception handling

مشخص کن آیا Worker می‌تواند:

پیام را دو بار بفرستد
پیام را گم کند
Queue را خالی کند ولی DB را update نکند
DB را update کند ولی پیام واقعاً ارسال نشده باشد
6. Account Discovery / Pool را Audit کن

کامل بررسی کن:

Dynamic discovery
Eligibility
Archived accounts
Active accounts
Readiness
Session validity
Lifecycle
Daily/hourly capacity
Assigned account
Pool refresh
Account health

باید مشخص کنی آیا archived/ineligible account به هر شکلی ممکن است وارد ارسال شود.

7. Rubika Session Path

دقیقاً مشخص کن:

Session از کجا load می‌شود
Canonical session loader چیست
Legacy loader چیست
چه حساب‌هایی legacy path دارند
fallback وجود دارد یا نه
session corruption چه اثری دارد

Warningهایی مثل:

rubika_legacy_session_loader

را بررسی کن و مشخص کن:

bug است؟
technical debt است؟
خطر runtime دارد؟
migration لازم دارد؟
8. Preflight را Audit کن

تمام Preflight checkهای Rubika را استخراج کن:

user registration
session
account readiness
lifecycle
min interval
hourly quota
daily quota
schedule
workday
campaign status
sender status

برای هر check بگو:

retryable یا permanent
error code
retry_after
next_allowed_at
frontend representation
9. Rate Limit / Lifecycle

تمام محدودیت‌های واقعی Runtime را استخراج کن.

مخصوصاً:

NEW
OBSERVATION
LIMITED
RAMPING
NORMAL

و:

daily cap
hourly cap
minimum interval
jitter

بررسی کن precedence بین:

env
lifecycle policy
database
sender schedule

چیست.

هر Conflict احتمالی را گزارش کن.

10. Schedule / Time Window

بررسی کامل:

timezone
Tehran time
day-of-week mapping
Thursday
Friday
multiple slots
active/inactive slots
frontend/backend consistency
boundary conditions

Edge caseها:

دقیقاً ساعت 10:00
14:00
16:00
20:00
midnight
timezone drift
Docker UTC
11. Redis Architecture

فقط Redis keys مرتبط با Rubika را پیدا کن.

برای هر key:

نام
type
producer
consumer
TTL
cleanup
recovery behavior

مخصوصاً:

queue:rubika:*
delayed retry
inflight
pause
quota
locks
dedupe

مشخص کن چه stateهایی volatile هستند و چه stateهایی persistent.

12. DB Audit

تمام table/modelهای مرتبط Rubika را پیدا کن.

روابط را رسم کن:

Campaign
→
CampaignRecipient
→
Message
→
SenderAccount
→
Session
→
Schedule

یا ساختار واقعی پروژه.

بررسی کن:

foreign keys
nullable columns
unique constraints
indexes
duplicate recipient
duplicate message
stale statuses
orphan records
13. Frontend Rubika Audit

فقط UI مربوط به Rubika را کامل بررسی کن.

شامل:

campaign creation
campaign list
campaign detail
recipient status
failure display
retryable/permanent distinction
sender account selection
account management
lifecycle
schedules
preflight
capacity
progress percentage
pause/resume

بررسی کن آیا UI ممکن است:

retryable را failed نهایی نشان دهد
progress اشتباه نشان دهد
backend state قدیمی cache شود
permanent/retryable را قاطی کند
14. API Contract Audit

تمام APIهای Rubika را لیست کن.

برای هر endpoint:

method
path
auth
request
response
consumer frontend
error handling

mismatch بین frontend/backend را گزارش کن.

15. Failure Scenario Matrix

یک Matrix کامل بساز برای:

Redis down
Postgres down
worker down
API down
network failure
Rubika unavailable
invalid session
recipient not registered
min interval
hourly quota
daily quota
schedule closed
campaign paused
duplicate job
process crash mid-send

برای هرکدام بگو:

DB state
Redis state
retry
user-visible state
self recovery
risk
16. Restart / Recovery Audit

بررسی کن بعد از:

Docker restart
Redis restart
Postgres restart
Core API restart
Rubika worker restart

آیا سیستم بدون دخالت دستی Recovery می‌کند یا نه.

17. Concurrency / Race Conditions

دنبال این مشکلات بگرد:

duplicate consumption
race in quota
race in status update
double delivery
stale lock
concurrent retry
account assignment race
worker restart race
pause/resume race
18. Security Review

فقط امنیت Rubika path:

session storage
secrets
logs
API auth
frontend exposure
Redis auth
internal service trust

Secret valueها را هرگز در گزارش چاپ نکن.

فقط نام متغیر یا محل وجود secret را بگو.

19. Observability

بررسی کن:

logs
structured logs
campaign_id
message_id
account_id
error_code
retry metadata
queue metrics
worker health

بگو برای Incident debugging چه چیزهایی کم داریم.

20. Test Coverage

تمام تست‌های مرتبط Rubika را پیدا کن.

Matrix بده:

| Area | Test Exists | Quality | Missing Cases |

مخصوصاً تست برای:

retry
restart
Redis failure
lost delayed retry
pause/resume
quota
schedule
lifecycle
duplicate
permanent error
21. Dead / Legacy Code

هر چیزی که:

legacy
unused
duplicate
shadow path
parallel implementation

است مشخص کن.

هدف این است که بفهمیم برای Rubika چند Runtime Path موازی داریم.

22. Canonical Path

در پایان دقیقاً یک مسیر Canonical برای Rubika پیشنهاد بده.

به شکل:

Frontend → API → Campaign → Dispatcher → Preflight → Queue → Worker → Rubika Transport → Result → DB → UI

و برای هر مسیر دیگر یکی از این statusها بده:

KEEP
MERGE
DEPRECATE
REMOVE
LEGACY ONLY
23. Severity Classification

هر مشکل را یکی از این‌ها کن:

P0 Critical
P1 High
P2 Medium
P3 Low

برای هر Issue:

Issue ID
Severity
Evidence
File
Function
Runtime impact
Reproduction scenario
Recommended fix
Risk of fix
24. خروجی نهایی اجباری

گزارش نهایی باید دقیقاً این بخش‌ها را داشته باشد:

Executive Summary
Rubika Architecture Map
Runtime Message Flow
Backend Audit
Worker Audit
Redis Audit
Database Audit
Frontend Audit
API Contract Audit
Session Audit
Preflight Audit
Retry Audit
Rate Limit & Lifecycle Audit
Schedule Audit
Failure Matrix
Restart & Recovery Audit
Concurrency/Race Audit
Security Audit
Observability Audit
Test Coverage
Legacy/Duplicate Paths
Canonical Rubika Path
Complete Issue Register
Recommended Remediation Plan
Final Production Readiness Verdict
25. Production Readiness Verdict

در انتها یکی را انتخاب کن:

READY
READY WITH MINOR FIXES
NOT READY
CRITICAL BLOCKERS

و دقیقاً بگو چرا.

26. مهم: شواهد

هیچ نتیجه‌ای بدون Evidence نده.

هر ادعا باید حداقل یکی از این‌ها را داشته باشد:

file path
function name
class name
route
DB model
Redis key
test

اگر چیزی را نتوانستی اثبات کنی بنویس:

NOT PROVEN

حدس نزن.

27. مهم: فعلاً هیچ اصلاحی انجام نده

تا پایان Audit:

هیچ فایل، DB، Redis، Container یا Runtime State را تغییر نده.

در انتهای گزارش فقط پیشنهاد اصلاح بده.

بعد از ارائه گزارش منتظر تأیید من بمان.

Context مهم از Incident اخیر

حتماً این موارد را هم در Audit لحاظ کن:

Campaign 238
30 recipient
2 delivered
2 permanent failure
12 مورد قبلاً FAILED_RETRYABLE به دلیل min interval
14 pending
آن 12 retryable در DB بودند ولی هیچ delayed retry در Redis نداشتند
delayed retry key واقعی:
rubika:retry:delayed
Queueهای queue:rubika:* خالی بودند
Rubika worker قبلاً delayed retry drain در loop نداشت و برای آن patch اضافه شده
Redis hostname alias redis یک بار از Docker network حذف شده بود و Worker نمی‌توانست resolve کند
Warning مربوط به:
rubika_legacy_session_loader
Redis authentication فعال است
Campaign 238 فعلاً paused
وضعیت فعلی campaign 238:
DELIVERED = 2
FAILED_PERMANENT = 2
PENDING = 26

بررسی کن آیا این Incident فقط symptom بوده یا مشکلات معماری عمیق‌تری در Rubika path وجود دارد.

اصل نهایی

من یک Audit سطحی نمی‌خواهم.

می‌خواهم Rubika path را مثل یک سیستم Production واقعی بررسی کنی:

Code + Runtime + State + Queue + DB + Frontend + Recovery + Failure Modes + Tests

و در پایان دقیقاً بگویی:

«مسیر Rubika کجا سالم است، کجا ناقص است، کجا خطرناک است، و قبل از Production چه چیزهایی باید اصلاح شوند.»