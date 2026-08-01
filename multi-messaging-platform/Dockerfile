FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Browsers come from the Playwright base image (/ms-playwright); Python package must be pip-installed.
ENV PIP_DEFAULT_TIMEOUT=600
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Retry pip on flaky networks (common on first Docker build in Iran / slow links).
RUN python -m pip install --upgrade pip setuptools wheel \
    && sed '/^playwright/d' requirements.txt > requirements.docker.txt \
    && for attempt in 1 2 3 4 5 6; do \
         pip install --no-cache-dir --timeout 600 --retries 20 -r requirements.docker.txt \
         && break; \
         echo "requirements pip attempt ${attempt} failed — retry in 20s"; \
         sleep 20; \
       done

RUN for attempt in 1 2 3 4 5 6; do \
         pip install --no-cache-dir --timeout 600 --retries 20 "playwright==1.49.1" \
         && python -c "import playwright; print('playwright ok')" \
         && break; \
         echo "playwright pip attempt ${attempt} failed — retry in 20s"; \
         sleep 20; \
       done

COPY alembic.ini .
COPY alembic ./alembic
COPY storage ./storage
COPY core_engine ./core_engine
COPY workers ./workers
COPY scripts ./scripts
COPY tests ./tests

# Fix aiobale proxy issue with aiohttp 3.9+
RUN python3 -c "\
import importlib.util; \
spec = importlib.util.find_spec('aiobale.client.session.aiohttp'); \
path = spec.origin; \
src = open(path).read(); \
src = src.replace('aiohttp.ClientSession(\n                timeout=session_timeout, proxy=self.proxy\n            )', 'aiohttp.ClientSession(\n                timeout=session_timeout\n            )'); \
src = src.replace('aiohttp.ClientSession(timeout=session_timeout, proxy=self.proxy)', 'aiohttp.ClientSession(timeout=session_timeout)'); \
src = src.replace('aiohttp.ClientSession(proxy=self.proxy)', 'aiohttp.ClientSession()'); \
open(path, 'w').write(src); \
print('patched:', path) \
"
