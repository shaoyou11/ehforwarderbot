# syntax=docker/dockerfile:1.19
# Stage 1: Builder stage - Install build dependencies and Python packages
FROM python:3.11.17-alpine3.24@sha256:d9368b3a5ac59afea7b5d4f2e2aea0941dbf9fdee9c369c5bec00b98244bc929 AS builder

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'
ENV PIP_DEFAULT_TIMEOUT=30 PIP_RETRIES=2

COPY web-image/constraints-runtime.lock /tmp/constraints.lock

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
RUN set -e; pip3 install --no-cache-dir --constraint /tmp/constraints.lock urllib3 setuptools wheel packaging pip; \
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
COPY web-image/compile_zh.py /tmp/compile-zh.py
COPY web-image/patch_media.py /tmp/patch-media.py
COPY web-image/patch_upload.py /tmp/patch-upload.py
COPY web-image/patch_login.py /tmp/patch-login.py
COPY web-image/patch_channels.py /tmp/patch-channels.py
COPY web-image/patch_topic_guard.py /tmp/patch-topic-guard.py
COPY web-image/control/topic_guard.py /usr/local/lib/python3.11/site-packages/efb_telegram_master/topic_guard.py
RUN python /tmp/patch-web.py && python /tmp/patch-media.py /usr/local/lib/python3.11/site-packages && python /tmp/patch-upload.py /usr/local/lib/python3.11/site-packages && python /tmp/patch-login.py /usr/local/lib/python3.11/site-packages && python /tmp/patch-channels.py /usr/local/lib/python3.11/site-packages && python /tmp/compile-zh.py /usr/local/lib/python3.11/site-packages && pip3 check
RUN pip3 freeze --all --exclude pip > /tmp/web-packages.txt
RUN python /tmp/patch-topic-guard.py /usr/local/lib/python3.11/site-packages

# Stage 2: Final stage - Install only runtime dependencies and copy artifacts
FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

# Copy the official Python runtime without installation tools. No production
# files are removed: the final filesystem is assembled from selected artifacts.
COPY --from=builder --exclude=lib/python3.11/site-packages --exclude=lib/python3.11/ensurepip --exclude=bin/pip* /usr/local/ /usr/local/
ENV PATH="/usr/local/bin:${PATH}"

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

COPY web-image/constraints-runtime.lock /tmp/constraints.lock

# Set timezone
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone;

# Install runtime C-library dependencies including cron and necessary libs for python packages
RUN set -ex; \
    apk upgrade --no-cache; \
    apk add --no-cache --update \
        libbz2 libexpat libffi libuuid ncurses-libs readline sqlite-libs xz-libs gdbm libnsl libtirpc \
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
        py3-ruamel.yaml;

# Copy installed python packages from builder stage's site-packages
COPY --from=builder --exclude=pip --exclude=pip-*.dist-info /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
# Copy executables installed by pip packages
COPY --from=builder /usr/local/bin/ehforwarderbot /usr/local/bin/ehforwarderbot

# Fail the image build if the public Lottie GIF renderer cannot load Cairo.
RUN python -c "import bz2, ctypes, curses, dbm.gnu, lzma, readline, sqlite3, ssl; from lottie.exporters.gif import export_gif; from lottie.exporters.cairo import PngRenderer; import importlib.util; assert importlib.util.find_spec('pip') is None; assert importlib.util.find_spec('ensurepip') is None"

# Copy entrypoint script and make it executable
COPY web-image/control/ /opt/efb-backend-control/
COPY web-image/entrypoint.py /opt/efb-web/entrypoint.py
COPY web-image/test_web.py /opt/efb-web/test_web.py
COPY web-image/prepare_profile.py /opt/efb-web/prepare_profile.py
COPY web-image/binding_sync.py /opt/efb-web/binding_sync.py
COPY web-image/test_binding_sync.py /opt/efb-web/test_binding_sync.py
COPY --from=builder /tmp/web-packages.txt /opt/efb-web/packages.txt
RUN python /opt/efb-backend-control/test_topic_guard.py
RUN addgroup -g 10001 efbweb && adduser -D -u 10001 -G efbweb efbweb
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
WORKDIR /data
ENV EFB_WEB_ENABLE_LOGIN=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANGUAGE=zh_CN
RUN python /opt/efb-backend-control/test_dependency_compat.py && python /opt/efb-web/test_web.py && python /opt/efb-web/test_binding_sync.py && python /opt/efb-backend-control/test_control.py && python /opt/efb-backend-control/test_frontend.py && python /opt/efb-backend-control/test_upload.py && python /opt/efb-backend-control/test_qr_relay.py && python /opt/efb-backend-control/test_login_guard.py && python /opt/efb-backend-control/test_health.py && python /opt/efb-backend-control/test_channels_video.py

ENTRYPOINT ["python", "/opt/efb-web/entrypoint.py"]
