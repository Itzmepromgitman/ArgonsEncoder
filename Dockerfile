# Base Image
FROM fedora:40

# 1. Setup home directory, non interactive shell and timezone
RUN mkdir -p /bot && chmod 755 /bot
WORKDIR /bot

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Africa/Lagos
ENV TERM=xterm

# 2. Install runtime dependencies (git for update.py; procps for psutil process mgmt)
RUN dnf -qq -y install \
       git \
       bash \
       xz \
       wget \
       curl \
       python3-pip \
       psmisc \
       procps-ng \
    && python3 -m pip install --no-cache-dir --upgrade pip setuptools \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# 3. Install pinned ffmpeg build
RUN arch=$(arch | sed 's/aarch64/arm64/' | sed 's/x86_64/64/') && \
    wget -q "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux${arch}-gpl-7.1.tar.xz" && \
    tar -xJf "ffmpeg-n7.1-latest-linux${arch}-gpl-7.1.tar.xz" && \
    cp -a */bin/* /usr/bin/ && \
    rm -rf ffmpeg-n7.1-latest-linux${arch}-gpl-7.1* || true

# 4. Copy files and install python requirements as non-root
COPY --chown=1000:1000 . .
RUN useradd -u 1000 -m botuser && chown -R botuser:botuser /bot
USER botuser

RUN pip3 install --user --no-cache-dir -r requirements.txt

# 5. Start bot (single supervised foreground process; health endpoint on $PORT)
CMD ["bash", "start.sh"]

# Health check against the aiohttp keep-alive endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8030')+'/', timeout=3)" || exit 1
