import os
import boto3
import time
import asyncio
import logging
from threading import Thread, Lock
from datetime import datetime, timedelta
from dotenv import load_dotenv
from botocore.exceptions import ClientError
from email_filter.logger import update_log_entry

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')

class SpotInstanceManager:
    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.ami_id = os.getenv("AWS_AMI_ID", "ami-0b3436f14413b755b")
        self.instance_type = os.getenv("AWS_INSTANCE_TYPE", "g5.xlarge")
        self.spot_price = os.getenv("AWS_SPOT_PRICE", "0.50")
        self.subnet_id = os.getenv("AWS_SUBNET_ID", "subnet-09f6d9ea2063e4784")
        self.security_group_ids = os.getenv("AWS_SECURITY_GROUP_IDS", "").split(',')
        self.key_name = os.getenv("AWS_KEY_NAME", "timamap")

        self.session = boto3.Session(profile_name="amplify-app", region_name=self.region)
        self.ec2_client = self.session.client("ec2")

        # Lock and threading setup
        self.interaction_lock = Lock()
        self.monitor_thread = None

        # State variables
        self.instance_id = None
        self.spot_request_id = None
        self.instance_is_active = False
        self.last_interaction = None
        self.instance_launch_time = None
        self.active_users = set()

        # Timeout and runtime configurations
        self.timeout_minutes = 5
        self.max_runtime_minutes = 60

    def log(self, message):
        """Helper function to log messages with timestamps."""
        logging.info(message)

    async def request_instance(self, user_id, account_id):
        """Requests or reuses a spot instance asynchronously."""
        async with asyncio.Lock():
            self.active_users.add(user_id)

            # If instance is already running, reuse it
            if self.instance_id:
                self.log(f"Using existing instance with ID: {self.instance_id}")
                public_ip = await self._get_instance_public_ip(self.instance_id)
                if public_ip:
                    update_log_entry(user_id, account_id, f"Using existing instance with ID: {self.instance_id} and public IP: {public_ip}")
                else:
                    update_log_entry(user_id, account_id, "Public IP not available for existing instance.", status='error')
                return public_ip
            else:
                update_log_entry(user_id, account_id, "No active AI Server found. Checking for other registered AI Servers.")

            try:
                response = self.ec2_client.describe_instances(
                    Filters=[
                        {'Name': 'instance-state-name', 'Values': ['running']},
                        {'Name': 'instance-lifecycle', 'Values': ['spot']}
                    ]
                )
                for reservation in response['Reservations']:
                    for instance in reservation['Instances']:
                        self.instance_id = instance['InstanceId']
                        self.instance_launch_time = datetime.now()
                        self.instance_is_active = True
                        public_ip = await self._get_instance_public_ip(self.instance_id)
                        if public_ip:
                            update_log_entry(user_id, account_id, f"Found registered AI Server with ID: {self.instance_id} and public IP: {public_ip}")
                            return public_ip
            except ClientError as e:
                self.log(f"Error checking for active spot instances: {e}")

            # Request a new spot instance
            update_log_entry(user_id, account_id, "Requesting new AI Server.")
            try:
                response = await self._request_spot_instance()
                self.spot_request_id = response["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
                update_log_entry(user_id, account_id, f"AI Server requested with ID: {self.spot_request_id}")
                return await self._wait_for_instance_launch(user_id, account_id)
            except ClientError as e:
                update_log_entry(user_id, account_id, f"Error requesting spot instance: {e}", status='error')
                return None

    async def _request_spot_instance(self):
        """Request a new spot instance with retries."""
        for attempt in range(5):
            try:
                return self.ec2_client.request_spot_instances(
                    SpotPrice=self.spot_price,
                    InstanceCount=1,
                    LaunchSpecification={
                        "ImageId": self.ami_id,
                        "InstanceType": self.instance_type,
                        "KeyName": self.key_name,
                        "NetworkInterfaces": [{
                            "DeviceIndex": 0,
                            "SubnetId": self.subnet_id,
                            "AssociatePublicIpAddress": True,
                            "Groups": self.security_group_ids
                        }]
                    },
                    Type="one-time"
                )
            except ClientError as e:
                self.log(f"Retry {attempt + 1} for spot request due to error: {e}")
                await asyncio.sleep(2 ** attempt)
        raise ClientError("Failed to request spot instance after multiple attempts.")

    async def _wait_for_instance_launch(self, user_id, account_id):
        """Wait for the spot instance to launch, then retrieve the public IP."""
        for attempt in range(12):
            try:
                result = self.ec2_client.describe_spot_instance_requests(SpotInstanceRequestIds=[self.spot_request_id])
                instance_id = result["SpotInstanceRequests"][0].get("InstanceId")
                if instance_id:
                    self.instance_id = instance_id
                    self.instance_launch_time = datetime.now()
                    self.instance_is_active = True
                    public_ip = await self._get_instance_public_ip(instance_id)
                    if public_ip:
                        update_log_entry(user_id, account_id, f"Instance launched with ID: {instance_id} and public IP: {public_ip}")
                        return public_ip
            except ClientError as e:
                update_log_entry(user_id, account_id, f"Waiting for instance launch, attempt {attempt + 1}: {e}", status='error')
            await asyncio.sleep(2 ** attempt)
        update_log_entry(user_id, account_id, "AI Server launch timed out", status='error')
        return None

    async def _get_instance_public_ip(self, instance_id):
        """Retrieve the public IP of an instance asynchronously."""
        for attempt in range(5):
            try:
                response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
                public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress')
                if public_ip:
                    return public_ip
            except ClientError as e:
                self.log(f"Retry {attempt + 1} for public IP retrieval due to error: {e}")
                await asyncio.sleep(2 ** attempt)
        self.log(f"Failed to retrieve public IP for instance {instance_id} after multiple attempts.")
        return None

    def check_status(self):
        """Check if the instance is currently active."""
        with self.interaction_lock:
            return self.instance_is_active

    def update_last_interaction(self):
        """Update the last interaction timestamp."""
        with self.interaction_lock:
            self.last_interaction = datetime.now()

    def terminate_instance(self, user_id=None):
        """Terminate the current spot instance if no active users remain."""
        with self.interaction_lock:
            if user_id:
                self.active_users.discard(user_id)
                self.log(f"User {user_id} removed from active users list.")
            if not self.active_users and self.instance_id:
                try:
                    self.log(f"Terminating instance {self.instance_id}")
                    self.ec2_client.terminate_instances(InstanceIds=[self.instance_id])
                    self.instance_id = None
                    self.instance_is_active = False
                except ClientError as e:
                    self.log(f"Error terminating instance: {e}")

    async def monitor_instance_status(self):
        """Asynchronous monitoring of instance for inactivity or max runtime."""
        while True:
            await asyncio.sleep(10)
            with self.interaction_lock:
                if self.instance_id:
                    now = datetime.now()
                    if self.last_interaction and now - self.last_interaction > timedelta(minutes=self.timeout_minutes):
                        self.log("Instance inactive. Terminating due to timeout.")
                        self.terminate_instance()
                    elif self.instance_launch_time and now - self.instance_launch_time > timedelta(minutes=self.max_runtime_minutes):
                        self.log("Instance reached max runtime. Terminating.")
                        self.terminate_instance()

def delete_file_from_s3(user_id, account_id, bucket_name='mailmatch'):
    """Delete a file from S3 for the given user and account."""
    try:
        s3_client = boto3.client('s3')
        file_key = f"{user_id}/{account_id}/file_to_delete"  # Update with actual file key logic
        s3_client.delete_object(Bucket=bucket_name, Key=file_key)
        logging.info(f"Deleted file {file_key} from bucket {bucket_name}.")
    except ClientError as e:
        logging.error(f"Error deleting file from S3: {e}")
        raise

def upload_file_to_s3(file_path, bucket_name, file_key):
    """Upload a file to S3."""
    try:
        s3_client = boto3.client('s3')
        s3_client.upload_file(file_path, bucket_name, file_key)
        logging.info(f"Uploaded file {file_key} to bucket {bucket_name}.")
    except ClientError as e:
        logging.error(f"Error uploading file to S3: {e}")
        raise

def generate_presigned_url(bucket_name, file_key, expiration=2592000):
    """Generate a presigned URL for an S3 object."""
    try:
        s3_client = boto3.client('s3')
        presigned_url = s3_client.generate_presigned_url('get_object',
                                                         Params={'Bucket': bucket_name, 'Key': file_key},
                                                         ExpiresIn=expiration)
        logging.info(f"Generated presigned URL for {file_key}.")
        return presigned_url
    except ClientError as e:
        logging.error(f"Error generating presigned URL: {e}")
        raise
