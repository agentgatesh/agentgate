import { AgentGateError } from "./errors.js";

export interface ClientOptions {
  serverUrl?: string;
  apiKey?: string;
  timeout?: number;
}

/**
 * TypeScript client for the AgentGate API.
 *
 * @example
 * ```ts
 * const client = new AgentGateClient({ apiKey: "your-key" });
 * const agents = await client.listAgents();
 * const result = await client.sendTask("agent-id", "Hello!");
 * ```
 */
export class AgentGateClient {
  private readonly serverUrl: string;
  private readonly apiKey: string | undefined;
  private readonly timeout: number;

  constructor(options: ClientOptions = {}) {
    this.serverUrl = (options.serverUrl ?? "https://agentgate.sh").replace(
      /\/$/,
      "",
    );
    this.apiKey = options.apiKey;
    this.timeout = options.timeout ?? 30_000;
  }

  // ── Internals ──

  private headers(auth: boolean = false): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (auth && this.apiKey) {
      h["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return h;
  }

  private async request<T = unknown>(
    method: string,
    path: string,
    options: {
      params?: Record<string, string | number>;
      body?: unknown;
      auth?: boolean;
      customHeaders?: Record<string, string>;
    } = {},
  ): Promise<T> {
    let url = `${this.serverUrl}${path}`;

    if (options.params) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(options.params)) {
        if (v !== undefined && v !== null) {
          qs.set(k, String(v));
        }
      }
      const s = qs.toString();
      if (s) url += `?${s}`;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(url, {
        method,
        headers: options.customHeaders ?? this.headers(options.auth),
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });

      if (res.status >= 400) {
        const text = await res.text();
        throw new AgentGateError(res.status, text);
      }

      if (res.status === 204) return undefined as T;
      return (await res.json()) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  private get<T = unknown>(
    path: string,
    opts?: { params?: Record<string, string | number>; auth?: boolean },
  ) {
    return this.request<T>("GET", path, opts);
  }

  private post<T = unknown>(
    path: string,
    body?: unknown,
    opts?: { auth?: boolean; customHeaders?: Record<string, string> },
  ) {
    return this.request<T>("POST", path, { body, ...opts });
  }

  private put<T = unknown>(
    path: string,
    body?: unknown,
    opts?: { auth?: boolean },
  ) {
    return this.request<T>("PUT", path, { body, ...opts });
  }

  private del(path: string, opts?: { auth?: boolean }) {
    return this.request<void>("DELETE", path, opts);
  }

  // ── Registry operations ──

  /** List all registered agents. Optionally filter by skill or tag. */
  async listAgents(options?: {
    skill?: string;
    tag?: string;
  }): Promise<unknown[]> {
    const params: Record<string, string> = {};
    if (options?.skill) params["skill"] = options.skill;
    if (options?.tag) params["tag"] = options.tag;
    return this.get("/agents/", { params });
  }

  /** List all unique agent tags with counts. */
  async listTags(): Promise<Record<string, unknown>> {
    return this.get("/agents/tags");
  }

  /** Advanced agent search with full-text, multi-tag, and sorting. */
  async searchAgents(options?: {
    q?: string;
    tags?: string;
    skill?: string;
    sort?: string;
    limit?: number;
    offset?: number;
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string | number> = {
      sort: options?.sort ?? "newest",
      limit: options?.limit ?? 50,
      offset: options?.offset ?? 0,
    };
    if (options?.q) params["q"] = options.q;
    if (options?.tags) params["tags"] = options.tags;
    if (options?.skill) params["skill"] = options.skill;
    return this.get("/agents/search", { params });
  }

  /** Get a single agent by ID. */
  async getAgent(agentId: string): Promise<Record<string, unknown>> {
    return this.get(`/agents/${agentId}`);
  }

  /** Get all versions of an agent by name. */
  async getAgentVersions(
    name: string,
    version?: string,
  ): Promise<unknown[]> {
    const params: Record<string, string> = {};
    if (version) params["version"] = version;
    return this.get(`/agents/by-name/${name}`, { params });
  }

  /** Get the latest version of an agent by name. */
  async getAgentLatest(name: string): Promise<Record<string, unknown>> {
    return this.get(`/agents/by-name/${name}/latest`);
  }

  /** Get health status for an agent. */
  async getAgentHealth(agentId: string): Promise<Record<string, unknown>> {
    return this.get(`/agents/${agentId}/health`);
  }

  /** Get health status for all agents. */
  async getAllHealth(): Promise<Record<string, unknown>> {
    return this.get("/health/agents");
  }

  /** Get the A2A-compliant Agent Card for an agent. */
  async getAgentCard(agentId: string): Promise<Record<string, unknown>> {
    return this.get(`/agents/${agentId}/card`);
  }

  /** Register a new agent. Requires API key. */
  async registerAgent(options: {
    name: string;
    url: string;
    description?: string;
    version?: string;
    skills?: Record<string, unknown>[];
  }): Promise<Record<string, unknown>> {
    return this.post(
      "/agents/",
      {
        name: options.name,
        url: options.url,
        description: options.description ?? "",
        version: options.version ?? "1.0.0",
        skills: options.skills ?? [],
      },
      { auth: true },
    );
  }

  /** Update an agent. Requires API key. */
  async updateAgent(
    agentId: string,
    fields: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.put(`/agents/${agentId}`, fields, { auth: true });
  }

  /** Delete an agent by ID. Requires API key. */
  async deleteAgent(agentId: string): Promise<void> {
    return this.del(`/agents/${agentId}`, { auth: true });
  }

  // ── Reviews ──

  /** Submit a review for an agent (1-5 stars). */
  async createReview(
    agentId: string,
    rating: number,
    comment: string = "",
    reviewer: string = "anonymous",
  ): Promise<Record<string, unknown>> {
    return this.post(`/agents/${agentId}/reviews`, {
      rating,
      comment,
      reviewer,
    });
  }

  /** Get reviews for an agent. */
  async listReviews(
    agentId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<unknown[]> {
    return this.get(`/agents/${agentId}/reviews`, {
      params: {
        limit: options?.limit ?? 50,
        offset: options?.offset ?? 0,
      },
    });
  }

  /** Get aggregate review stats for an agent. */
  async getReviewStats(
    agentId: string,
  ): Promise<Record<string, unknown>> {
    return this.get(`/agents/${agentId}/reviews/stats`);
  }

  // ── A2A communication ──

  /** Send an A2A task to an agent via AgentGate routing. */
  async sendTask(
    agentId: string,
    text: string,
    options?: { taskId?: string; agentApiKey?: string },
  ): Promise<Record<string, unknown>> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (options?.agentApiKey) {
      headers["Authorization"] = `Bearer ${options.agentApiKey}`;
    }
    return this.post(
      `/agents/${agentId}/task`,
      {
        id: options?.taskId ?? "task-1",
        message: { parts: [{ type: "text", text }] },
      },
      { customHeaders: headers },
    );
  }

  /** Get invocation logs for an agent. Requires API key. */
  async getAgentLogs(
    agentId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<unknown[]> {
    return this.get(`/agents/${agentId}/logs`, {
      params: {
        limit: options?.limit ?? 50,
        offset: options?.offset ?? 0,
      },
      auth: true,
    });
  }

  /** Get usage stats for an agent. Requires API key. */
  async getAgentUsage(
    agentId: string,
  ): Promise<Record<string, unknown>> {
    return this.get(`/agents/${agentId}/usage`, { auth: true });
  }

  /** Get usage breakdown by day or month. Requires API key. */
  async getUsageBreakdown(
    agentId: string,
    options?: { period?: string; days?: number },
  ): Promise<Record<string, unknown>> {
    return this.get(`/agents/${agentId}/usage/breakdown`, {
      params: {
        period: options?.period ?? "day",
        days: options?.days ?? 30,
      },
      auth: true,
    });
  }

  // ── Chains ──

  /** Create a named chain of agent steps. Requires API key. */
  async createChain(options: {
    name: string;
    steps: Record<string, unknown>[];
    description?: string;
  }): Promise<Record<string, unknown>> {
    return this.post(
      "/chains/",
      {
        name: options.name,
        description: options.description ?? "",
        steps: options.steps,
      },
      { auth: true },
    );
  }

  /** List all chains. Requires API key. */
  async listChains(): Promise<unknown[]> {
    return this.get("/chains/", { auth: true });
  }

  /** Get a chain by ID. */
  async getChain(chainId: string): Promise<Record<string, unknown>> {
    return this.get(`/chains/${chainId}`, { auth: true });
  }

  /** Update a chain. */
  async updateChain(
    chainId: string,
    fields: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.put(`/chains/${chainId}`, fields, { auth: true });
  }

  /** Delete a chain. */
  async deleteChain(chainId: string): Promise<void> {
    return this.del(`/chains/${chainId}`, { auth: true });
  }

  /** Execute a chain with an initial input. Requires API key. */
  async runChain(
    chainId: string,
    input: string,
  ): Promise<Record<string, unknown>> {
    return this.post(`/chains/${chainId}/run`, { input }, { auth: true });
  }

  // ── Organization management ──

  /** Create an organization. Requires admin API key. */
  async createOrg(options: {
    name: string;
    apiKey: string;
    costPerInvocation?: number;
    rateLimit?: number;
    rateBurst?: number;
    billingAlertThreshold?: number;
  }): Promise<Record<string, unknown>> {
    const payload: Record<string, unknown> = {
      name: options.name,
      api_key: options.apiKey,
      cost_per_invocation: options.costPerInvocation ?? 0.001,
      rate_limit: options.rateLimit ?? 10.0,
      rate_burst: options.rateBurst ?? 20,
    };
    if (options.billingAlertThreshold !== undefined) {
      payload["billing_alert_threshold"] = options.billingAlertThreshold;
    }
    return this.post("/orgs/", payload, { auth: true });
  }

  /** List all organizations. Requires admin API key. */
  async listOrgs(): Promise<unknown[]> {
    return this.get("/orgs/", { auth: true });
  }

  /** Get an organization by ID. */
  async getOrg(orgId: string): Promise<Record<string, unknown>> {
    return this.get(`/orgs/${orgId}`, { auth: true });
  }

  /** Update an organization. */
  async updateOrg(
    orgId: string,
    fields: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.put(`/orgs/${orgId}`, fields, { auth: true });
  }

  /** Delete an organization. Requires admin API key. */
  async deleteOrg(orgId: string): Promise<void> {
    return this.del(`/orgs/${orgId}`, { auth: true });
  }

  /** List agents belonging to an organization. */
  async listOrgAgents(orgId: string): Promise<unknown[]> {
    return this.get(`/orgs/${orgId}/agents`, { auth: true });
  }

  /** Get billing summary for an organization. */
  async getOrgBilling(orgId: string): Promise<Record<string, unknown>> {
    return this.get(`/orgs/${orgId}/billing`, { auth: true });
  }

  /** Get daily billing breakdown for an organization. */
  async getOrgBillingBreakdown(
    orgId: string,
  ): Promise<Record<string, unknown>> {
    return this.get(`/orgs/${orgId}/billing/breakdown`, { auth: true });
  }

  /** Get wallet balance and tier info for an organization. */
  async getOrgWallet(orgId: string): Promise<Record<string, unknown>> {
    return this.get(`/orgs/${orgId}/wallet`, { auth: true });
  }

  /** Add funds to an organization's wallet. */
  async topupOrg(
    orgId: string,
    amount: number,
  ): Promise<Record<string, unknown>> {
    return this.post(`/orgs/${orgId}/topup`, { amount }, { auth: true });
  }

  /** List transactions for an organization. */
  async listOrgTransactions(
    orgId: string,
    options?: { limit?: number; offset?: number; role?: string },
  ): Promise<unknown[]> {
    return this.get(`/orgs/${orgId}/transactions`, {
      params: {
        limit: options?.limit ?? 50,
        offset: options?.offset ?? 0,
        role: options?.role ?? "all",
      },
      auth: true,
    });
  }

  /** Get transaction summary for an organization. */
  async getOrgTransactionSummary(
    orgId: string,
  ): Promise<Record<string, unknown>> {
    return this.get(`/orgs/${orgId}/transactions/summary`, { auth: true });
  }

  /** Change an organization's tier (free/pro/enterprise). */
  async changeOrgTier(
    orgId: string,
    tier: string,
  ): Promise<Record<string, unknown>> {
    return this.post(`/orgs/${orgId}/tier`, { tier }, { auth: true });
  }

  /** Start API key rotation. Returns new key (shown once). */
  async rotateOrgKey(orgId: string): Promise<Record<string, unknown>> {
    return this.post(`/orgs/${orgId}/rotate-key`, undefined, { auth: true });
  }

  /** Confirm key rotation: promote new key, revoke old. */
  async confirmOrgKeyRotation(
    orgId: string,
  ): Promise<Record<string, unknown>> {
    return this.post(`/orgs/${orgId}/confirm-rotation`, undefined, {
      auth: true,
    });
  }

  // ── UCP (Universal Commerce Protocol) ──

  /** Get the UCP discovery profile (/.well-known/ucp). */
  async ucpDiscover(): Promise<Record<string, unknown>> {
    return this.get("/.well-known/ucp");
  }

  /** List paid agents as UCP products. */
  async ucpCatalog(): Promise<Record<string, unknown>> {
    return this.get("/ucp/catalog");
  }

  /** Create a UCP checkout session for a paid agent task. */
  async ucpCheckoutCreate(
    agentId: string,
    task: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.post(
      "/ucp/checkout",
      { agent_id: agentId, task },
      { auth: true },
    );
  }

  /** Get the status of a UCP checkout session. */
  async ucpCheckoutGet(
    sessionId: string,
  ): Promise<Record<string, unknown>> {
    return this.get(`/ucp/checkout/${sessionId}`);
  }

  /** Complete a UCP checkout session (execute task + billing). */
  async ucpCheckoutComplete(
    sessionId: string,
  ): Promise<Record<string, unknown>> {
    return this.post(`/ucp/checkout/${sessionId}/complete`, undefined, {
      auth: true,
    });
  }

  // ── Discovery & Health ──

  /** Get the .well-known/agent.json discovery document. */
  async discover(): Promise<Record<string, unknown>> {
    return this.get("/.well-known/agent.json");
  }

  /** Check server health. */
  async health(): Promise<Record<string, unknown>> {
    return this.get("/health");
  }
}
