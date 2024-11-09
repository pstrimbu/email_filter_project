import os
import time
import requests
import threading

# Set your API key and base URL for the requests
API_URL = "http://34.238.0.250:5000/api"
API_KEY = "_p0fhuNaCq9H8tS8b5OxtLE5VGieqFY4IrMNp9UUFPc"
HEADERS = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# Available models
models = [
    "llama3.2:latest", #fast (<1 to 6 seconds), provides a 0 or 1 response
    "gemma:7b", # fast (8 to 20 seconds), provides a 0 or 1 response
    "mistral-openorca:latest" # (9 to 20 seconds). provides a 0 or 1 response

    # WONT WORK
    # "wizard-vicuna", # requires more than 16gb of ram
    # "llama2-uncensored", # can't get it to provide a single digit response
    # "mistral-small:latest", # requires more than 16gb of ram
    # "gemma:2b", # super fast. responds with 'is relevant', 'is related'
    # "llama3:latest", # 7 to 15 seconds. but inconsistent results.  sometimes 0/1, sometimes text.
    # "llama2:7b", # fast. 4 to 20 seconds.provides inconsistent results. sometimes 0/1, sometimes text.
    # "llama3.2:1b", # fast (6 to 10 seconds) but censored -- won't assist with financial issues, responds with text
    # "phi3.5:latest", # slow. 20 to 60 seconds. provides a 0 or 1 mixed with text response

]

# Directories for test payloads and results
PAYLOAD_DIR = "test-payloads"
RESULTS_DIR = "test-results"

# Ensure the necessary directories exist
if not os.path.exists(PAYLOAD_DIR):
    os.makedirs(PAYLOAD_DIR)

if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

# Get the current working directory (runtime directory in VSCode)
runtime_dir = os.getcwd()

# Function to adjust email text to exactly match the required size
def adjust_to_size(text, size):
    if len(text) > size:
        return text[:size]  # Truncate if too large
    else:
        # Pad if too small
        return text + ' ' * (size - len(text))

# Function to generate the base 5000-byte email text using the provided API
def generate_base_email_text(byte_size, topic, model):
    payload = {
        "query": f"Generate a sample email with exactly {byte_size} bytes regarding {topic}.",
        "model": model
    }

    response = requests.post(API_URL, headers=HEADERS, json=payload)
    if response.status_code == 200:
        data = response.json()
        return data.get("response", "")
    else:
        print(f"Failed to generate email for model {model}: {response.status_code}")
        return ""

# Function to load or generate the base 5000-byte payload, and then truncate it for each size
def get_or_generate_payloads(byte_size, topic, model):
    base_payload_file = os.path.join(PAYLOAD_DIR, f"test-payload-5000.txt")

    payload = ""

    # Check if the base 5000-byte payload exists; if not, generate it
    if os.path.exists(base_payload_file):
        with open(base_payload_file, 'r') as file:
            payload = file.read()
    else:
        email = generate_base_email_text(5000, topic, model)
        user_prompt = f"business regarding the company ntech, any stock purchase, transfer, or sale"
        payload = (f"Evaluate the entire content of an email, including its subject, body text, and any attachments. "
                f"If the email content matches or is relevant to the topic(s) provided, respond with exactly '1'. "
                f"If the email content does not match or is not relevant, respond with exactly '0'. "
                f"Respond with only the single digit '0' or '1'. Provide no other preamble, text, explanation, or analysis. "
                f"The topics to consider are: {user_prompt}. The email is: {email}")

        with open(base_payload_file, 'w') as file:
            file.write(payload)

    # Ensure the size matches the required payload size by truncating
    return adjust_to_size(payload, byte_size)

# Function to send request and record the time taken
def send_request(results, index, payload, model):
    start_time = time.time()
    response = requests.post(API_URL, headers=HEADERS, json={"query": payload, "model": model})
    end_time = time.time()

    duration = end_time - start_time
    if response.status_code == 200:
        results[index] = duration
    else:
        results[index] = None

# Function to test performance with 8 threads using the same payload
def test_performance(payload, model):
    thread_count = 8
    results = [None] * thread_count
    threads = []

    for i in range(thread_count):
        thread = threading.Thread(target=send_request, args=(results, i, payload, model))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    successful_results = [result for result in results if result is not None]
    if successful_results:
        avg_response_time = sum(successful_results) / len(successful_results)
        response_times = " : ".join(f"{result:.2f}" for result in successful_results)
        return avg_response_time, response_times
    else:
        return None, None

# Function to run the tests with increasing payload sizes and store results for each model
def run_tests_for_model(model):
    payload_sizes = range(500, 5500, 500)  # From 500 to 5000 bytes in increments of 500
    repeat_count = 1  # Run each test multiple times for an average result
    thread_count = 8

    # Define the topic for relevant emails
    matching_topic = "business regarding the company ntech, ntech properties, shareholders, or any stock purchase, transfer, or sale"

    # Output file for results
    result_file_path = os.path.join(RESULTS_DIR, f"test-results-{model.replace(':', '-')}.txt")

    with open(result_file_path, 'w') as result_file:
        print(f"Testing model: {model}")
        result_file.write(f"Testing model: {model}\n")
        result_file.write("Payload Size | Avg Time | Reply Times\n")
        result_file.write("-------------------------------------------------------------\n")

        print("Payload Size | Avg Time | Reply Times")
        print("-------------------------------------------------------------")

        for payload_size in payload_sizes:
            # Get or generate the payload for this size by truncating the 5000-byte payload
            payload = get_or_generate_payloads(payload_size, matching_topic, model)

            all_avg_times = []
            all_reply_times = []

            for _ in range(repeat_count):
                avg_time, reply_times = test_performance(payload, model)
                if avg_time is not None:
                    all_avg_times.append(avg_time)
                    all_reply_times.append(reply_times)

            if all_avg_times:
                final_avg_time = sum(all_avg_times) / len(all_avg_times)
                final_reply_times = " | ".join(all_reply_times)

                result_line = f"{payload_size:11} | {final_avg_time:.2f}     | {final_reply_times}"
                print(result_line)
                result_file.write(result_line + "\n")
            else:
                result_line = f"{payload_size:11} | Failed to get response from API"
                print(result_line)
                result_file.write(result_line + "\n")

# Function to run tests across all models
def run_tests():
    for model in models:
        run_tests_for_model(model)

if __name__ == "__main__":
    run_tests()
