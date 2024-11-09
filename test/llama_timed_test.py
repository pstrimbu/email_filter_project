import time
import requests
import threading

API_URL = "http://34.238.0.250:5000/api"
API_KEY = "_p0fhuNaCq9H8tS8b5OxtLE5VGieqFY4IrMNp9UUFPc"
PAYLOAD = {"query": "What is the capital of France?"}
HEADERS = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# Global variables
model_name = None
backlog_threads = 0  # Global backlog count
responses_received = []  # To store response times during an interval
lock = threading.Lock()

# Function to send request and record the time taken
def send_request():
    global model_name, backlog_threads, responses_received
    start_time = time.time()

    # Increment the backlog counter when sending a request
    with lock:
        backlog_threads += 1

    response = requests.post(API_URL, headers=HEADERS, json=PAYLOAD)
    end_time = time.time()

    duration = end_time - start_time

    if response.status_code == 200:
        data = response.json()

        # Extract the model name from the first successful response
        if model_name is None and 'model' in data:
            model_name = data['model']

        # Store the duration in responses received during the interval
        with lock:
            responses_received.append(duration)

    # Decrement the backlog counter once the request is completed
    with lock:
        backlog_threads -= 1

# Function to fire off a batch of requests
def fire_requests(thread_count):
    threads = []

    for _ in range(thread_count):
        thread = threading.Thread(target=send_request)
        threads.append(thread)
        thread.start()

    return threads

# Function to calculate and print the results every interval
def print_results(interval):
    global responses_received

    with lock:
        if responses_received:
            avg_response_time = sum(responses_received) / len(responses_received)
            response_count = len(responses_received)
        else:
            avg_response_time = 0
            response_count = 0

        # Print the average response time and backlog threads count
        print(f"{time.time():.2f} | {avg_response_time:.2f} | {response_count} | {interval:.2f}s | {backlog_threads} ")

        # Clear the list for the next interval
        responses_received = []

# Function to run the test with constant rate and reducing interval
def run_tests():
    global model_name, backlog_threads
    thread_count = 4  # Fire 4 requests per batch
    interval = 4.0    # Start with 4 seconds between intervals
    decrement_factor = 0.9  # Reduce interval by 10%
    interval_adjust_time = 20  # Reduce interval every 20 seconds
    last_adjustment_time = time.time()

    # Print model name once at the start
    print(f"Model: {model_name if model_name else 'Unknown (Will fetch soon)'}")
    print("Time | Avg Time | Response Count | Interval (s) | Backlog Threads")
    print("------------------------------------------------------------------------")

    while True:
        # Fire off a batch of requests
        fire_requests(thread_count)

        # Sleep for the current interval
        time.sleep(interval)

        # Print the results for this interval
        print_results(interval)

        # Adjust the interval every 20 seconds
        if time.time() - last_adjustment_time >= interval_adjust_time:
            interval = max(interval * decrement_factor, 0.1)  # Don't let it go below 0.1 seconds
            last_adjustment_time = time.time()

if __name__ == "__main__":
    run_tests()
