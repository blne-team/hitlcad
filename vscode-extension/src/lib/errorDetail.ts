/**
 * The reason a hitlcad server request failed, from the response's body: FastAPI's `detail` (a message, or
 * validation errors shown as JSON), else the body itself, else the status text.
 */
export function errorDetail(body: string, statusText: string): string {
  try {
    const { detail } = JSON.parse(body) as { detail?: unknown };
    if (typeof detail === "string") return detail;
    if (detail) return JSON.stringify(detail);
  } catch {
    // not JSON: the body is the reason
  }
  return body || statusText;
}
