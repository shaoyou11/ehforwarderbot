# Stage 1: Builder stage - Install build dependencies and Python packages
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4 AS builder

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'

COPY constraints.lock /tmp/constraints.lock

# Install build-time dependencies for apk packages and pip packages
RUN set -ex; \
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
RUN pip3 install --no-cache-dir --constraint /tmp/constraints.lock urllib3==1.26.15; \
    # Install/reinstall rich and Pillow from pip (as per original Dockerfile intent)
    # Note: Pillow might be installed via apk (py3-pillow) and pip, pip version will likely take precedence.
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock --no-deps --force-reinstall rich Pillow; \
    # Install TgCrypto, ignoring any pre-installed PyYAML
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock --ignore-installed PyYAML TgCrypto;

    # Install other Python dependencies from git and PyPI
RUN pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/shaoyou11/ehforwarderbot-core.git@abf737397cdea2dde991b0cb547877157a031cf7 python-telegram-bot pyqrcode; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/jiz4oh/efb-mp-instantview-middleware.git@abed7e68cc89e4e04dd6b6a39c6088e80dad94ac; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/jiz4oh/efb-map-middleware.git@51f360e95bd38db4bd65485f1bdb5a388e6f5be9; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/jiz4oh/efb-keyword-replace.git@ede3f2ede8092017d7005f9b2150d6325076c852; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/shaoyou11/efb-telegram-master.git@bbe4ce40ebd04b1dab44c2358cbdc953fcac6396; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/shaoyou11/python-comwechatrobot-http.git@68624f576622360f6fdced80f8e270d62f6fb137; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/shaoyou11/efb-wechat-comwechat-slave.git@ab1de11576c6cc3fb64cd2cd84ea2a453471b66f; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/QQ-War/efb-keyword-reply.git@c7dfef513e85d6647ad78c70b4e3353ab8804977; \
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock git+https://github.com/QQ-War/efb_message_merge.git@946837e5508bf9325060f15f2a725525baf368ff;

# Stage 2: Final stage - Install only runtime dependencies and copy artifacts
FROM python:3.11-alpine@sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4

ARG EFB_IMAGE_BUILD_TIME=unknown
ARG EFB_IMAGE_SOURCE_REF=unknown

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'
ENV EFB_DATA_PATH /data/
ENV EFB_PARAMS ""
ENV EFB_PROFILE "default"
ENV EFB_IMAGE_REVISION "bbe4ce4-ab1de11-http68624f5-mw-abed7e6-51f360e-bridge-2032f50-watchdog-697275d"
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
    pip3 install --no-cache-dir --constraint /tmp/constraints.lock 'setuptools>=82.0.1';

# Copy installed python packages from builder stage's site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
# Copy executables installed by pip packages
COPY --from=builder /usr/local/bin/ehforwarderbot /usr/local/bin/ehforwarderbot

# Fail the image build if the public Lottie GIF renderer cannot load Cairo.
RUN python -c "from lottie.exporters.gif import export_gif; from lottie.exporters.cairo import PngRenderer"

# APScheduler 3.6 is required by python-telegram-bot 13, but still imports the
# pkg_resources API removed by setuptools 82. Use the standard metadata API.
RUN sed -i \
        -e 's/from pkg_resources import get_distribution, DistributionNotFound/from importlib.metadata import distribution, PackageNotFoundError/' \
        -e 's/get_distribution(/distribution(/' \
        -e 's/except DistributionNotFound:/except PackageNotFoundError:/' \
        -e 's/del get_distribution, DistributionNotFound/del distribution, PackageNotFoundError/' \
        /usr/local/lib/python3.11/site-packages/apscheduler/__init__.py; \
    sed -i \
        's/from pkg_resources import iter_entry_points/from importlib.metadata import entry_points\n\ndef iter_entry_points(group):\n    return entry_points().select(group=group)/' \
        /usr/local/lib/python3.11/site-packages/apscheduler/schedulers/base.py

# Copy entrypoint script and make it executable
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
