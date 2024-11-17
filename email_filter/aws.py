import os
import boto3
import time
import asyncio
import logging
from threading import Thread, Lock
from datetime import datetime, timedelta
from dotenv import load_dotenv
from botocore.exceptions import ClientError, ProfileNotFound
from email_filter.logger import update_log_entry
from email_filter.globals import processing_status

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')

# Debug flag
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

def log_debug(user_id, account_id, message):
    if DEBUG_MODE:
        update_log_entry(user_id, account_id, f"DEBUG: {message}")

class InstanceManager:
    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.instance_id = os.getenv("INSTANCE_ID")
        self.processor_type = os.getenv("PROCESSOR_TYPE", "spot")
        
        # Ensure the session is initialized with the correct profile and region
        self.session = boto3.Session(
            profile_name=os.getenv("AWS_PROFILE", "default"),  # Use 'default' if no profile is specified
            region_name=self.region
        )
        
        # Validate the AWS profile
        if not self._is_valid_aws_profile():
            log_debug(None, None, "Invalid AWS profile configuration.")
            raise ValueError("Invalid AWS profile configuration.")
        
        # Initialize the EC2 client
        self.ec2_client = self.session.client("ec2")
        
        self.interaction_lock = Lock()
        self.active_users = set()
        self.last_interaction = None
        self.timeout_minutes = 5
        self.monitor_thread = None
        self.user_id = None
        self.account_id = None
        self._public_ip = None

    def _is_valid_aws_profile(self):
        """Check if the AWS profile is valid."""
        try:
            # Attempt to get the current region to verify the session
            region = self.session.region_name
            logging.info(f"Profile is valid. Region: {region}")
            return True
        except ProfileNotFound:
            logging.error("Profile not found.")
            return False
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            return False

    def set_public_ip(self, ip_address):
        """Set the public IP address."""
        self._public_ip = ip_address

    def get_public_ip(self):
        """Get the public IP address."""
        return self._public_ip

    async def request_instance(self, user_id=None, account_id=None):
        log_debug(user_id, account_id, "Entering request_instance function")
        try:
            if not self.monitor_thread:
                self.monitor_thread = Thread(target=self.start_monitoring)
                self.monitor_thread.start()

            if self.user_id:
                self.user_id = user_id
            else:
                user_id = self.user_id

            if self.account_id:
                account_id = self.account_id
            else:
                account_id = account_id
                
            last_logged_time = datetime.now()
            self.active_users.add(user_id)
            while True:
                response = self.ec2_client.describe_instances(InstanceIds=[self.instance_id])
                state = response['Reservations'][0]['Instances'][0]['State']['Name']
                status = None
                
                if state == 'stopped':
                    self.ec2_client.start_instances(InstanceIds=[self.instance_id])
                    logging.info(f"Starting instance {self.instance_id}.")
                elif state == 'running':
                    instance_status = self.ec2_client.describe_instance_status(InstanceIds=[self.instance_id])
                    status = instance_status['InstanceStatuses'][0]['InstanceStatus']['Status'] if instance_status['InstanceStatuses'] else None
                    if status == 'ok':
                        update_log_entry(user_id, account_id, f"AI Server {self.instance_id} is now running and ready for requests.")
                        logging.info(f"Instance {self.instance_id} is now running and ready for requests.")
                        break

                # Log the current state every 30 seconds
                current_time = datetime.now()
                if (current_time - last_logged_time).total_seconds() >= 30:
                    update_log_entry(user_id, account_id, f"Waiting for AI Server to be ready. Current state: {state}, Status: {status}")
                    last_logged_time = current_time

                await asyncio.sleep(5)  # Adjusted to 5 seconds

            # Retrieve the public IP address
            response = self.ec2_client.describe_instances(InstanceIds=[self.instance_id])
            public_ip = response['Reservations'][0]['Instances'][0].get('PublicIpAddress')
            if public_ip:
                self.set_public_ip(public_ip)
                update_log_entry(user_id, account_id, f"AI Server active: {self.instance_id} has public IP: {public_ip}")    
                logging.info(f"Instance {self.instance_id} has public IP: {public_ip}")
                return public_ip
            else:
                logging.error(f"Public IP not available for instance {self.instance_id}.")
                return None
        except ClientError as e:
            log_debug(user_id, account_id, f"Error managing on-demand instance: {e}")
            return None

    def log(self, message):
        """Helper function to log messages with timestamps."""
        logging.info(message)

    def terminate_instance(self, user_id=None):
        """Terminate the instance if no active users remain."""
        with self.interaction_lock:
            if user_id:
                self.active_users.discard(user_id)
                logging.info(f"User {user_id} removed from active users list.")

    async def monitor_instance_status(self):
        await monitor_instance_status(self)

    def start_monitoring(self):
        asyncio.run(self.monitor_instance_status())

    def update_last_interaction(self):
        """Update the last interaction timestamp."""
        with self.interaction_lock:
            self.last_interaction = datetime.now()

    def check_status(self):
        """Check if the instance is currently active and status is 'ok'."""
        with self.interaction_lock:
            response = self.ec2_client.describe_instances(InstanceIds=[self.instance_id])
            state = response['Reservations'][0]['Instances'][0]['State']['Name']
            instance_status = self.ec2_client.describe_instance_status(InstanceIds=[self.instance_id])
            status = instance_status['InstanceStatuses'][0]['InstanceStatus']['Status'] if instance_status['InstanceStatuses'] else None
            return state == 'running' and status == 'ok'
    
    def stop_instance(self):
        self.log(f"Stopping instance {self.instance_id}")
        self.ec2_client.stop_instances(InstanceIds=[self.instance_id])


