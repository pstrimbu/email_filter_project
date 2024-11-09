import os
import time
import requests
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

# Set your API key and base URL for the requests
API_URL = "http://34.238.0.250:5000/api"
API_KEY = "_p0fhuNaCq9H8tS8b5OxtLE5VGieqFY4IrMNp9UUFPc"
HEADERS = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# Available models
models = [
    "llama3.2:latest",
    "gemma:7b",
    "mistral-openorca:latest"
]

# Directory for test payloads
PAYLOAD_DIR = "payloads"

# Ensure the payload directory exists
if not os.path.exists(PAYLOAD_DIR):
    print(f"Payload directory '{PAYLOAD_DIR}' does not exist.")
    exit(1)

# Function to read all payloads from the payloads directory
def load_payloads():
    payloads = []
    for filename in os.listdir(PAYLOAD_DIR):
        file_path = os.path.join(PAYLOAD_DIR, filename)
        if os.path.isfile(file_path) and filename.endswith(".txt"):
            with open(file_path, 'r') as file:
                payload_content = file.read()
                payloads.append(payload_content)
    return payloads

# Function to send request and return payload size, response time, and first 10 characters of the response
def send_request(payload, model):
    payload_size = len(payload.encode('utf-8'))
    start_time = time.time()
    response = requests.post(API_URL, headers=HEADERS, json={"query": payload[:2000], "model": model})
    end_time = time.time()

    duration = end_time - start_time
    if response.status_code == 200:
        response_text = response.json().get("response", "")[:10]  # Get the first 10 characters of the response
        return f"size: {payload_size} bytes | time: {duration:.2f} | response: '{response_text}'"
    else:
        return f"size: {payload_size} bytes | time: {duration:.2f} | Failed to get response from API"

# Function to test performance by sending 50 payloads in random order with 8 parallel threads
def test_performance(payloads, model):
    random.shuffle(payloads)  # Randomize the order
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(send_request, random.choice(payloads), model) for _ in range(50)]
        for future in as_completed(futures):
            print(future.result())

# Function to run tests across all models
def run_tests():
    payloads = load_payloads()
    if not payloads:
        print("No payloads found in the payloads directory.")
        return

    for model in models:
        print(f"Testing model: {model}")
        test_performance(payloads, model)

if __name__ == "__main__":
    run_tests()
