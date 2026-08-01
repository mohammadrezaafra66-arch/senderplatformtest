#!/bin/bash
echo "🚀 Starting MMP..."

# Docker containers
docker rm -f mmp-postgres mmp-redis 2>/dev/null
docker run -d --name mmp-postgres \
  -e POSTGRES_USER=mmp_user \
  -e POSTGRES_PASSWORD=mmp_pass \
  -e POSTGRES_DB=mmp_db \
  -p 5432:5432 postgres:15-alpine

docker run -d --name mmp-redis \
  -p 6379:6379 redis:7-alpine

echo "⏳ Waiting for DB..."
sleep 5

# Migrations
cd /workspaces/senderplatformtest/multi-messaging-platform
alembic upgrade head

echo "✅ Ready! Now run these in separate terminals:"
echo "  T1: cd multi-messaging-platform && uvicorn core_engine.main:app --host 0.0.0.0 --port 8001 --reload"
echo "  T2: cd multi-messaging-platform && celery -A workers.tasks.celery_app worker --loglevel=info"
echo "  T3: cd multi-messaging-platform && celery -A workers.tasks.celery_app beat --loglevel=info"
echo "  T4: cd frontend && npm run dev"
