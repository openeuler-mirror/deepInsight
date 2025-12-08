FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev \
    libcairo2-dev libpango1.0-dev libjpeg-dev libpng-dev \
    libpq-dev default-libmysqlclient-dev libxml2 libxslt1.1 \
    fonts-noto-cjk fonts-noto-color-emoji fonts-wqy-microhei fonts-wqy-zenhei fontconfig \
    curl git ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /deepinsight

COPY pyproject.toml poetry.lock ./

RUN uv pip install --system -r pyproject.toml --extra-index-url ${PIP_INDEX_URL}

COPY . .

RUN pip install --no-deps .

ENV PYTHONPATH="/deepinsight:${PYTHONPATH}"

RUN chmod +x /deepinsight/entrypoint.sh
ENTRYPOINT ["/deepinsight/entrypoint.sh"]

CMD ["deepinsight", "api", "start"]