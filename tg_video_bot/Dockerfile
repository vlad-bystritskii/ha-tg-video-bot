# The aiogram image already ships a compiled telegram-bot-api server binary
# (Alpine / musl) for amd64 and aarch64. We build on top of it and add
# Python + ffmpeg + yt-dlp so a single container can run BOTH the local
# Bot API server and the bot itself (Home Assistant add-ons are one container).
FROM aiogram/telegram-bot-api:latest

# nodejs is the external JavaScript runtime yt-dlp needs to solve YouTube's JS
# challenges (required since yt-dlp 2025.11.12 — without it YouTube returns
# "Requested format is not available"). The base is alpine:latest, so apk gives a
# musl-native Node >=22 with a matching ABI (no glibc shim needed). yt-dlp is told
# to use it via `--js-runtimes node`.
RUN apk add --no-cache \
    python3 \
    py3-pip \
    ffmpeg \
    jq \
    bash \
    curl \
    tzdata \
    nodejs

COPY requirements.txt /
RUN pip3 install --no-cache-dir --break-system-packages -r /requirements.txt

COPY run.sh /run.sh
COPY bot.py /bot.py
RUN chmod +x /run.sh

# The base image sets its own ENTRYPOINT that launches telegram-bot-api directly;
# clear it so our run.sh (which launches both processes) is what actually runs.
ENTRYPOINT []
CMD ["/run.sh"]
