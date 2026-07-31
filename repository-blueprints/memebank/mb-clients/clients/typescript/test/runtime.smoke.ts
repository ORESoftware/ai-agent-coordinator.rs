import assert from 'node:assert/strict';

import { operations } from '../src/generated.ts';
import { redactHeaders, shouldRetry } from '../src/runtime.ts';

assert.deepEqual(
  operations.map((operation) => operation.operationId),
  ['createImport', 'getAsset', 'searchAssets', 'createClipboardManifest', 'listSyncEvents'],
);
assert.equal(
  shouldRetry({ retryClass: 'safe_read', status: 503, attempt: 1, bodyStarted: false }),
  true,
);
assert.equal(
  shouldRetry({ retryClass: 'safe_read', status: 503, attempt: 3, bodyStarted: false }),
  false,
);
assert.equal(
  shouldRetry({ retryClass: 'safe_read', status: 503, attempt: 1, bodyStarted: true }),
  false,
);
assert.deepEqual(
  redactHeaders({ Authorization: 'Bearer secret', 'X-Request-Id': 'req-1' }),
  { Authorization: '[REDACTED]', 'X-Request-Id': 'req-1' },
);
