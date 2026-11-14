FROM docker.io/library/python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
COPY server.py /app/server.py
USER 65534:65534
ENTRYPOINT ["python3", "-I", "-B", "/app/server.py"]
