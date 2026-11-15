import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUuid, requestId } from '../src/ids.ts';

test('request IDs keep prefixes and work without crypto.randomUUID', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  try {
    let seed = 0;
    Object.defineProperty(globalThis, 'crypto', {
      configurable: true,
      value: {
        getRandomValues(bytes) {
          for (let index = 0; index < bytes.length; index += 1) bytes[index] = ++seed;
          return bytes;
        },
      },
    });
    const first = randomUuid();
    const second = randomUuid();
    assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.notEqual(first, second);
    assert.equal(requestId('admin', '_').startsWith('admin_'), true);
    assert.equal(requestId('fulfillment').startsWith('fulfillment-'), true);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor);
    else delete globalThis.crypto;
  }
});