class SpotInstanceManager:
    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.ami_id = os.getenv("AWS_AMI_ID", "ami-0b3436f14413b755b")
        self.instance_type = os.getenv("AWS_INSTANCE_TYPE", "g5.xlarge")
        self.spot_price = os.getenv("AWS_SPOT_PRICE", "0.50")
        self.subnet_id = os.getenv("AWS_SUBNET_ID", "subnet-09f6d9ea2063e4784")
        self.security_group_ids = os.getenv("AWS_SECURITY_GROUP_IDS", "").split(',')
        self.key_name = os.getenv("AWS_KEY_NAME", "timamap")

        # Ensure the session is initialized with the correct profile and region
        self.session = boto3.Session(
            profile_name=os.getenv("AWS_PROFILE", "default"),  # Use 'default' if no profile is specified
            region_name=self.region
        )
        
        # Validate the AWS profile
        if not self._is_valid_aws_profile():
            log_debug(None, None, "Invalid AWS profile configuration.")
            raise ValueError("Invalid AWS profile configuration.")
        
        # Initialize the EC2 client
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

        self.user_id = None
        self.account_id = None
        self._public_ip = None

    def _is_valid_aws_profile(self):
        """Check if the AWS profile is valid."""
        try:
            # Attempt to get the current region to verify the session
            region = self.session.region_name
            logging.info(f"Profile is valid. Region: {region}")
            return True
        except ProfileNotFound:
            logging.error("Profile not found.")
            return False
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            return False

    def set_public_ip(self, ip_address):
        """Set the public IP address."""
        self._public_ip = ip_address

    def get_public_ip(self):
        """Get the public IP address."""
        return self._public_ip

    def log(self, message):
        """Helper function to log messages with timestamps."""
        logging.info(message)

    async def request_instance(self, user_id=None, account_id=None):
        log_debug(user_id, account_id, "Entering request_instance function")
        if not self.monitor_thread:
            self.monitor_thread = Thread(target=self.start_monitoring)
            self.monitor_thread.start()

        if self.user_id:
            self.user_id = user_id
        else:
            user_id = self.user_id

        if self.account_id:
            account_id = self.account_id
        else:
            account_id = account_id

        """Requests or reuses a spot instance asynchronously."""
        async with asyncio.Lock():
            self.active_users.add(user_id)

            # If instance is already running, reuse it
            if self.instance_id:
                log_debug(user_id, account_id, f"Using existing instance with ID: {self.instance_id}")
                public_ip = await self._get_instance_public_ip(self.instance_id)
                if public_ip:
                    self.set_public_ip(public_ip)
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
                            self.set_public_ip(public_ip)
                            update_log_entry(user_id, account_id, f"Found registered AI Server with ID: {self.instance_id} and public IP: {public_ip}")
                            return public_ip
            except ClientError as e:
                self.log(f"Error checking for active spot instances: {e}")

            # Request a new spot instance
            update_log_entry(user_id, account_id, "Requesting new AI Server.")
            try:
                response = await self._request_spot_instance(user_id, account_id)
                self.spot_request_id = response["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
                update_log_entry(user_id, account_id, f"AI Server requested with ID: {self.spot_request_id}")
                return await self._wait_for_instance_launch(user_id, account_id)
            except ClientError as e:
                update_log_entry(user_id, account_id, f"Error requesting spot instance: {e}", status='error')
                return None

    async def _request_spot_instance(self, user_id, account_id):
        log_debug(user_id, account_id, "Entering _request_spot_instance function")
        global processing_status
        """Request a new spot instance with retries."""
        for attempt in range(20):
            if processing_status.get((user_id, account_id)) == 'stopping':
                return None
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
        log_debug(user_id, account_id, "Entering _wait_for_instance_launch function")
        global processing_status
        """Wait for the spot instance to launch, then retrieve the public IP."""
        for attempt in range(20):
            if processing_status.get((user_id, account_id)) == 'stopping':
                return None
            try:
                result = self.ec2_client.describe_spot_instance_requests(SpotInstanceRequestIds=[self.spot_request_id])
                instance_id = result["SpotInstanceRequests"][0].get("InstanceId")
                if instance_id:
                    self.instance_id = instance_id
                    self.instance_launch_time = datetime.now()
                    self.instance_is_active = True
                    public_ip = await self._get_instance_public_ip(instance_id)
                    if public_ip:
                        self.set_public_ip(public_ip)
                        update_log_entry(user_id, account_id, f"Instance launched with ID: {instance_id} and public IP: {public_ip}")
                        return public_ip
            except ClientError as e:
                update_log_entry(user_id, account_id, f"Waiting for instance launch, attempt {attempt + 1}: {e}", status='error')
            await asyncio.sleep(2 ** attempt)
        update_log_entry(user_id, account_id, "AI Server launch timed out", status='error')
        return None

    async def _get_instance_public_ip(self, instance_id):
        log_debug(None, None, f"Entering _get_instance_public_ip function for instance {instance_id}")
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
                
    def stop_instance(self):
        try:
            self.log(f"Terminating spot instance {self.instance_id}")
            self.ec2_client.terminate_instances(InstanceIds=[self.instance_id])
            self.instance_id = None
            self.instance_is_active = False
        except ClientError as e:
            self.log(f"Error terminating instance: {e}")

    async def monitor_instance_status(self):
        await monitor_instance_status(self)

    def start_monitoring(self):
        asyncio.run(self.monitor_instance_status())


async def monitor_instance_status(manager):
    """Shared monitoring logic for both InstanceManager and SpotInstanceManager."""
    log_debug(None, None, "Entering monitor_instance_status function")
    no_active_users_since = None  # Track when no active users were first detected

    while True:
        await asyncio.sleep(30)
        with manager.interaction_lock:
            if manager.active_users and not manager.instance_id:
                manager.request_instance()

            if manager.instance_id and not manager.active_users:
                if no_active_users_since is None:
                    no_active_users_since = datetime.now()
                    manager.log("No active users detected. Starting 15-minute countdown to stop instance.")
                elif (datetime.now() - no_active_users_since).total_seconds() >= 900:
                    manager.log("No active users for 15 minutes. Stopping instance.")
                    manager.stop_instance()
                    no_active_users_since = None  # Reset the timer
            else:
                if manager.active_users:                    
                    no_active_users_since = None # Reset the timer if there are active users or no instance


def delete_file_from_s3(bucket_name='mailmatch', file_key=None):
    """Delete a file from S3 for the given user and account."""
    try:
        s3_client = boto3.client('s3')
        s3_client.delete_object(Bucket=bucket_name, Key=file_key)
        logging.info(f"Deleted file {file_key} from bucket {bucket_name}.")
        return True
    except ClientError as e:
        logging.error(f"Error deleting {file_key} from S3 bucket {bucket_name}: {e}")
        return False

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
