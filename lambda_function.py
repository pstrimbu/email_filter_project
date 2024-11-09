
import boto3
import os

ec2_client = boto3.client('ec2', region_name='us-east-1')

def lambda_handler(event, context):
    print("Received Spot Instance termination warning:", event)
    
    response = ec2_client.request_spot_instances(
        InstanceCount=1,
        LaunchSpecification={
            "ImageId": "ami-0b3436f14413b755b",
            "InstanceType": "g5.xlarge",
            "KeyName": "/Users/peterstrimbu/dev/ssh/keys/timamap.pem",
            "SubnetId": "subnet-09f6d9ea2063e4784",
            "SecurityGroupIds": ['sg-07a3d8b246cafe58b', 'sg-0ac0b571e88605a5f'],
        },
        SpotPrice="0.50",
        Type="one-time",
    )
    
    spot_request_id = response["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
    print(f"Requested new Spot Instance with Request ID: {spot_request_id}")
    
    return {"statusCode": 200, "body": f"New spot instance requested with Request ID: {spot_request_id}"}    
