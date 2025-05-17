# Use uma imagem base oficial do Python.
# Escolha uma tag específica para builds reproduzíveis, ex: python:3.11-slim-bullseye
FROM python:3.11-slim-buster

# Variáveis de ambiente
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Instalar dependências do sistema:
# - ffmpeg: para processamento de áudio/vídeo (essencial para MP3 e alguns merges de vídeo)
# - git: instaloader pode, em raras situações, precisar dele para alguma funcionalidade interna ou se você instalasse algo do git.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Criar diretório para a aplicação
WORKDIR /app

# Copiar arquivo de dependências
COPY requirements.txt .

# Instalar dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do código da aplicação
COPY . .

# Criar um usuário não-root para rodar a aplicação (melhor para segurança)
RUN useradd -m myuser
USER myuser

# Expor a porta que o Gunicorn vai rodar (o Render define a PORT via env var)
# Gunicorn no startCommand vai usar $PORT, então este EXPOSE é mais informativo
EXPOSE 8080

# Comando para rodar a aplicação (o Render pode sobrescrever isso com o startCommand no render.yaml)
# No entanto, o Render usará o startCommand do render.yaml para deploys Docker.
# O Gunicorn deve escutar em 0.0.0.0 para ser acessível de fora do container.
# A porta será definida pelo Render via variável de ambiente PORT.
# CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:$PORT", "--timeout", "180", "--workers", "2"]
# O Render define PORT, então Gunicorn no startCommand do render.yaml é mais direto.
