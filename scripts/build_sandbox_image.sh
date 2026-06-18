#!/usr/bin/env bash
# build_sandbox_image.sh — Build OCI-compatible sandbox image

set -euo pipefail

IMAGE_NAME="prismatic-sandbox"
TAG="latest"

echo "Building OCI-compatible sandbox image: ${IMAGE_NAME}:${TAG}..."
docker build -f Dockerfile.sandbox -t "${IMAGE_NAME}:${TAG}" .

echo "Successfully built OCI sandbox image: ${IMAGE_NAME}:${TAG}"
