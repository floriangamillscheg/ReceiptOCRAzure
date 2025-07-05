# Use official lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy app files into container
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables (you can also override them at runtime)
ENV DOCUMENTINTELLIGENCE_ENDPOINT="https://dings.cognitiveservices.azure.com/"
ENV DOCUMENTINTELLIGENCE_API_KEY="30Gl3NVzL6VGbiLNxcNNsG91BvkDFFxpF7fdeHyeAxtB6r7GlwG6JQQJ99AJAC5RqLJXJ3w3AAALACOGK4Lt"

# Run the FastAPI app
CMD ["uvicorn", "ReceiptOCRAzure:app", "--host", "0.0.0.0", "--port", "8000"]