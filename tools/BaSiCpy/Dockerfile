FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Create install dependencies
RUN pip install -r requirements.txt

# Run the python script when the container launches
CMD ["python", "main.py"]