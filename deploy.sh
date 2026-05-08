#!/bin/bash
# infrastructure/deploy.sh
# Package and deploy the Market Pulse pipeline to AWS.
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - An S3 bucket named: {ACCOUNT_ID}-market-pulse-deploy
#   - .env file with all required vars OR env vars exported
#
# Usage:
#   chmod +x infrastructure/deploy.sh
#   ./infrastructure/deploy.sh

set -e

# ── Config ──────────────────────────────────────────────────────────────────
STACK_NAME="market-pulse"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
DEPLOY_BUCKET="${ACCOUNT_ID}-market-pulse-deploy"

# Load .env if present
if [ -f .env ]; then
  echo "Loading .env..."
  export $(grep -v '^#' .env | xargs)
fi

# Verify required env vars
for var in ALPHA_VANTAGE_API_KEY NEWS_API_KEY ANTHROPIC_API_KEY DISCORD_WEBHOOK_URL; do
  if [ -z "${!var}" ]; then
    echo "ERROR: $var is not set"
    exit 1
  fi
done

echo "─────────────────────────────────────────────"
echo "  Market Pulse Deployment"
echo "  Stack:  $STACK_NAME"
echo "  Region: $REGION"
echo "  Bucket: $DEPLOY_BUCKET"
echo "─────────────────────────────────────────────"

# ── 1. Create deployment bucket if needed ────────────────────────────────────
echo ""
echo "[1/4] Ensuring deployment S3 bucket exists..."
aws s3 mb "s3://${DEPLOY_BUCKET}" --region "$REGION" 2>/dev/null || true

# ── 2. Install dependencies and package ─────────────────────────────────────
echo ""
echo "[2/4] Installing Python dependencies..."
pip install \
  yfinance \
  pandas \
  requests \
  anthropic \
  boto3 \
  --target ./package \
  --quiet

echo "Packaging Lambda zip..."
cd package && zip -r ../market-pulse.zip . -x "*.pyc" -x "__pycache__/*" > /dev/null && cd ..

# Add our own code (exclude dev files)
zip -r market-pulse.zip shared/ lambdas/ \
  -x "*.pyc" -x "__pycache__/*" \
  -x "tests/*" -x "*.test.py" \
  > /dev/null

ZIP_SIZE=$(du -sh market-pulse.zip | cut -f1)
echo "Package size: $ZIP_SIZE"

# ── 3. Upload to S3 ──────────────────────────────────────────────────────────
echo ""
echo "[3/4] Uploading package to S3..."
aws s3 cp market-pulse.zip "s3://${DEPLOY_BUCKET}/market-pulse.zip" --region "$REGION"

# ── 4. Deploy CloudFormation stack ───────────────────────────────────────────
echo ""
echo "[4/4] Deploying CloudFormation stack..."
aws cloudformation deploy \
  --template-file infrastructure/cloudformation.yaml \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    AlphaVantageApiKey="$ALPHA_VANTAGE_API_KEY" \
    NewsApiKey="$NEWS_API_KEY" \
    AnthropicApiKey="$ANTHROPIC_API_KEY" \
    DiscordWebhookUrl="$DISCORD_WEBHOOK_URL"

echo ""
echo "✅ Deployment complete!"
echo ""

# Show outputs
echo "Stack outputs:"
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table

echo ""
echo "To trigger a manual run:"
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" \
  --output text)
echo "  aws stepfunctions start-execution --state-machine-arn $STATE_MACHINE_ARN --input '{}'"
echo ""
echo "To subscribe to failure alerts:"
ALERTS_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AlertsTopicArn'].OutputValue" \
  --output text)
echo "  aws sns subscribe --topic-arn $ALERTS_ARN --protocol email --notification-endpoint your@email.com"

# Cleanup
rm -f market-pulse.zip
rm -rf package/
