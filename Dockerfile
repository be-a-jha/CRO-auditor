# Container for Hugging Face Spaces (Docker SDK) or any container host. The app listens on $PORT (7860 on Spaces)
# in public mode; set ANTHROPIC_API_KEY and APP_PASSWORD as secrets on the host, never in the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# Spaces run the container as user 1000; the app writes runs and caches next to itself.
RUN useradd -m -u 1000 user && chown user:user /app
COPY --chown=user . /app
USER user
ENV HOME=/home/user HOST=0.0.0.0 PORT=7860
EXPOSE 7860
CMD ["python", "app.py", "--no-browser"]
