FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/local/cuda/bin:$PATH \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH \
    CUDA_HOME=/usr/local/cuda

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tzdata \
        python3.11 python3.11-venv python3.11-distutils python3-pip \
        curl ca-certificates \
        libglib2.0-0 libgl1-mesa-glx libsm6 libxrender1 libxext6 \
        libjpeg-turbo8 libpng16-16; \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime; \
    echo $TZ > /etc/timezone; \
    dpkg-reconfigure -f noninteractive tzdata; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python3.11 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
 && python -m pip install --no-cache-dir --upgrade pip setuptools wheel packaging

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app

ENV UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8319

EXPOSE 8319

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${UVICORN_PORT}/api/healthz" || exit 1

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", "--port", "8319", \
     "--workers", "1", "--no-access-log", "--timeout-keep-alive", "5"]