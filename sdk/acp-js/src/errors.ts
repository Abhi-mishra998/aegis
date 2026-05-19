export class ACPError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ACPError";
  }
}

export class DeniedError extends ACPError {
  reason: string;
  detail: string;
  decisionId?: string;

  constructor(reason: string, detail: string, decisionId?: string) {
    super(`${reason}: ${detail}`);
    this.name = "DeniedError";
    this.reason = reason;
    this.detail = detail;
    this.decisionId = decisionId;
  }
}

export class RateLimitedError extends ACPError {
  retryAfter?: number;
  constructor(retryAfter?: number) {
    super(retryAfter ? `rate limited; retry after ${retryAfter}s` : "rate limited");
    this.name = "RateLimitedError";
    this.retryAfter = retryAfter;
  }
}

export class PolicyError extends ACPError {
  constructor(message: string) {
    super(message);
    this.name = "PolicyError";
  }
}
