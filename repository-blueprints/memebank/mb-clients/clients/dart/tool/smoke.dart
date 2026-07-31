import 'package:mb_clients/mb_clients.dart';

void main() {
  final ids = operations.map((operation) => operation.operationId).toList();
  const expected = <String>[
    'createImport',
    'getAsset',
    'searchAssets',
    'createClipboardManifest',
    'listSyncEvents',
  ];
  if (ids.length != expected.length ||
      !ids.asMap().entries.every((entry) => entry.value == expected[entry.key])) {
    throw StateError('generated operation catalog mismatch: $ids');
  }
  if (!shouldRetry(
    retryClass: RetryClass.safeRead,
    status: 503,
    attempt: 1,
    bodyStarted: false,
  )) {
    throw StateError('safe transient failure should be retryable');
  }
  if (shouldRetry(
    retryClass: RetryClass.safeRead,
    status: 503,
    attempt: 3,
    bodyStarted: false,
  )) {
    throw StateError('retry budget must be bounded');
  }
  final headers = redactHeaders(<String, String>{
    'Authorization': 'Bearer secret',
    'X-Request-Id': 'req-1',
  });
  if (headers['Authorization'] != redacted || headers['X-Request-Id'] != 'req-1') {
    throw StateError('header redaction mismatch: $headers');
  }
}
