#!/usr/bin/env bash
set -euo pipefail

FUNCTIONS_DIR="src/functions"
DIST_DIR="dist"
FUNCTION="${1:-}"

build_function() {
  local func_name="$1"
  local func_dir="${FUNCTIONS_DIR}/${func_name}"
  local out_dir="${DIST_DIR}/${func_name}"

  if [ ! -d "$func_dir" ]; then
    echo "ERROR: Function directory '${func_dir}' not found." >&2
    exit 1
  fi

  echo "Bundling function: ${func_name}..."
  npx esbuild "${func_dir}/index.mjs" \
    --bundle \
    --platform=node \
    --target=node20 \
    --format=esm \
    --outdir="${out_dir}" \
    --out-extension:.js=.mjs \
    --external:@aws-sdk/* \
    --tree-shaking=true

  if [ ! -f "${out_dir}/index.mjs" ]; then
    echo "ERROR: ${out_dir}/index.mjs not created." >&2
    exit 1
  fi

  echo "Zipping function: ${func_name}..."
  (cd "${DIST_DIR}" && zip -r "../${func_name}.zip" "${func_name}/")

  if [ ! -f "${func_name}.zip" ]; then
    echo "ERROR: ${func_name}.zip not created." >&2
    exit 1
  fi

  echo "Built: ${func_name}.zip"
}

# Install all dependencies (esbuild is a devDependency)
npm ci

if [ -n "$FUNCTION" ]; then
  # Build a single function
  build_function "$FUNCTION"
else
  # Build all functions
  echo "Building all functions..."
  for func_dir in "${FUNCTIONS_DIR}"/*/; do
    func_name=$(basename "$func_dir")
    build_function "$func_name"
  done
fi

echo "Build complete."
