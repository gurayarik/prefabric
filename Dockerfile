# Dockerfile
FROM python:3.9-slim-buster

# Bağımlılıkları yüklemek için çalışma dizinini ayarla
WORKDIR /app

# Bağımlılık dosyasını kopyala ve Python bağımlılıklarını kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Geri kalan uygulama dosyalarını kopyala
COPY app.py .
COPY templates templates/
COPY LICENSE .

# Uygulamanın çalışacağı portu belirt (Uvicorn varsayılan olarak 8000 kullanır)
EXPOSE 8000

# Uygulamayı başlatma komutu (uvicorn app:app - app.py dosyasındaki FastAPI uygulamamızın adı app)
# --host 0.0.0.0 dışarıdan erişime izin verir
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]