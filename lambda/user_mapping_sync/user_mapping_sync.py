import boto3
import csv
import io
import os

IDENTITY_STORE_ID = os.environ['IDENTITY_STORE_ID']
IDENTITY_STORE_REGION = os.environ['IDENTITY_STORE_REGION']
S3_BUCKET = os.environ['S3_BUCKET']
S3_KEY = os.environ['S3_KEY']
DIRECTORY_ID = IDENTITY_STORE_ID  # e.g. d-xxxxxxxxxx

def lambda_handler(event, context):
    identity = boto3.client('identitystore', region_name=IDENTITY_STORE_REGION)
    s3 = boto3.client('s3')

    # Paginate through all users
    users = []
    paginator = identity.get_paginator('list_users')
    for page in paginator.paginate(IdentityStoreId=IDENTITY_STORE_ID):
        for user in page['Users']:
            kiro_user_id = f"{DIRECTORY_ID}.{user['UserId']}"
            email = ''
            if user.get('Emails'):
                email = user['Emails'][0].get('Value', '')
            users.append({
                'kiro_userid': kiro_user_id,
                'username': user.get('UserName', ''),
                'display_name': user.get('DisplayName', ''),
                'email': email
            })

    # Write CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['kiro_userid', 'username', 'display_name', 'email'])
    writer.writeheader()
    writer.writerows(users)

    # Upload to S3
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=output.getvalue(),
        ContentType='text/csv'
    )

    return {'statusCode': 200, 'users_synced': len(users)}
