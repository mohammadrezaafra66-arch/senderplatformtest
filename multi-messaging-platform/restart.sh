#!/bin/bash
echo "در حال ریستارت Sender Platform..."
docker compose restart
echo "همه سرویس‌ها ریستارت شدند:"
docker compose ps
