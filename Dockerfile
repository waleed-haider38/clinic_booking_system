# Start from an empty system by installing the below package
FROM python:3.13-slim

# Telling the work directory in which directory these commands will run
WORKDIR /app

# Copy the requirements.txt before copying the whole code
COPY requirements.txt .

# Running the command to install all the packages required in requirements.txt file
# This command will only run once, at build time.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole folder code now
COPY . .

# Telling the port on which port it will run
EXPOSE 8000

# This command will run every time a container starts
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]