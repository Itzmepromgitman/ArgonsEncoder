# Base Image
FROM fedora:43

# 1. Setup home directory, non interactive shell and timezone
RUN mkdir -p /bot && chmod 755 /bot
WORKDIR /bot

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Africa/Lagos
ENV TERM=xterm
ENV PYTHONUNBUFFERED=1

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

# 3. Install a checksum-verified FFmpeg build
ARG FFMPEG_VERSION=9.0
ARG FFMPEG_SHA256_X86_64=c9b3911f578bdaef056813e903042819b4cfc493e9eb4eba6671b70be38e682a
ARG FFMPEG_SHA256_ARM64=bcf6e731d77ee45662a1042d1ca0cea134412f96e395ae8328b3276fc2d82dda
RUN arch=$(arch | sed 's/aarch64/arm64/' | sed 's/x86_64/64/') && \
    case "$arch" in \
      64) expected="$FFMPEG_SHA256_X86_64" ;; \
      arm64) expected="$FFMPEG_SHA256_ARM64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac && \
    file="ffmpeg-n${FFMPEG_VERSION}-latest-linux${arch}-gpl-${FFMPEG_VERSION}.tar.xz" && \
    wget -q "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$file" && \
    echo "$expected  $file" | sha256sum -c - && \
    tar -xJf "$file" && \
    cp -a */bin/* /usr/bin/ && \
    rm -rf "$file"

# 4. Copy files and install python requirements as non-root
COPY --chown=1000:1000 . .
RUN useradd -u 1000 -m botuser && \
    mkdir -p /bot/downloads /bot/thumbs /bot/watermarks && \
    chown -R botuser:botuser /bot
USER botuser

RUN pip3 install --user --no-cache-dir -r requirements.txt

# 5. Start bot (single supervised foreground process; health endpoint on $PORT)
CMD ["bash", "start.sh"]

# Health check against the aiohttp keep-alive endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8030')+'/', timeout=3)" || exit 1
