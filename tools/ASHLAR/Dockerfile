FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install Java with apt
RUN apt-get update && \
    apt-get install -y openjdk-21-jdk && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /app

# Create install dependencies
RUN pip install -r requirements.txt

# Run the python script when the container launches
CMD ["python", "main.py"]