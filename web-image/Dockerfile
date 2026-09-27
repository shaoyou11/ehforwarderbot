# Stage 1: Builder stage - Install build dependencies and Python packages
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4 AS builder

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'
ENV PIP_DEFAULT_TIMEOUT=30 PIP_RETRIES=2

COPY constraints.lock /tmp/constraints.lock

# Install build-time dependencies for apk packages and pip packages
RUN set -ex; \
    apk upgrade --no-cache; \
    apk add --no-cache --update \
        python3-dev \
        py3-pillow \
        py3-ruamel.yaml \
        git \
        gcc \
        musl-dev \
        zlib-dev \
        jpeg-dev \
        libffi-dev \
        openssl-dev \
        libwebp-dev;
    # Install python packages using pip with --no-cache-dir
RUN set -e; pip3 install --no-cache-dir --constraint /tmp/constraints.lock urllib3==2.8.0 setuptools==83.0.0; \
    # Install/reinstall rich and Pillow from pip (as per original Dockerfile intent)
    # Note: Pillow might be installed via apk (py3-pillow) and pip, pip version will likely take precedence.
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock rich Pillow; \
    # Install TgCrypto, ignoring any pre-installed PyYAML
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock --ignore-installed PyYAML TgCrypto;

    # Install other Python dependencies from git and PyPI
RUN set -e; pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/shaoyou11/ehforwarderbot-core.git@abf737397cdea2dde991b0cb547877157a031cf7 python-telegram-bot pyqrcode; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/jiz4oh/efb-mp-instantview-middleware.git@abed7e68cc89e4e04dd6b6a39c6088e80dad94ac; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/jiz4oh/efb-map-middleware.git@51f360e95bd38db4bd65485f1bdb5a388e6f5be9; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/jiz4oh/efb-keyword-replace.git@ede3f2ede8092017d7005f9b2150d6325076c852; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/QQ-War/efb-keyword-reply.git@c7dfef513e85d6647ad78c70b4e3353ab8804977; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/QQ-War/efb_message_merge.git@946837e5508bf9325060f15f2a725525baf368ff;

# Keep stable dependencies reusable when a channel changes.
RUN pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/shaoyou11/efb-telegram-master.git@9816e65fa50c93fd5272260aff045a23e4386ed7
RUN pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/Ovler-Young/efb-wechat-slave.git@7eeec172bf347ad7eef1c2222fe2048aefc4b59d
RUN pip3 install --no-cache-dir --constraint /tmp/constraints.lock lottie==0.7.2 cairosvg==2.9.1
COPY web-image/patch.py /tmp/patch-web.py
RUN python /tmp/patch-web.py && pip3 check
RUN pip3 freeze --all > /tmp/web-packages.txt

# Stage 2: Final stage - Install only runtime dependencies and copy artifacts
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4

ARG EFB_IMAGE_BUILD_TIME=unknown
ARG EFB_IMAGE_SOURCE_REF=unknown

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'
ENV EFB_DATA_PATH /data/
ENV EFB_PARAMS ""
ENV EFB_PROFILE "web"
ENV EFB_IMAGE_REVISION "web-7eeec17-tg9816e65-isolated-v1"
ENV EFB_CORE_REVISION "${EFB_IMAGE_SOURCE_REF}"
ENV EFB_TELEGRAM_MASTER_REVISION "9816e65fa50c93fd5272260aff045a23e4386ed7"
ENV EFB_IMAGE_BUILD_TIME "${EFB_IMAGE_BUILD_TIME}"
ENV EFB_IMAGE_SOURCE_REF "${EFB_IMAGE_SOURCE_REF}"
ENV HTTPS_PROXY ""

LABEL org.opencontainers.image.created="${EFB_IMAGE_BUILD_TIME}" \
      org.opencontainers.image.revision="${EFB_IMAGE_SOURCE_REF}"

COPY constraints.lock /tmp/constraints.lock

# Set timezone
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone;

# Install runtime C-library dependencies including cron and necessary libs for python packages
RUN set -ex; \
    apk upgrade --no-cache; \
    apk add --no-cache --update \
        libmagic \
        cairo \
        ffmpeg \
        zlib \
        jpeg \
        libffi \
        py3-pillow \
        openssl \
        libwebp \
        cronie \
        py3-ruamel.yaml; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock 'setuptools==83.0.0';

# Copy installed python packages from builder stage's site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
# Copy executables installed by pip packages
COPY --from=builder /usr/local/bin/ehforwarderbot /usr/local/bin/ehforwarderbot

# Fail the image build if the public Lottie GIF renderer cannot load Cairo.
RUN python -c "from lottie.exporters.gif import export_gif; from lottie.exporters.cairo import PngRenderer"

# Copy entrypoint script and make it executable
COPY web-image/entrypoint.py /opt/efb-web/entrypoint.py
COPY web-image/test_web.py /opt/efb-web/test_web.py
COPY --from=builder /tmp/web-packages.txt /opt/efb-web/packages.txt
RUN addgroup -g 10001 efbweb && adduser -D -u 10001 -G efbweb efbweb
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
WORKDIR /data
ENV EFB_WEB_ENABLE_LOGIN=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN python /opt/efb-web/test_web.py

ENTRYPOINT ["python", "/opt/efb-web/entrypoint.py"]
