داشبورد فرانت‌اند Sender Platform (Next.js / Pages Router)

## Getting Started

### نصب

```bash
pnpm install
```

یا اگر ترجیح می‌دهید:

```bash
npm install
```

### اجرا (Development)

```bash
pnpm dev
```

Open [http://localhost:3010](http://localhost:3010) with your browser.

> **مهم:** پورت `3000` روی این سیستم معمولاً توسط Docker کانتینر `afrakala-local-web` (دستیار افراکالا) اشغال است.  
> UI این پروژه **Sender Platform** است — صفحه ورود با فیلد **نام کاربری** (نه ایمیل).  
> اگر `localhost:3000` می‌بینید، اشتباهی وارد پروژه دیگر شده‌اید.

صفحات نمونه:
- `src/pages/index.tsx`
- `src/pages/login.tsx`

## i18n (ترجمه)

- ترجمه‌ها در مسیر `locales/fa/common.json` قرار دارند.
- i18n با `react-i18next` و فایل `src/i18n.ts` راه‌اندازی شده است.
- متن‌ها در صفحات با `t("...")` از فایل ترجمه خوانده می‌شوند.

## RTL و فونت فارسی

- جهت صفحه RTL از طریق `src/pages/_document.tsx` و `globals.css` اعمال شده است.
- برای جلوگیری از وابستگی به دانلود فونت در زمان build، از فونت‌های سیستم (`Tahoma` و مشابه) استفاده شده است. در مرحله‌های بعد می‌توان فونت فارسی را به‌صورت local (داخل `public/fonts/`) اضافه کرد.

## تاریخ و زمان شمسی

- کتابخانه: `moment-jalaali`
- utility: `src/utils/jalali.ts`
- نمونه استفاده در صفحه `src/pages/index.tsx` نمایش داده می‌شود.

## Auth (اتصال به API)

- توکن در `sessionStorage` ذخیره می‌شود (`mmp.access_token`).
- ورود واقعی: `POST /auth/token` و سپس `GET /auth/me` برای نقش کاربر.
- کلاینت HTTP: `src/lib/api.ts` — هدر `Authorization: Bearer …` و هندل 401/403.
- صفحات داشبورد با `RequireAuth` محافظت می‌شوند.

فایل env نمونه:

مقدار پیش‌فرض API در dev: `/backend` (پروکسی Next → `localhost:8001`، بدون CORS)

```bash
cp .env.local.example .env.local
```

بعد از تغییر `.env.local` یا `next.config.ts` حتماً `npm run dev` را یک‌بار **متوقف و دوباره اجرا** کنید.

کاربران تست بک‌اند: `admin/admin123`, `operator/operator123`, `viewer/viewer123`

## Notes

- مرحله ۳ فاز ۷: Auth واقعی + محافظت مسیرها.
- مرحله ۴ فاز ۷: KPI داشبورد از `/dashboard/summary`، صف‌ها، workerها، کمپین‌های اخیر.
- مرحله ۷ فاز ۷: لیست/جزئیات/start/stop کمپین + ساخت از import + لاگ گیرندگان.
- مرحله ۵ فاز ۷: آپلود مخاطبان — preview/commit از `/imports/contacts/*` + لینک به ساخت کمپین.
- مرحله ۶ فاز ۷: UI اکانت‌ها — لیست/ایجاد/ویرایش/تست اتصال (`/accounts`, admin).
- مرحله ۸ فاز ۷: UI گزارش‌ها — لاگ پیام (`/reports/messages`) و Audit (`/reports/audit`).
- مرحله ۹ فاز ۷: Polish — design system (`components/ui`, `globals.css`)، Layout واکنش‌گرا، جداول scrollable.
- **فاز ۷ فرانت‌اند تکمیل شد.** مرحله بعد: فاز ۷-zero بک‌اند (commit + export CSV) یا فاز ۸ (worker واقعی / deploy).

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn-pages-router) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/pages/building-your-application/deploying) for more details.
