# Utilise une image officielle Python
FROM python:3.14.3-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers nécessaires
COPY requirements.txt .
COPY bot.py .
COPY cogs ./cogs
COPY utils ./utils
COPY data ./data

# Installer les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# NB : on ne copie PAS .env dans l'image (cela graverait les secrets dans une
# couche de l'image). Les variables sont injectées au runtime par
# docker-compose via `env_file: .env`.

EXPOSE 8080

# Commande pour lancer le bot
CMD ["python", "bot.py"]

