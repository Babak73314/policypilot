/**
 * Wire types for the PolicyPilot API, written from the FastAPI handlers in `apps/api` (camelCase
 * on the wire). Times are ISO 8601 UTC strings; money is minor units plus an ISO 4217 code.
 * Bodies are not validated at runtime: the API is the source of truth (docs/design/api.md).
 */

// ─── shared ────────────────────────────────────────────────────────────────────

export interface Money {
  amountMinor: number;
  currency: string;
}

export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  code?: string;
}

export interface Page<T> {
  items: T[];
  nextCursor?: string | null;
}

export interface Citation {
  chunkId: number;
  documentTitle: string;
  section: string;
  pages: string;
}

export interface QuoteArtifact {
  type: "quote";
  quoteId: string;
  productId: string;
  productName: string;
  carrierName: string;
  premium: Money;
  billingPeriod: string;
  monthlyEquivalent: Money | null;
  variants: { key: string; label: string; premium: Money | null }[];
  breakdown: { label: string; amount: Money | null }[];
  disclaimer: string;
  expiresAt: string;
  overBudget: boolean;
  /** The server-rendered price line, e.g. "$73.00 per month". */
  rendered: string;
}

export interface ComparisonArtifact {
  type: "comparison";
  productIds: string[];
  columns: { productId: string; productName: string; carrierName: string }[];
  rows: { attribute: string; label: string; values: (string | null)[] }[];
}

export type Artifact = QuoteArtifact | ComparisonArtifact;

/** A quick reply offered with a message; sent back as the turn's `action`. */
export interface MessageAction {
  id: string;
  label: string;
}

export type MessageRole = "customer" | "assistant" | "agent" | "system";

export interface ChatMessage {
  id: number;
  role: MessageRole;
  content: string;
  createdAt: string;
  citations: Citation[];
  artifacts: Artifact[];
  actions: MessageAction[];
}

export type ConversationStatus = "active" | "awaiting_review" | "human_takeover" | "closed";

// ─── customer chat ─────────────────────────────────────────────────────────────

export interface ChatSession {
  conversationId: string;
  token: string;
  locale: string;
  disclosure: string;
  privacyUrl: string;
  demoMode: boolean;
}

export interface Transcript {
  status: ConversationStatus;
  messages: ChatMessage[];
  nextAfter: number | null;
  /** A full page was returned: read on from `nextAfter`. */
  hasMore?: boolean;
}

export interface MessageCompleted {
  messageId: number | null;
  text: string;
  citations: Citation[];
  quoteIds: string[];
  actions: MessageAction[];
  conversationStatus: ConversationStatus;
  token: string;
  replayed?: boolean;
}

export interface TurnError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface PublicProduct {
  id: string;
  code: string;
  line: "life" | "health" | "auto";
  carrier: { code: string; name: string };
  name: string;
  summary: string;
  highlights: string[];
  attributes: Record<string, unknown>;
  currency: string;
  billingPeriod: string;
}

// ─── WhatsApp simulator (local only) ───────────────────────────────────────────

export type SimulatorPayload =
  | { type: "text"; text: string }
  | { type: "interactive"; body: string; buttons: { id: string; title: string }[] }
  | { type: "template"; name: string; language: string; params: string[]; body?: string };

export interface SimulatorItem {
  id: number;
  payload: SimulatorPayload;
  createdAt: string;
}

// ─── agent dashboard ───────────────────────────────────────────────────────────

export interface AgentProfile {
  id: string;
  email: string;
  displayName: string;
  role: string;
}

export interface LoginResponse {
  accessToken: string;
  expiresAt: string;
  agent: AgentProfile;
}

export type ReviewKind = "application_review" | "handoff";
export type ReviewStatus = "open" | "claimed" | "resolving" | "resolved";

export interface ReviewTaskSummary {
  id: string;
  kind: ReviewKind;
  status: ReviewStatus;
  priority: number;
  summary: string | null;
  conversationId: string;
  channel: "web" | "whatsapp";
  applicationId: string | null;
  createdAt: string;
  claimedBy: { id: string; displayName: string } | null;
}

export interface ReviewTaskDetail extends ReviewTaskSummary {
  snapshot: {
    version: number;
    data: Record<string, unknown>;
    needsProfile: Record<string, unknown>;
    quote: QuoteArtifact | null;
  } | null;
  messagesSinceOpened: ChatMessage[];
  decision: Record<string, unknown> | null;
  resolution: string | null;
  resolvedAt: string | null;
  whatsappWindowOpen: boolean | null;
}

export type ReviewAction =
  "approve" | "request_changes" | "reject" | "edit_and_approve" | "return_to_assistant" | "close";

export interface ResolveResponse {
  task: ReviewTaskDetail;
  customerMessage: { id?: number; content?: string } | null;
}

export interface ConversationSummary {
  id: string;
  channel: "web" | "whatsapp";
  status: ConversationStatus;
  customerDisplayName: string | null;
  lastActivityAt: string;
  createdAt: string;
  messageCount: number;
  locale: string;
}

export interface RunSummary {
  id: string;
  route: string | null;
  status: string;
  startedAt: string;
  durationMs: number | null;
  models: string[];
  fallbackUsed: boolean;
  inputTokens: number;
  outputTokens: number;
  costEstimateMicros: number;
  guardrailActions: string[];
}

export interface RunStep {
  seq: number;
  kind: string;
  name: string;
  startedAt: string;
  durationMs: number;
  model: string | null;
  provider: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  summary: Record<string, unknown>;
}

export interface RunDetail extends RunSummary {
  steps: RunStep[];
}

export interface ConversationDetail {
  conversation: ConversationSummary;
  messages: ChatMessage[];
  profile: Record<string, unknown>;
  quotes: QuoteArtifact[];
  applications: { id: string; status: string; productName: string; createdAt: string }[];
  runs: RunSummary[];
}

export type LeadStatus = "new" | "contacted" | "qualified" | "won" | "lost";

export interface Lead {
  id: string;
  kind: string;
  status: LeadStatus;
  lineInterest: string[];
  source: string;
  customerDisplayName: string | null;
  assignedAgent: { id: string; displayName: string } | null;
  nextFollowUpAt: string | null;
  createdAt: string;
  contact: { email: string | null; phone: string | null };
  consent: { at: string | null; source: string | null; textVersion: string | null };
  conversationId: string | null;
}

export interface CatalogProduct {
  id: string;
  code: string;
  line: "life" | "health" | "auto";
  carrier: { code: string; name: string };
  name: string;
  summary: string;
  attributes: Record<string, unknown>;
  currency: string;
  billingPeriod: string;
  active: boolean;
}

export interface RateTable {
  id: string;
  version: string;
  effectiveFrom: string;
  effectiveTo: string | null;
  publishedAt: string | null;
  factors: Record<string, unknown>;
}

export interface MetricsSummary {
  openReviews: number;
  avgReviewMinutes: number;
  leadsToday: number;
  applicationsByStatus: Record<string, number>;
  runsToday: number;
  errorRate: number;
  fallbackRate: number;
  guardrailActions: Record<string, number>;
  modelCallsToday: number;
  modelCallLimit: number;
  whatsappOutboundThisMonth: number;
  deadLetteredEvents: number;
}

export interface OutboxItem {
  id: string;
  eventType: string;
  attempts: number;
  lastError: string | null;
  createdAt: string;
}
