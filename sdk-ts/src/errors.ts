/** Error thrown when the AgentGate API returns an HTTP error (status >= 400). */
export class AgentGateError extends Error {
  public readonly statusCode: number;
  public readonly detail: string;

  constructor(statusCode: number, detail: string) {
    super(`HTTP ${statusCode}: ${detail}`);
    this.name = "AgentGateError";
    this.statusCode = statusCode;
    this.detail = detail;
  }
}
