# AI Foundry Registry Backend

Backend API for the AI Foundry Registry platform. Contains Lambda function source code (Node.js 20.x) that is bundled with esbuild and deployed to AWS Lambda.

## Project Structure

```
ai-foundry-registry-backend/
├── src/
│   └── functions/
│       └── api/
│           └── index.mjs    # API Lambda handler
├── dist/                    # esbuild output (gitignored)
├── scripts/
│   ├── build.sh             # Bundles Lambda function(s) with esbuild, then zips
│   └── deploy.sh            # Uploads zip(s) to S3 + updates Lambda function(s)
├── package.json
└── README.md
```

## Prerequisites

- Node.js 20.x
- AWS CLI configured with appropriate credentials
- `zip` utility available on PATH

## Scripts

### `scripts/build.sh [function-name]`

Bundles Lambda function(s) with esbuild and produces zip artifacts. Runs `npm ci` to install dependencies, then uses esbuild to bundle each function into a self-contained ESM file with tree-shaking and AWS SDK externalized. The bundled output is zipped for deployment.

- If `function-name` is provided, builds only that function (e.g., `scripts/build.sh api`).
- If omitted, builds all functions discovered under `src/functions/`.
- No environment argument required.
- Produces `<function-name>.zip` in the project root for each built function.

### `scripts/deploy.sh <environment> [function-name]`

Uploads zip artifact(s) to the S3 Lambda Zip Bucket and updates Lambda function code in the target environment.

- `environment` (required): Target deployment environment. Must be one of `dev`, `test`, `stage`, or `prod`.
- `function-name` (optional): If provided, deploys only that function. If omitted, deploys all functions.
- Requires the corresponding zip artifact to exist (run `scripts/build.sh` first).
- Requires AWS CLI configured with permissions to write to S3 and update Lambda functions.
