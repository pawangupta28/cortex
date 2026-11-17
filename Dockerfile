FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Build data + indexes + eval report at image build time, so the container starts ready.
RUN python -m cortex.cli init --synthetic && python -m cortex.cli eval
RUN useradd -m cortex && chown -R cortex /app
USER cortex
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["python", "-m", "cortex.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
