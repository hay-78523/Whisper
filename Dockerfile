# Dockerfile cho Hugging Face Spaces (Docker Space) hoac bat ky may chu Linux nao.
# Space free: 2 vCPU / 16GB RAM — nen dat WHISPER_DEFAULT_MODEL=small cho nhanh,
# hoac de mac dinh large-v3-turbo neu chap nhan cham hon doi lay do chinh xac.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Spaces chay bang user thuong (uid 1000) — tro cache model va users.json ve /tmp
ENV HF_HOME=/tmp/hf
RUN mkdir -p /tmp/hf && chmod 777 /tmp/hf /app

EXPOSE 7860

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "7860"]
