#!/usr/bin/env bash
#
# Launch the cheapest always-on EC2 for LoopifyBot: a t4g.micro (ARM Graviton).
# Creates a locked-down security group (SSH from your IP only) and the instance.
#
# Prereqs: AWS CLI configured, an existing EC2 key pair whose .pem you hold.
#
set -euo pipefail

# ── Config (override via environment) ─────────────────────────────────
REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.micro}"           # ARM, free-tier eligible
AMI_ID="${AMI_ID:-ami-02c4144237becae44}"             # Ubuntu 24.04 arm64 (us-east-1)
KEY_NAME="${KEY_NAME:-ils-acc-examplekey-us-east-1}"  # existing key pair
SG_NAME="${SG_NAME:-loopify-bot-sg}"
NAME_TAG="${NAME_TAG:-loopify-bot}"
VOLUME_GB="${VOLUME_GB:-8}"

# Restrict SSH to your current public IP.
MY_IP="$(curl -s https://checkip.amazonaws.com)"
echo "==> SSH will be restricted to ${MY_IP}/32"

# ── Security group ────────────────────────────────────────────────────
VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" \
    --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"

SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)"

if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then
    echo "==> Creating security group $SG_NAME..."
    SG_ID="$(aws ec2 create-security-group --region "$REGION" \
        --group-name "$SG_NAME" \
        --description "LoopifyBot: SSH in, all out" \
        --vpc-id "$VPC_ID" --query 'GroupId' --output text)"
    aws ec2 authorize-security-group-ingress --region "$REGION" \
        --group-id "$SG_ID" --protocol tcp --port 22 --cidr "${MY_IP}/32" >/dev/null
fi
echo "==> Security group: $SG_ID"

# ── Launch ────────────────────────────────────────────────────────────
echo "==> Launching $INSTANCE_TYPE ($AMI_ID)..."
INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$VOLUME_GB,VolumeType=gp3}" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME_TAG}]" \
    --metadata-options "HttpTokens=required" \
    --query 'Instances[0].InstanceId' --output text)"

echo "==> Instance: $INSTANCE_ID — waiting for it to run..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP="$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"

echo ""
echo "==> Ready."
echo "    Instance : $INSTANCE_ID"
echo "    Public IP: $PUBLIC_IP"
echo "    SSH      : ssh -i <key>.pem ubuntu@$PUBLIC_IP"
