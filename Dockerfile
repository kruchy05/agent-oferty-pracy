# Obraz aplikacji: API + strona. Baza jest w osobnym kontenerze (docker-compose.yml).
# 3.13 - ta sama wersja, na której testy chodzą na GitHubie
FROM python:3.13-slim

# bez plików .pyc w obrazie i od razu widoczne printy agenta w `docker compose logs`
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# najpierw same zależności - Docker zapamięta tę warstwę i przy zmianie kodu nie instaluje wszystkiego od nowa
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY szukaj.py .

# aplikacja nie działa jako root - na wypadek, gdyby ktoś znalazł w niej dziurę
RUN useradd --create-home agent && mkdir dane && chown agent dane
USER agent

EXPOSE 8000

# Docker sam sprawdza, czy serwer odpowiada (stan "healthy" w `docker compose ps`)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/info', timeout=4)"

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
