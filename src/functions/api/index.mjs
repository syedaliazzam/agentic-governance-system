const VALID_ENVS = ['dev', 'test', 'stage', 'prod'];
const env = process.env.ENV;

if (!env || !VALID_ENVS.includes(env)) {
  throw new Error(`Invalid or missing ENV: "${env}". Must be one of: ${VALID_ENVS.join(', ')}`);
}

export const handler = async (event) => {
  try {
    return { statusCode: 200, body: JSON.stringify({ status: 'ok' }) };
  } catch (error) {
    return { statusCode: 500, body: JSON.stringify({ status: 'error' }) };
  }
};
