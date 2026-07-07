# The aiogram image already ships a compiled telegram-bot-api server binary
# (Alpine / musl) for amd64 and aarch64. We build on top of it and add
# Python + ffmpeg + yt-dlp so a single container can run BOTH the local
# Bot API server and the bot itself (Home Assistant add-ons are one container).
FROM aiogram/telegram-bot-api:latest

RUN apk add --no-cache \
    python3 \
    py3-pip \
    ffmpeg \
    jq \
    bash \
    curl \
    tzdata

COPY requirements.txt /
RUN pip3 install --no-cache-dir --break-system-packages -r /requirements.txt

COPY run.sh /run.sh
COPY bot.py /bot.py
RUN chmod +x /run.sh

# The base image sets its own ENTRYPOINT that launches telegram-bot-api directly;
# clear it so our run.sh (which launches both processes) is what actually runs.
ENTRYPOINT []
CMD ["/run.sh"]
