FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 全部 *.py：server 依赖 appconfig/updater/validate/seccheck 等，逐个列文件名容易漏(漏了容器起不来)
COPY *.py ./
COPY web ./web
# config.yaml 与数据库通过卷挂载(见 docs/DEPLOY.md)：
#   -v ./config.yaml:/app/config.yaml:ro  -v ./data:/app/data
# 并把 config 里 db.path 设为 "data/cloud_overview.db"
# 网页改的密码 / 生成的场地 token 存这里：放进数据卷，容器重建不丢
ENV CO_SETTINGS=/app/data/cloud_settings.json
EXPOSE 8900
CMD ["python", "server.py"]
