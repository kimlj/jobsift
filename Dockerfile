# jobsift in a container. The image holds the code and nothing of yours: config,
# secrets, resume and database stay in the folder compose.yaml mounts at /work,
# so rebuilding or deleting the image loses nothing. See docs/deploy.md, Option D.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The code runs from /opt/jobsift and never from the mounted folder. Without
# PYTHONSAFEPATH, `python -m` puts the working directory first on sys.path, and a
# checkout mounted at /work would run its own jobsift/ instead of the image's -
# whatever version happens to be on disk, with whatever dependencies it wants.
ENV PYTHONSAFEPATH=1 \
    PYTHONPATH=/opt/jobsift \
    HOME=/tmp \
    JOBSIFT_DOCKER=1

WORKDIR /opt/jobsift
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY jobsift/ jobsift/
# --setup copies these into /work, and they belong to the version that was built.
COPY config.example.yaml .env.example resume.example.txt profile.example.yaml career.example.yaml ./

WORKDIR /work
USER 1000:1000
ENTRYPOINT ["python", "-m", "jobsift"]
