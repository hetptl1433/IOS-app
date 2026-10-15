FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY sparc_lab ./sparc_lab
RUN pip install --no-cache-dir .
ENV SPARC_DATA_DIR=/app/runtime
EXPOSE 8765
CMD ["python", "-m", "sparc_lab", "--host", "0.0.0.0", "--port", "8765"]
