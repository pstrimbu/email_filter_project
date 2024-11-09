import boto3
import json
import zipfile
import os

# Initialize clients
session = boto3.Session(profile_name="amplify-app", region_name="us-east-1")
ec2_client = session.client("ec2")
lambda_client = session.client("lambda")
iam_client = session.client("iam")
eventbridge_client = session.client("events")

# Configuration parameters
AMI_ID = "ami-0b3436f14413b755b"
INSTANCE_TYPE = "g5.xlarge"
SPOT_PRICE = "0.50"
KEY_NAME = "/Users/peterstrimbu/dev/ssh/keys/timamap.pem"
SUBNET_ID = "subnet-09f6d9ea2063e4784"
SECURITY_GROUP_IDS = ["sg-07a3d8b246cafe58b", "sg-0ac0b571e88605a5f"]
LAMBDA_ROLE_NAME = "SpotInstanceTerminationHandlerRole"
LAMBDA_FUNCTION_NAME = "HandleSpotTermination"
EVENT_RULE_NAME = "SpotInstanceTerminationNotification"
ZIP_FILE_NAME = "lambda_function.zip"

# Step 1: Create IAM Role for Lambda Function
assume_role_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

try:
    role_response = iam_client.create_role(
        RoleName=LAMBDA_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(assume_role_policy),
    )
    role_arn = role_response["Role"]["Arn"]

    # Attach policy for EC2 and CloudWatch permissions
    iam_client.attach_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/AmazonEC2FullAccess"
    )
    iam_client.attach_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    )
    print(f"Created IAM Role with ARN: {role_arn}")
except iam_client.exceptions.EntityAlreadyExistsException:
    role_arn = iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]["Arn"]
    print(f"Using existing IAM Role with ARN: {role_arn}")

# Step 2: Create Lambda Function Code
lambda_code = f"""
import boto3
import os

ec2_client = boto3.client('ec2', region_name='us-east-1')

def lambda_handler(event, context):
    print("Received Spot Instance termination warning:", event)
    
    response = ec2_client.request_spot_instances(
        InstanceCount=1,
        LaunchSpecification={{
            "ImageId": "{AMI_ID}",
            "InstanceType": "{INSTANCE_TYPE}",
            "KeyName": "{KEY_NAME}",
            "SubnetId": "{SUBNET_ID}",
            "SecurityGroupIds": {SECURITY_GROUP_IDS},
        }},
        SpotPrice="{SPOT_PRICE}",
        Type="one-time",
    )
    
    spot_request_id = response["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
    print(f"Requested new Spot Instance with Request ID: {{spot_request_id}}")
    
    return {{"statusCode": 200, "body": f"New spot instance requested with Request ID: {{spot_request_id}}"}}    
"""

# Save Lambda function to a zip file
with open("lambda_function.py", "w") as f:
    f.write(lambda_code)
with zipfile.ZipFile(ZIP_FILE_NAME, "w") as zipf:
    zipf.write("lambda_function.py")

# Step 3: Create Lambda Function
with open(ZIP_FILE_NAME, "rb") as f:
    lambda_code_bytes = f.read()

try:
    lambda_response = lambda_client.create_function(
        FunctionName=LAMBDA_FUNCTION_NAME,
        Runtime="python3.8",
        Role=role_arn,
        Handler="lambda_function.lambda_handler",
        Code={"ZipFile": lambda_code_bytes},
        Timeout=30,
    )
    lambda_arn = lambda_response["FunctionArn"]
    print(f"Created Lambda function with ARN: {lambda_arn}")
except lambda_client.exceptions.ResourceConflictException:
    lambda_arn = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)["Configuration"]["FunctionArn"]
    print(f"Using existing Lambda function with ARN: {lambda_arn}")

# Step 4: Create EventBridge Rule
event_pattern = {
    "source": ["aws.ec2"],
    "detail-type": ["EC2 Spot Instance Interruption Warning"]
}

rule_response = eventbridge_client.put_rule(
    Name=EVENT_RULE_NAME,
    EventPattern=json.dumps(event_pattern),
    State="ENABLED",
)

rule_arn = rule_response["RuleArn"]
print(f"Created EventBridge rule with ARN: {rule_arn}")

# Step 5: Add Lambda as Target to EventBridge Rule
eventbridge_client.put_targets(
    Rule=EVENT_RULE_NAME,
    Targets=[{"Id": LAMBDA_FUNCTION_NAME, "Arn": lambda_arn}]
)

# Step 6: Grant EventBridge Permission to Trigger Lambda
lambda_client.add_permission(
    FunctionName=LAMBDA_FUNCTION_NAME,
    StatementId="SpotTerminationPermission",
    Action="lambda:InvokeFunction",
    Principal="events.amazonaws.com",
    SourceArn=rule_arn,
)

print("Setup complete. EventBridge rule will trigger Lambda on Spot Instance termination warnings.")
