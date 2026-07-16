# Stage 1: Builder stage - Install build dependencies and Python packages
FROM python:3.11-alpine AS builder

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'

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
RUN pip3 install --no-cache-dir urllib3==1.26.15; \
    # Install/reinstall rich and Pillow from pip (as per original Dockerfile intent)
    # Note: Pillow might be installed via apk (py3-pillow) and pip, pip version will likely take precedence.
    pip3 install --no-cache-dir --no-deps --force-reinstall rich Pillow; \
    # Install TgCrypto, ignoring any pre-installed PyYAML
    pip3 install --no-cache-dir --ignore-installed PyYAML TgCrypto;

    # Install other Python dependencies from git and PyPI
RUN pip3 install --no-cache-dir git+https://github.com/shaoyou11/ehforwarderbot-core.git@abf737397cdea2dde991b0cb547877157a031cf7 python-telegram-bot pyqrcode; \
    pip3 install --no-cache-dir git+https://github.com/jiz4oh/efb-mp-instantview-middleware.git@e7772cc2c5acc5b776f4bc0bc7562ea5b893eab9; \
    pip3 install --no-cache-dir git+https://github.com/jiz4oh/efb-map-middleware.git@72ea47bba6e2c66c61d706b0864edc2c669101d8; \
    pip3 install --no-cache-dir git+https://github.com/jiz4oh/efb-keyword-replace.git@ede3f2ede8092017d7005f9b2150d6325076c852; \
    pip3 install --no-cache-dir git+https://github.com/shaoyou11/efb-telegram-master.git@8fc2b19d3d7955ba9ae3b679accbe833acbe4541; \
    pip3 install --no-cache-dir git+https://github.com/jiz4oh/python-comwechatrobot-http.git@3df22af9a6e77e8032681800af4ffb90a3981b4c; \
    pip3 install --no-cache-dir git+https://github.com/shaoyou11/efb-wechat-comwechat-slave.git@96d97ee01b9a1eb747a5c9416f648679ed9b5582; \
    pip3 install --no-cache-dir git+https://github.com/QQ-War/efb-keyword-reply.git@c7dfef513e85d6647ad78c70b4e3353ab8804977; \
    pip3 install --no-cache-dir git+https://github.com/QQ-War/efb_message_merge.git@946837e5508bf9325060f15f2a725525baf368ff;

# Stage 2: Final stage - Install only runtime dependencies and copy artifacts
FROM python:3.11-alpine

ENV LANG C.UTF-8
ENV TZ 'Asia/Shanghai'
ENV EFB_DATA_PATH /data/
ENV EFB_PARAMS ""
ENV EFB_PROFILE "default"
ENV HTTPS_PROXY ""

# Set timezone
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone;

# Install runtime C-library dependencies including cron and necessary libs for python packages
RUN set -ex; \
    apk add --no-cache --update \
        libmagic \
        ffmpeg \
        zlib \
        jpeg \
        libffi \
        py3-pillow \
        openssl \
        libwebp \
        cronie \
        py3-ruamel.yaml; \
    pip3 install --no-cache-dir 'setuptools>=82.0.1'; \
    # Clean up apk cache
    rm -rf /var/cache/apk/*;

# Copy installed python packages from builder stage's site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
# Copy executables installed by pip packages
COPY --from=builder /usr/local/bin/ehforwarderbot /usr/local/bin/ehforwarderbot

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
