import os
import boto3
import time
from threading import Thread, Lock
from datetime import datetime, timedelta
from .models import Result, EmailAccount
from .extensions import db
from botocore.exceptions import ClientError
from email_filter.logger import update_log_entry

# AWS configuration using environment variables
REGION = os.getenv("AWS_REGION", "us-east-1")
AMI_ID = os.getenv("AWS_AMI_ID", "ami-0b3436f14413b755b")
INSTANCE_TYPE = os.getenv("AWS_INSTANCE_TYPE", "g5.xlarge")
SPOT_PRICE = os.getenv("AWS_SPOT_PRICE", "0.50")
SUBNET_ID = os.getenv("AWS_SUBNET_ID", "subnet-09f6d9ea2063e4784")
SECURITY_GROUP_IDS = os.getenv("AWS_SECURITY_GROUP_IDS", "sg-07a3d8b246cafe58b,sg-0ac0b571e88605a5f").split(',')
KEY_NAME = os.getenv("AWS_KEY_NAME", "timamap")

# Initialize Boto3 EC2 client
session = boto3.Session(profile_name="amplify-app", region_name=REGION)
ec2_client = session.client("ec2")
bucket_name = 'mailmatch'

# Global state variables
current_instance_id = None
spot_request_id = None
monitor_thread = None
instance_is_active = False
last_interaction = None
interaction_lock = Lock()
timeout_minutes = 5  # Timeout in minutes after last interaction
instance_launch_time = None  # Initialize instance_launch_time
max_runtime_minutes = 60  # Define maximum runtime in minutes

# Global list to track active users
active_users = []

def log(message):
    """Helper function to print logs with timestamps."""
    print(f"[{datetime.now()}] {message}")

def monitor_instance_status():
    """Monitors the spot instance status and handles status changes."""
    global current_instance_id, instance_is_active, spot_request_id

    while current_instance_id:
        with interaction_lock:
            # Check for inactivity
            if last_interaction and datetime.now() - last_interaction > timedelta(minutes=timeout_minutes):
                log("Instance inactive for 5 minutes. Terminating...")
                terminate_instance()
                current_instance_id = None

            # Check for maximum runtime
            if instance_launch_time and datetime.now() - instance_launch_time > timedelta(minutes=max_runtime_minutes):
                log("Instance has reached maximum runtime. Terminating...")
                terminate_instance()

        # Check instance state and spot request state
        if current_instance_id:
            try:
                response = ec2_client.describe_instance_status(InstanceIds=[current_instance_id])
                statuses = response.get("InstanceStatuses", [])
                if not statuses:
                    log("Instance not found or is terminated.")
                    current_instance_id = None
                    instance_is_active = False                    
                    continue

                instance_state = statuses[0]["InstanceState"]["Name"]
                instance_status = statuses[0]["InstanceStatus"]["Status"]
                # log(f"Instance state: {instance_state}, Status: {instance_status}")

                if instance_state in ["stopping", "shutting-down", "terminated"]:
                    instance_is_active = False
                    current_instance_id = None
                    log("Instance is shutting down. Pausing tasks...")

                elif instance_status == "ok" and instance_state == "running":
                    instance_is_active = True
                    # log("Instance is running. Resuming tasks...")

            except Exception as e:
                current_instance_id = None
                log(f"Error checking instance status: {e}")

        # Check the Spot request state
        if spot_request_id:
            try:
                spot_request_response = ec2_client.describe_spot_instance_requests(SpotInstanceRequestIds=[spot_request_id])
                spot_request_state = spot_request_response["SpotInstanceRequests"][0]["State"]
                spot_status_code = spot_request_response["SpotInstanceRequests"][0].get("Status", {}).get("Code", "Unknown")
                # log(f"Spot request state: {spot_request_state}, Status Code: {spot_status_code}")

                if spot_request_state in ["cancelled", "closed", "failed"]:
                    log(f"Spot request is no longer active (state: {spot_request_state}). Terminating instance.")
                    terminate_instance()
                    spot_request_id = None

            except Exception as e:
                spot_request_id = None
                log(f"Error checking spot request status: {e}")

        time.sleep(10)  # Check every 10 seconds

