FROM python:3.11-slim

WORKDIR /app

# ASHLAR reads the OME-TIFF cycle files through Bio-Formats, which needs a JVM.
RUN apt-get update && \
    apt-get install -y openjdk-21-jdk && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install -r requirements.txt

CMD ["python", "main.py"]
