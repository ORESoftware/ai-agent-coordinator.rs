import 'generated.dart';

const defaultTimeout = Duration(seconds: 30);
const maxAttempts = 3;
const redacted = '[REDACTED]';

abstract interface class AccessTokenProvider {
  Future<String> accessToken();
  Future<String> refreshAccessToken();
}

abstract interface class ClientTransport {
  Future<TransportResponse> execute(RequestPlan request);
}

final class TransportResponse {
  const TransportResponse({required this.statusCode, required this.bodyStarted});

  final int statusCode;
  final bool bodyStarted;
}

final class RequestPlan {
  const RequestPlan({
    required this.operation,
    required this.path,
    required this.headers,
    this.body,
    this.timeout = defaultTimeout,
  });

  final ClientOperation operation;
  final String path;
  final Map<String, String> headers;
  final Stream<List<int>>? body;
  final Duration timeout;
}

bool shouldRetry({
  required RetryClass retryClass,
  required int status,
  required int attempt,
  required bool bodyStarted,
}) {
  if (retryClass == RetryClass.never || attempt >= maxAttempts || bodyStarted) {
    return false;
  }
  return const {408, 425, 429, 500, 502, 503, 504}.contains(status);
}

Map<String, String> redactHeaders(Map<String, String> headers) {
  const names = {
    'authorization',
    'cookie',
    'set-cookie',
    'x-api-key',
    'x-provider-token',
  };
  return {
    for (final entry in headers.entries)
      entry.key: names.contains(entry.key.toLowerCase()) ? redacted : entry.value,
  };
}
