#!/bin/bash
echo "وضعیت Sender Platform"
echo "=========================="
docker compose ps
echo ""
echo "وضعیت Redis:"
docker compose exec redis redis-cli INFO replication | grep role
echo ""
echo "وضعیت Evolution API:"
curl -s http://localhost:8080/instance/fetchInstances \
  -H "apikey: $(grep EVOLUTION_API_KEY .env | cut -d= -f2)" \
  | python3 -c "import sys,json; data=json.load(sys.stdin); [print(f'  {i[\"name\"]} : {i[\"connectionStatus\"]}') for i in data]"
