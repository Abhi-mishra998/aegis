// Sprint 8 — TreeDataProvider that renders the last N /execute decisions
// for the developer's Aegis tenant.

import * as vscode from "vscode";
import { AegisClient, DecisionRow } from "./aegisClient";

export class AegisDecisionsProvider
  implements vscode.TreeDataProvider<DecisionItem>
{
  private _onDidChangeTreeData: vscode.EventEmitter<
    DecisionItem | undefined | null | void
  > = new vscode.EventEmitter();
  readonly onDidChangeTreeData: vscode.Event<
    DecisionItem | undefined | null | void
  > = this._onDidChangeTreeData.event;

  private decisions: DecisionRow[] = [];
  private status: "idle" | "loading" | "error" | "no-key" = "idle";
  private lastError: string | null = null;

  constructor(private getClient: () => Promise<AegisClient | null>) {}

  refresh(): void {
    void this.load();
  }

  setNoKey(): void {
    this.status = "no-key";
    this.decisions = [];
    this._onDidChangeTreeData.fire();
  }

  async load(): Promise<void> {
    this.status = "loading";
    this._onDidChangeTreeData.fire();
    try {
      const client = await this.getClient();
      if (!client) {
        this.setNoKey();
        return;
      }
      const max = vscode.workspace
        .getConfiguration("aegis")
        .get<number>("maxDecisions", 25);
      this.decisions = await client.listRecentDecisions(max);
      this.status = "idle";
      this.lastError = null;
    } catch (err) {
      this.status = "error";
      this.lastError = err instanceof Error ? err.message : String(err);
      this.decisions = [];
    } finally {
      this._onDidChangeTreeData.fire();
    }
  }

  getTreeItem(element: DecisionItem): vscode.TreeItem {
    return element;
  }

  getChildren(element?: DecisionItem): vscode.ProviderResult<DecisionItem[]> {
    if (element) {
      return [];
    }
    if (this.status === "no-key") {
      const item = new DecisionItem(
        "Set Aegis API key to begin",
        "Run “Aegis: Set API Key” from the command palette.",
        "warning",
      );
      item.command = {
        command: "aegis.setApiKey",
        title: "Set Aegis API Key",
      };
      return [item];
    }
    if (this.status === "loading") {
      return [new DecisionItem("Loading…", "polling Aegis", "loading~spin")];
    }
    if (this.status === "error") {
      return [
        new DecisionItem(
          "Failed to load decisions",
          this.lastError ?? "unknown error",
          "error",
        ),
      ];
    }
    if (this.decisions.length === 0) {
      return [
        new DecisionItem(
          "No decisions yet",
          "Send a /execute call to populate this view.",
          "info",
        ),
      ];
    }
    return this.decisions.map((row) => {
      const item = new DecisionItem(
        labelFor(row),
        descriptionFor(row),
        iconFor(row),
      );
      item.tooltip = tooltipFor(row);
      item.command = {
        command: "aegis.openDecision",
        title: "Open decision",
        arguments: [row],
      };
      item.contextValue = "aegisDecision";
      return item;
    });
  }
}

export class DecisionItem extends vscode.TreeItem {
  constructor(label: string, description: string, icon: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.iconPath = new vscode.ThemeIcon(icon);
  }
}

function labelFor(row: DecisionRow): string {
  const tool = row.tool ?? "<tool>";
  const action = row.action ?? row.decision ?? "?";
  return `${action.padEnd(10)} · ${tool}`;
}

function descriptionFor(row: DecisionRow): string {
  const parts: string[] = [];
  if (row.timestamp) {
    try {
      parts.push(new Date(row.timestamp).toLocaleTimeString());
    } catch {
      parts.push(row.timestamp);
    }
  }
  if (row.risk_score != null) {
    parts.push(`risk ${Number(row.risk_score).toFixed(2)}`);
  }
  if (row.agent_id) {
    parts.push(`agent ${row.agent_id.slice(0, 8)}…`);
  }
  return parts.join(" · ");
}

function iconFor(row: DecisionRow): string {
  const a = (row.action ?? row.decision ?? "").toLowerCase();
  if (a === "allow") {
    return "pass";
  }
  if (a === "deny" || a === "kill" || a === "redact" || a === "blocked") {
    return "error";
  }
  if (a === "throttle" || a === "escalate") {
    return "warning";
  }
  return "circle-outline";
}

function tooltipFor(row: DecisionRow): string {
  const lines: string[] = [];
  if (row.audit_id) {
    lines.push(`audit_id: ${row.audit_id}`);
  }
  if (row.request_id) {
    lines.push(`request_id: ${row.request_id}`);
  }
  if (row.reason) {
    lines.push(`reason: ${row.reason}`);
  }
  if (row.risk_score != null) {
    lines.push(`risk_score: ${Number(row.risk_score).toFixed(3)}`);
  }
  return lines.join("\n");
}
