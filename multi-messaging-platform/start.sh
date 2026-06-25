#!/bin/bash
echo "در حال راه‌اندازی Sender Platform..."
docker compose up -d
echo "همه سرویس‌ها راه‌اندازی شدند:"
docker compose ps
