# Frontend message log UX

«لاگ پیام‌ها» column order: #, شماره, نام, **متن پیام**, render, send, فرستنده, به‌روزرسانی.

Text cell: 3-line clamp, `white-space: pre-wrap`, plain text (no `dangerouslySetInnerHTML`). «بیشتر» opens a wide modal with full text, copy, sender, GPT/product metadata, hash.

Campaign detail shows settings (`use_gpt`, `include_products`, render batch) and committed samples labeled «پیام نهایی ثبت‌شده».

Create page: sample preview labeled «پیش‌نمایش نمونه» via `/campaigns/render-preview`. GPT suggestion remains separately labeled non-final.
