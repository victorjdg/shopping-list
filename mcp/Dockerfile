# El contexto de build es la RAÍZ del proyecto (shopping-list/), no este
# directorio: el servidor importa db/ tal cual (db.py, initdb.py, schema.sql),
# así que la imagen necesita copiar ambos. Se construye con:
#   podman build -f mcp/Dockerfile -t shopping-list:latest .
FROM python:3.12-slim

WORKDIR /app

COPY mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY db/ ./db/
COPY mcp/server.py ./mcp/server.py

EXPOSE 8000

CMD ["python", "mcp/server.py"]
