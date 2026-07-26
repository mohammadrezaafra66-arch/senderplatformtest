#!/bin/bash
# patch aiobale aiohttp session — remove proxy from ClientSession.__init__
AIOBALE_SESSION=$(python3 -c "import aiobale.client.session.aiohttp as m; print(m.__file__)")
echo "Patching: $AIOBALE_SESSION"
sed -i 's/aiohttp.ClientSession(timeout=session_timeout, proxy=self.proxy)/aiohttp.ClientSession(timeout=session_timeout)/g' "$AIOBALE_SESSION"
sed -i 's/aiohttp.ClientSession(proxy=self.proxy)/aiohttp.ClientSession()/g' "$AIOBALE_SESSION"
# fix multiline case
python3 -c "
content = open('$AIOBALE_SESSION').read()
content = content.replace('aiohttp.ClientSession(\n                timeout=session_timeout, proxy=self.proxy\n            )', 'aiohttp.ClientSession(\n                timeout=session_timeout\n            )')
open('$AIOBALE_SESSION', 'w').write(content)
"
echo "✅ aiobale patched"
