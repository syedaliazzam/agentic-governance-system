import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fc from 'fast-check';

/**
 * Feature: basic-architecture-setup, Property 1: Environment validation rejects invalid values
 *
 * For any string that is not exactly one of `dev`, `test`, `stage`, or `prod`,
 * the backend environment validation function SHALL reject it and signal an error.
 * Conversely, for any of the four valid values, validation SHALL succeed.
 *
 * Validates: Requirements 1.4
 */

const VALID_ENVS = ['dev', 'test', 'stage', 'prod'];

describe('Property 1: Environment validation rejects invalid values', () => {
  const originalEnv = process.env.ENV;

  afterEach(() => {
    // Restore original ENV
    if (originalEnv !== undefined) {
      process.env.ENV = originalEnv;
    } else {
      delete process.env.ENV;
    }
    vi.resetModules();
  });

  it('should accept all valid environment values', async () => {
    for (const validEnv of VALID_ENVS) {
      vi.resetModules();
      process.env.ENV = validEnv;
      const module = await import('./index.mjs');
      expect(module.handler).toBeDefined();
    }
  });

  it('should reject any string that is not a valid environment value', async () => {
    /**
     * Validates: Requirements 1.4
     */
    await fc.assert(
      fc.asyncProperty(
        fc.string().filter((s) => !VALID_ENVS.includes(s)),
        async (invalidEnv) => {
          vi.resetModules();
          process.env.ENV = invalidEnv;
          await expect(import('./index.mjs')).rejects.toThrow(
            /Invalid or missing ENV/
          );
        }
      ),
      { numRuns: 100 }
    );
  });

  it('should reject when ENV is missing (undefined)', async () => {
    vi.resetModules();
    delete process.env.ENV;
    await expect(import('./index.mjs')).rejects.toThrow(
      /Invalid or missing ENV/
    );
  });

  it('should reject empty string', async () => {
    vi.resetModules();
    process.env.ENV = '';
    await expect(import('./index.mjs')).rejects.toThrow(
      /Invalid or missing ENV/
    );
  });

  it('should reject near-miss values (case variations, whitespace, extra chars)', async () => {
    /**
     * Validates: Requirements 1.4
     */
    const nearMissArb = fc.oneof(
      // Uppercase variations
      fc.constantFrom('Dev', 'DEV', 'Test', 'TEST', 'Stage', 'STAGE', 'Prod', 'PROD'),
      // With leading/trailing whitespace
      fc.constantFrom(' dev', 'dev ', ' test ', 'stage\t', '\nprod'),
      // With extra characters
      fc.constantFrom('dev1', 'testing', 'staging', 'production', 'development'),
      // Common misspellings
      fc.constantFrom('dv', 'tst', 'stg', 'prd', 'devv', 'prodd')
    );

    await fc.assert(
      fc.asyncProperty(nearMissArb, async (nearMiss) => {
        vi.resetModules();
        process.env.ENV = nearMiss;
        await expect(import('./index.mjs')).rejects.toThrow(
          /Invalid or missing ENV/
        );
      }),
      { numRuns: 100 }
    );
  });

  it('should accept valid envs generated from constantFrom', async () => {
    /**
     * Validates: Requirements 1.4
     */
    await fc.assert(
      fc.asyncProperty(fc.constantFrom(...VALID_ENVS), async (validEnv) => {
        vi.resetModules();
        process.env.ENV = validEnv;
        const module = await import('./index.mjs');
        expect(module.handler).toBeDefined();
        expect(typeof module.handler).toBe('function');
      }),
      { numRuns: 100 }
    );
  });
});
