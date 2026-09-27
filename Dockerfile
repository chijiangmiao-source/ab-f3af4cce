FROM python:3.11-slim

# 仅依赖 Python 标准库，无需 pip 安装第三方包
WORKDIR /project

# 构建产物：应用源码 + 编排文件（verify 会做构建产物检查）
COPY app/ ./app/
COPY Dockerfile docker-compose.yml ./

# 构建步骤：全量字节码编译，尽早暴露语法问题
RUN python3 -m compileall -q app

WORKDIR /project/app
EXPOSE 8080

# web 与 verify 使用同一镜像，按 service command 区分
CMD ["python3", "server.py"]
