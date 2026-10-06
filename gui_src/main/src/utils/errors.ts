/** Error message extraction for API calls. */

interface ServerError {
  response?: { data?: { error?: string } };
}

/** Prefer the server's `{ error }` payload, then the JS message, then a fallback. */
export function errorMessage(e: unknown, fallback = 'Unexpected error'): string {
  const server = (e as ServerError)?.response?.data?.error;
  if (server) return server;
  if (e instanceof Error && e.message) return e.message;
  return typeof e === 'string' && e ? e : fallback;
}
