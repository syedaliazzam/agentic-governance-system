/**
 * Feature: basic-architecture-setup, Property 2: Lambda handler returns OK for all valid requests
 *
 * **Validates: Requirements 5.1**
 *
 * For any API Gateway proxy event with any HTTP method, any path, any headers,
 * and any body content, the Lambda handler SHALL return a response with
 * statusCode 200 and body '{"status":"ok"}'.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import fc from 'fast-check';

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'];

/**
 * Generator for random API Gateway proxy events.
 * Produces events with random HTTP methods, paths, headers, and bodies.
 */
const arbApiGatewayEvent = fc.record({
  httpMethod: fc.constantFrom(...HTTP_METHODS),
  path: fc.stringMatching(/^\/[a-zA-Z0-9/_-]{0,50}$/),
  headers: fc.dictionary(
    fc.string({ minLength: 1, maxLength: 30 }).filter((s) => /^[a-zA-Z0-9-]+$/.test(s)),
    fc.string({ maxLength: 200 })
  ),
  queryStringParameters: fc.oneof(
    fc.constant(null),
    fc.dictionary(
      fc.string({ minLength: 1, maxLength: 20 }).filter((s) => /^[a-zA-Z0-9_]+$/.test(s)),
      fc.string({ maxLength: 100 })
    )
  ),
  body: fc.oneof(fc.constant(null), fc.string({ maxLength: 1000 }), fc.json()),
});

describe('Property 2: Lambda handler returns OK for all valid requests', () => {
  let handler;

  beforeAll(async () => {
    // Set ENV before dynamically importing the module to pass top-level validation
    process.env.ENV = 'dev';
    const mod = await import('./index.mjs');
    handler = mod.handler;
  });

  it('should always return statusCode 200 and body {"status":"ok"} for any valid API Gateway event', async () => {
    await fc.assert(
      fc.asyncProperty(arbApiGatewayEvent, async (event) => {
        const result = await handler(event);

        expect(result.statusCode).toBe(200);
        expect(result.body).toBe('{"status":"ok"}');
      }),
      { numRuns: 100 }
    );
  });
});
