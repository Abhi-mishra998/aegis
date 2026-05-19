export { Client } from "./client.js";
export type { ClientOptions, ExecuteResult, ReplayResult } from "./client.js";
export { ACPError, DeniedError, RateLimitedError, PolicyError } from "./errors.js";
export {
  validatePolicy,
  loadPolicy,
  type Policy,
  type Rule,
  type Autonomy,
} from "./policy.js";
export {
  verifyReceipt,
  canonicalJson,
  fingerprintPublicKey,
  type SignedReceiptPayload,
} from "./receipts.js";
export {
  verifyInclusion,
  leafHashForReceipt,
  type InclusionProof,
  type MerkleSibling,
} from "./transparency.js";
export {
  buildArchive,
  ArchiveError,
  type BuildArchiveOptions,
  type ArchiveCounts,
} from "./archive.js";
export { initProject, type InitOptions, type InitResult } from "./init.js";
export const VERSION = "0.2.0";
