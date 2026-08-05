# Serwer gry „Pędzący Piotrek”.
#
# Sam serwer nie potrzebuje pygame ani niczego graficznego — logika partii żyje
# w engine/, które nie importuje pygame, więc obraz jest mały i startuje szybko.
#
#   docker build -t piotrek-server .
#   docker run -p 51337:51337 piotrek-server
#
# Platformy hostingowe podają port w zmiennej PORT; serwer ją czyta sam.

FROM python:3.12-slim

WORKDIR /app

# Tylko WebSockety — pygame celowo pominięte.
RUN pip install --no-cache-dir "websockets>=13"

COPY pedzacy_piotrek/ ./pedzacy_piotrek/

ENV PYTHONUNBUFFERED=1
EXPOSE 51337

CMD ["python", "-m", "pedzacy_piotrek.server"]