def request_spot_instance(user_id, account_id):
    global current_instance_id, spot_request_id, monitor_thread, instance_is_active, active_users, instance_launch_time

    if user_id not in active_users:
        active_users.append(user_id)

    public_ip = None

    if current_instance_id:
        try:
            instance_info = ec2_client.describe_instances(InstanceIds=[current_instance_id])
            public_ip = instance_info['Reservations'][0]['Instances'][0].get('PublicIpAddress')
            instance_is_active = True
            update_log_entry(user_id, account_id, f"Using existing instance with ID: {current_instance_id} and public IP: {public_ip}")
        except Exception as e:
            update_log_entry(user_id, account_id, f"Error retrieving public IP for existing instance: {e}", status='error')
    else:
        try:
            spot_requests = ec2_client.describe_spot_instance_requests(
                Filters=[{'Name': 'state', 'Values': ['open', 'active']}]
            )['SpotInstanceRequests']

            if spot_requests:
                for request in spot_requests:
                    if request['State'] == 'active':
                        current_instance_id = request['InstanceId']
                        public_ip = get_instance_public_ip(current_instance_id)
                        instance_is_active = True                        
                        update_log_entry(user_id, account_id, f"Using existing AI Server with ID: {current_instance_id} and public IP: {public_ip}")
            else:
                # No active spot requests, so request a new one
                response = ec2_client.request_spot_instances(
                    SpotPrice=SPOT_PRICE,
                    InstanceCount=1,
                    LaunchSpecification={
                        "ImageId": AMI_ID,
                        "InstanceType": INSTANCE_TYPE,
                        "KeyName": KEY_NAME,
                        "NetworkInterfaces": [{
                            "DeviceIndex": 0,
                            "SubnetId": SUBNET_ID,
                            "AssociatePublicIpAddress": True,
                            "Groups": SECURITY_GROUP_IDS
                        }]
                    },
                    Type="one-time"
                )
                spot_request_id = response["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
                update_log_entry(user_id, account_id, f"AI Server requested with request ID: {spot_request_id}")

                if not spot_request_id:
                    update_log_entry(user_id, account_id, "Failed to create AI Server request.", status='error')
                    return None

                while not current_instance_id:
                    try:
                        result = ec2_client.describe_spot_instance_requests(SpotInstanceRequestIds=[spot_request_id])
                        spot_request_details = result["SpotInstanceRequests"][0]
                        instance_id = spot_request_details.get("InstanceId")

                        if instance_id:
                            update_log_entry(user_id, account_id, f"AI Server launched with instance ID: {instance_id}")
                            instance_launch_time = datetime.now()
                            instance_info = ec2_client.describe_instances(InstanceIds=[instance_id])
                            public_ip = instance_info['Reservations'][0]['Instances'][0].get('PublicIpAddress')
                            current_instance_id = instance_id
                            instance_is_active = True
                            update_log_entry(user_id, account_id, f"Instance public IP: {public_ip}")
                        else:
                            update_log_entry(user_id, account_id, "Waiting for AI Server to be fulfilled...")
                            time.sleep(10)
                    except Exception as e:
                        update_log_entry(user_id, account_id, f"Error describing AI Server request: {e}", status='error')
                        break
        except Exception as e:
            update_log_entry(user_id, account_id, f"Error requesting AI Server: {e}", status='error')

    if monitor_thread is None or not monitor_thread.is_alive():
        monitor_thread = Thread(target=monitor_instance_status, daemon=True)
        monitor_thread.start()
    
    return public_ip

def update_last_interaction():
    """Update the last interaction timestamp."""
    global last_interaction
    with interaction_lock:
        last_interaction = datetime.now()
        # log("Updated last interaction timestamp.")

def terminate_instance(user_id=None):
    """Terminate the current spot instance if no active users remain."""
    global current_instance_id, instance_is_active, active_users

    # Remove user from the active users list if user_id is provided
    if user_id and user_id in active_users:
        active_users.remove(user_id)
        log(f"User {user_id} removed from active users list.")

    # Only terminate if no active users remain
    if not active_users and current_instance_id:
        try:
            log(f"Terminating instance {current_instance_id}")
            ec2_client.terminate_instances(InstanceIds=[current_instance_id])
            current_instance_id = None
            instance_is_active = False
        except Exception as e:
            log(f"Error terminating instance: {e}")

def check_status():
    """Check the current spot instance status."""
    if current_instance_id:
        try:
            response = ec2_client.describe_instance_status(InstanceIds=[current_instance_id])
            statuses = response.get("InstanceStatuses", [])
            if statuses:
                instance_state = statuses[0]["InstanceState"]["Name"]
                instance_status = statuses[0]["InstanceStatus"]["Status"]
                # log(f"Instance status check: ID {current_instance_id}, state {instance_state}, status {instance_status}")
                return {
                    "instance_id": current_instance_id,
                    "state": instance_state,
                    "status": instance_status,
                    "is_active": instance_is_active
                }
        except Exception as e:
            log(f"Error checking instance status: {e}")
    return None

def get_instance_public_ip(instance_id):
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
        reservations = response.get('Reservations', [])
        if reservations:
            instances = reservations[0].get('Instances', [])
            if instances:
                public_ip = instances[0].get('PublicIpAddress')
                if public_ip:
                    return public_ip
                else:
                    log(f"No public IP found for instance {instance_id}.")
            else:
                log(f"No instances found in reservation for instance ID {instance_id}.")
        else:
            log(f"No reservations found for instance ID {instance_id}.")
    except Exception as e:
        log(f"Error retrieving public IP for instance {instance_id}: {e}")
    return None

def delete_file(file_id, user_id):
    """Delete a file from S3 and update the database."""
    try:
        result = Result.query.get(file_id)
        
        if not result.name:
            return {'success': False, 'message': 'No file name'}, 404

        # Ensure the result belongs to the current user
        if result.user_id != user_id:
            return {'success': False, 'message': 'Unauthorized action'}, 403

        # Delete the file from S3
        s3 = boto3.client('s3')

        try:
            s3.delete_object(Bucket=bucket_name, Key=result.name)
        except ClientError as e:
            return {'success': False, 'message': str(e)}, 500

        # Update the database
        result.file_url = None
        result.name = None
        db.session.commit()

        return {'success': True}, 200
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'message': str(e)}, 500