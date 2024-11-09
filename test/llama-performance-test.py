import time
import requests
import threading
import statistics

API_URL = "http://34.238.0.250:5000/api"
API_KEY = "_p0fhuNaCq9H8tS8b5OxtLE5VGieqFY4IrMNp9UUFPc"
PAYLOAD = {"query": "What is the capital of France?"}
HEADERS = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# Global variable to store the model name
model_name = None

# Function to send request and record the time taken
def send_request(results, index):
    global model_name
    start_time = time.time()
    response = requests.post(API_URL, headers=HEADERS, json=PAYLOAD)
    end_time = time.time()

    duration = end_time - start_time
    if response.status_code == 200:
        data = response.json()

        # Extract the model name from the first successful response
        if model_name is None and 'model' in data:
            model_name = data['model']
        results[index] = duration
    else:
        results[index] = None

# Function to test performance with a given number of threads
def test_performance(thread_count):
    results = [None] * thread_count
    threads = []

    for i in range(thread_count):
        thread = threading.Thread(target=send_request, args=(results, i))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    # Filter out failed requests
    successful_results = [result for result in results if result is not None]

    if successful_results:
        avg_response_time = sum(successful_results) / len(successful_results)
        response_times = " : ".join(f"{result:.2f}" for result in successful_results)
        return avg_response_time, response_times
    else:
        return None, None

# Function to run the tests with increasing thread counts
def run_tests():
    global model_name
    thread_count = 5  # Start with 1 thread
    step_size = 5     # Increment by 2 threads
    repeat_count = 1  # Run each test multiple times for an average result
    max_threads = 100  # Ensure at least x threads are tested

    best_avg_time = float('inf')
    best_thread_count = 0

    # Run the first test to capture the model name
    test_performance(1)

    # Print the model name before printing the table header
    if model_name:
        print(f"Model: {model_name}")

    print("Threads | Avg Time | Reply Times")
    print("-------------------------------------------------------------")

    while thread_count <= max_threads:
        all_avg_times = []
        all_reply_times = []

        for _ in range(repeat_count):
            avg_time, reply_times = test_performance(thread_count)
            if avg_time is not None:
                all_avg_times.append(avg_time)
                all_reply_times.append(reply_times)

        if all_avg_times:
            final_avg_time = sum(all_avg_times) / len(all_avg_times)
            final_reply_times = " | ".join(all_reply_times)

            print(f"{thread_count:7} | {final_avg_time:.2f}     | {final_reply_times}")

            # Check if this is the best average time
            if final_avg_time < best_avg_time:
                best_avg_time = final_avg_time
                best_thread_count = thread_count

        else:
            print(f"{thread_count:7} | Failed to get response from API")

        # Increment the thread count
        thread_count += step_size

if __name__ == "__main__":
    run_tests()
