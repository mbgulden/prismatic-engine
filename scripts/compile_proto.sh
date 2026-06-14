#!/usr/bin/env bash
# ── Protobuf Compilation Script ───────────────────────────
# Compiles gateway.proto into Python gRPC stubs.
# Run from the project root: ./scripts/compile_proto.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROTO_DIR="$PROJECT_ROOT/prismatic/gateway/proto"
OUT_DIR="$PROJECT_ROOT/prismatic/gateway/proto_out"

mkdir -p "$OUT_DIR"
touch "$OUT_DIR/__init__.py"

python3 -m grpc_tools.protoc \
    --proto_path="$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    "$PROTO_DIR/prismatic/v1/gateway.proto"

# Fix relative imports — protoc 6.x generates `from . import descriptor_as_X`
# which works when installed as google.protobuf but not in our proto_out package.
# Rewrite to use google.protobuf absolute imports.
sed -i \
  -e 's/^from \. import descriptor as _descriptor/from google.protobuf import descriptor as _descriptor/' \
  -e 's/^from \. import descriptor_pool as _descriptor_pool/from google.protobuf import descriptor_pool as _descriptor_pool/' \
  -e 's/^from \. import runtime_version as _runtime_version/from google.protobuf import runtime_version as _runtime_version/' \
  -e 's/^from \. import symbol_database as _symbol_database/from google.protobuf import symbol_database as _symbol_database/' \
  -e 's/^from \. import builder as _builder/from google.protobuf.internal import builder as _builder/' \
  "$OUT_DIR/gateway_pb2.py"

echo "✅ Protobuf compiled to $OUT_DIR"
