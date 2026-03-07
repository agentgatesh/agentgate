import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { AgentGateClient, AgentGateError } from "../src/index.js";

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function jsonResponse(data: unknown, status = 200) {
  return {
    status,
    ok: status < 400,
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as Response;
}

function emptyResponse(status = 204) {
  return {
    status,
    ok: status < 400,
    json: async () => undefined,
    text: async () => "",
  } as Response;
}

describe("AgentGateClient", () => {
  let client: AgentGateClient;

  beforeEach(() => {
    mockFetch.mockReset();
    client = new AgentGateClient({
      serverUrl: "https://test.agentgate.sh",
      apiKey: "test-key-123",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Constructor ──

  it("strips trailing slash from server URL", () => {
    const c = new AgentGateClient({ serverUrl: "https://example.com/" });
    expect((c as any).serverUrl).toBe("https://example.com");
  });

  it("defaults to https://agentgate.sh", () => {
    const c = new AgentGateClient();
    expect((c as any).serverUrl).toBe("https://agentgate.sh");
  });

  // ── Error handling ──

  it("throws AgentGateError on HTTP 4xx", async () => {
    mockFetch.mockResolvedValue({
      status: 404,
      ok: false,
      text: async () => "Not Found",
    } as Response);

    await expect(client.health()).rejects.toThrow(AgentGateError);
    await expect(client.health()).rejects.toThrow("HTTP 404");
  });

  it("throws AgentGateError on HTTP 5xx", async () => {
    mockFetch.mockResolvedValue({
      status: 500,
      ok: false,
      text: async () => "Internal Server Error",
    } as Response);

    await expect(client.health()).rejects.toThrow(AgentGateError);
  });

  it("AgentGateError has statusCode and detail", async () => {
    mockFetch.mockResolvedValue({
      status: 403,
      ok: false,
      text: async () => "Forbidden",
    } as Response);

    try {
      await client.health();
      expect.unreachable("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(AgentGateError);
      const err = e as AgentGateError;
      expect(err.statusCode).toBe(403);
      expect(err.detail).toBe("Forbidden");
    }
  });

  // ── Health & Discovery ──

  it("health()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "ok" }));
    const r = await client.health();
    expect(r).toEqual({ status: "ok" });
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/health",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("discover()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ name: "AgentGate" }));
    const r = await client.discover();
    expect(r).toEqual({ name: "AgentGate" });
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/.well-known/agent.json",
      expect.anything(),
    );
  });

  // ── Registry ──

  it("listAgents() without filters", async () => {
    mockFetch.mockResolvedValue(jsonResponse([{ id: "a1" }]));
    const r = await client.listAgents();
    expect(r).toEqual([{ id: "a1" }]);
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/agents/",
      expect.anything(),
    );
  });

  it("listAgents() with skill filter", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listAgents({ skill: "math" });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("skill=math"),
      expect.anything(),
    );
  });

  it("listAgents() with tag filter", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listAgents({ tag: "demo" });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("tag=demo"),
      expect.anything(),
    );
  });

  it("listTags()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ math: 2 }));
    const r = await client.listTags();
    expect(r).toEqual({ math: 2 });
  });

  it("searchAgents()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ agents: [], total: 0 }));
    await client.searchAgents({ q: "calc", tags: "math,demo", sort: "name" });
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("q=calc");
    expect(url).toContain("tags=math%2Cdemo");
    expect(url).toContain("sort=name");
  });

  it("getAgent()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "a1", name: "test" }));
    const r = await client.getAgent("a1");
    expect(r).toEqual({ id: "a1", name: "test" });
  });

  it("getAgentVersions()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([{ version: "1.0.0" }]));
    await client.getAgentVersions("my-agent");
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/agents/by-name/my-agent",
      expect.anything(),
    );
  });

  it("getAgentLatest()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ version: "2.0.0" }));
    await client.getAgentLatest("my-agent");
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/agents/by-name/my-agent/latest",
      expect.anything(),
    );
  });

  it("getAgentHealth()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "healthy" }));
    const r = await client.getAgentHealth("a1");
    expect(r).toEqual({ status: "healthy" });
  });

  it("getAllHealth()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ agents: {} }));
    await client.getAllHealth();
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/health/agents",
      expect.anything(),
    );
  });

  it("getAgentCard()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ name: "agent", skills: [] }));
    await client.getAgentCard("a1");
    expect(mockFetch).toHaveBeenCalledWith(
      "https://test.agentgate.sh/agents/a1/card",
      expect.anything(),
    );
  });

  it("registerAgent() sends auth header", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "new-id" }));
    await client.registerAgent({ name: "test", url: "http://localhost:8001" });
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.method).toBe("POST");
    expect(opts.headers["Authorization"]).toBe("Bearer test-key-123");
    const body = JSON.parse(opts.body);
    expect(body.name).toBe("test");
    expect(body.version).toBe("1.0.0");
  });

  it("updateAgent()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "a1", name: "updated" }));
    await client.updateAgent("a1", { name: "updated" });
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.method).toBe("PUT");
    expect(opts.headers["Authorization"]).toBe("Bearer test-key-123");
  });

  it("deleteAgent()", async () => {
    mockFetch.mockResolvedValue(emptyResponse());
    await client.deleteAgent("a1");
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.method).toBe("DELETE");
  });

  // ── Reviews ──

  it("createReview()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "r1" }));
    await client.createReview("a1", 5, "Great!", "user1");
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.rating).toBe(5);
    expect(body.comment).toBe("Great!");
    expect(body.reviewer).toBe("user1");
  });

  it("listReviews()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listReviews("a1", { limit: 10 });
    expect(mockFetch.mock.calls[0][0]).toContain("limit=10");
  });

  it("getReviewStats()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ avg: 4.5 }));
    await client.getReviewStats("a1");
    expect(mockFetch.mock.calls[0][0]).toContain("/reviews/stats");
  });

  // ── A2A ──

  it("sendTask() builds A2A message format", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ result: "ok" }));
    await client.sendTask("a1", "Hello!");
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.id).toBe("task-1");
    expect(body.message.parts[0]).toEqual({ type: "text", text: "Hello!" });
  });

  it("sendTask() with custom task ID and agent key", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ result: "ok" }));
    await client.sendTask("a1", "Hi", {
      taskId: "custom-id",
      agentApiKey: "agent-secret",
    });
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBe("Bearer agent-secret");
    const body = JSON.parse(opts.body);
    expect(body.id).toBe("custom-id");
  });

  it("getAgentLogs()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.getAgentLogs("a1");
    expect(mockFetch.mock.calls[0][0]).toContain("/agents/a1/logs");
    expect(mockFetch.mock.calls[0][1].headers["Authorization"]).toBe(
      "Bearer test-key-123",
    );
  });

  it("getAgentUsage()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ total: 100 }));
    await client.getAgentUsage("a1");
    expect(mockFetch.mock.calls[0][0]).toContain("/agents/a1/usage");
  });

  it("getUsageBreakdown()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ breakdown: [] }));
    await client.getUsageBreakdown("a1", { period: "month", days: 7 });
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toContain("period=month");
    expect(url).toContain("days=7");
  });

  // ── Chains ──

  it("createChain()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "c1" }));
    await client.createChain({
      name: "pipe",
      steps: [{ agent_id: "a1" }],
      description: "test chain",
    });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.name).toBe("pipe");
    expect(body.steps).toHaveLength(1);
  });

  it("listChains()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listChains();
    expect(mockFetch.mock.calls[0][0]).toContain("/chains/");
  });

  it("getChain()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "c1" }));
    await client.getChain("c1");
    expect(mockFetch.mock.calls[0][0]).toContain("/chains/c1");
  });

  it("updateChain()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "c1" }));
    await client.updateChain("c1", { name: "new-name" });
    expect(mockFetch.mock.calls[0][1].method).toBe("PUT");
  });

  it("deleteChain()", async () => {
    mockFetch.mockResolvedValue(emptyResponse());
    await client.deleteChain("c1");
    expect(mockFetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("runChain()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ results: [] }));
    await client.runChain("c1", "test input");
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.input).toBe("test input");
  });

  // ── Organizations ──

  it("createOrg()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "org1" }));
    await client.createOrg({ name: "TestOrg", apiKey: "org-key" });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.name).toBe("TestOrg");
    expect(body.api_key).toBe("org-key");
    expect(body.cost_per_invocation).toBe(0.001);
  });

  it("listOrgs()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listOrgs();
    expect(mockFetch.mock.calls[0][0]).toContain("/orgs/");
  });

  it("getOrg()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "org1" }));
    await client.getOrg("org1");
  });

  it("updateOrg()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ id: "org1" }));
    await client.updateOrg("org1", { name: "new" });
    expect(mockFetch.mock.calls[0][1].method).toBe("PUT");
  });

  it("deleteOrg()", async () => {
    mockFetch.mockResolvedValue(emptyResponse());
    await client.deleteOrg("org1");
    expect(mockFetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("listOrgAgents()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listOrgAgents("org1");
    expect(mockFetch.mock.calls[0][0]).toContain("/orgs/org1/agents");
  });

  it("getOrgBilling()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ total: 1.5 }));
    await client.getOrgBilling("org1");
  });

  it("getOrgBillingBreakdown()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ days: [] }));
    await client.getOrgBillingBreakdown("org1");
    expect(mockFetch.mock.calls[0][0]).toContain("/billing/breakdown");
  });

  it("getOrgWallet()", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ balance: 10.0, tier: "free" }),
    );
    const r = await client.getOrgWallet("org1");
    expect(r).toEqual({ balance: 10.0, tier: "free" });
  });

  it("topupOrg()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ new_balance: 15.0 }));
    await client.topupOrg("org1", 5.0);
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.amount).toBe(5.0);
  });

  it("listOrgTransactions()", async () => {
    mockFetch.mockResolvedValue(jsonResponse([]));
    await client.listOrgTransactions("org1", { role: "payer" });
    expect(mockFetch.mock.calls[0][0]).toContain("role=payer");
  });

  it("getOrgTransactionSummary()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ total_spent: 5.0 }));
    await client.getOrgTransactionSummary("org1");
    expect(mockFetch.mock.calls[0][0]).toContain("/transactions/summary");
  });

  it("changeOrgTier()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ tier: "pro" }));
    await client.changeOrgTier("org1", "pro");
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.tier).toBe("pro");
  });

  it("rotateOrgKey()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ new_key: "abc" }));
    await client.rotateOrgKey("org1");
    expect(mockFetch.mock.calls[0][0]).toContain("/rotate-key");
    expect(mockFetch.mock.calls[0][1].method).toBe("POST");
  });

  it("confirmOrgKeyRotation()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "confirmed" }));
    await client.confirmOrgKeyRotation("org1");
    expect(mockFetch.mock.calls[0][0]).toContain("/confirm-rotation");
  });

  // ── UCP ──

  it("ucpDiscover()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ucp_version: "2026-03-01" }));
    await client.ucpDiscover();
    expect(mockFetch.mock.calls[0][0]).toContain("/.well-known/ucp");
  });

  it("ucpCatalog()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ products: [] }));
    await client.ucpCatalog();
    expect(mockFetch.mock.calls[0][0]).toContain("/ucp/catalog");
  });

  it("ucpCheckoutCreate()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ session_id: "s1" }));
    await client.ucpCheckoutCreate("a1", { message: "test" });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.agent_id).toBe("a1");
    expect(body.task).toEqual({ message: "test" });
  });

  it("ucpCheckoutGet()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "pending" }));
    await client.ucpCheckoutGet("s1");
    expect(mockFetch.mock.calls[0][0]).toContain("/ucp/checkout/s1");
  });

  it("ucpCheckoutComplete()", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "completed" }));
    await client.ucpCheckoutComplete("s1");
    expect(mockFetch.mock.calls[0][0]).toContain("/ucp/checkout/s1/complete");
    expect(mockFetch.mock.calls[0][1].method).toBe("POST");
  });

  // ── Auth headers ──

  it("does not send auth header when no API key", async () => {
    const noAuthClient = new AgentGateClient({
      serverUrl: "https://test.agentgate.sh",
    });
    mockFetch.mockResolvedValue(jsonResponse([]));
    await noAuthClient.listOrgs();
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers["Authorization"]).toBeUndefined();
  });
});
