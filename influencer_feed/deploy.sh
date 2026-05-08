#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Personal default profile (override by exporting AWS_PROFILE before running).
export AWS_PROFILE="${AWS_PROFILE:-mastalcup}"
echo "AWS_PROFILE=${AWS_PROFILE}"

STACK_NAME="${STACK_NAME:-influencer-feed}"

command -v sam >/dev/null 2>&1 || {
  echo "Install AWS SAM CLI: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html" >&2
  exit 1
}

VENDOR_DIR="lambdas/ingest_source/vendor"
rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"
python -m pip install youtube-transcript-api --target "$VENDOR_DIR" --upgrade --quiet

sam build

if [[ ! -f samconfig.toml ]]; then
  echo "First deploy: sam deploy --guided"
  sam deploy --guided
else
  sam deploy --no-confirm-changeset
fi

echo ""
echo "Stack outputs:"
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" \
  --output table
