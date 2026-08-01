"""Patch aiobale aiohttp session to remove proxy from ClientSession.__init__"""
import re
import importlib.util

spec = importlib.util.find_spec("aiobale.client.session.aiohttp")
if spec and spec.origin:
    path = spec.origin
    content = open(path).read()
    
    # fix multiline case
    content = content.replace(
        "aiohttp.ClientSession(\n                timeout=session_timeout, proxy=self.proxy\n            )",
        "aiohttp.ClientSession(\n                timeout=session_timeout\n            )"
    )
    content = content.replace(
        "aiohttp.ClientSession(timeout=session_timeout, proxy=self.proxy)",
        "aiohttp.ClientSession(timeout=session_timeout)"
    )
    content = content.replace(
        "aiohttp.ClientSession(proxy=self.proxy)",
        "aiohttp.ClientSession()"
    )
    
    open(path, 'w').write(content)
    print(f"✅ aiobale patched at {path}")
else:
    print("❌ aiobale not found")
