FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin cairn
ENV PORT=8000 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
USER cairn
HEALTHCHECK --interval=2s --timeout=2s --start-period=15s --retries=20 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz')"
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
