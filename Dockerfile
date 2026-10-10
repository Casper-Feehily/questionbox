FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEBUG=0
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DEBUG=1 DATA_DIR=/tmp/qb-build python manage.py collectstatic --noinput
RUN groupadd --gid 10001 questionbox && useradd --uid 10001 --gid questionbox --create-home questionbox && mkdir -p /app/data && chown questionbox:questionbox /app/data
USER questionbox
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "30", "--access-logfile", "/dev/null"]
