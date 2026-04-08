# 使用官方 Python 基础镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 防止 Python 生成 .pyc 文件
ENV PYTHONDONTWRITEBYTECODE 1
# 实时打印日志到控制台
ENV PYTHONUNBUFFERED 1

# 安装系统依赖 (编译某些 Python 包可能需要)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --create-home app

# 先复制依赖文件并安装 (利用 Docker 缓存层加速构建)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

# 复制整个项目代码
COPY . .

RUN chown -R app:app /app
USER app

# 默认命令 (会被 docker-compose 覆盖，所以这里不写也没关系)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]