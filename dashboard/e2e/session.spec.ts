import { expect, test, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { PYTHON } from "../playwright.config";

interface Stored {
  id: string;
  agents: Record<string, string>;
}

async function storedSession(page: Page): Promise<Stored> {
  return page.evaluate(
    () => JSON.parse(localStorage.getItem("doubleblind.sessions") ?? "[]")[0] as Stored,
  );
}

async function viewAs(page: Page, name: RegExp) {
  await page.getByRole("tab", { name }).click();
  await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
}

test("a human creates a session, answers the agents' questions and signs both sides", async ({
  page,
  baseURL,
}) => {
  let agents: ChildProcess | null = null;
  try {
    await page.goto("/");
    await page.getByLabel("Session name").fill("Playwright · Spokes ↔ Parcelly");
    await page.getByRole("button", { name: "Create and start" }).click();
    await expect(page.getByTestId("session-page")).toBeVisible();
    await expect(page.getByTestId("agents-connect")).toBeVisible();

    const stored = await storedSession(page);
    agents = spawn(
      PYTHON,
      [
        "../scripts/dashboard_e2e.py",
        "parties",
        baseURL ?? "",
        stored.id,
        stored.agents.spokes,
        stored.agents.parcelly,
      ],
      { stdio: "inherit" },
    );

    await viewAs(page, /Spokes/);
    await expect(page.getByTestId("party-parcelly")).toContainText("Private to Parcelly");
    await expect(page.getByTestId("party-parcelly")).not.toContainText("Questions for our humans");
    await page
      .getByTestId("question-Q1")
      .getByRole("button", { name: /^tak, oznacz/ })
      .click({ timeout: 90_000 });
    await expect(page.getByTestId("question-Q1")).toContainText("✓");
    await page
      .getByTestId("question-Q2")
      .getByRole("button", { name: /^tak/ })
      .click({ timeout: 90_000 });
    await expect(page.getByTestId("question-Q2")).toContainText("✓");

    const sign = page.getByTestId("signatures").getByRole("button", { name: /^Sign v\d+/ });
    await sign.click({ timeout: 120_000 });
    await expect(page.getByTestId("signatures")).toContainText("✓ Signed");

    await viewAs(page, /Parcelly/);
    await page
      .getByTestId("signatures")
      .getByRole("button", { name: /^Sign v\d+/ })
      .click();

    await viewAs(page, /Observer/);
    await expect(page.getByTestId("state-badge").first()).toHaveText(/Certified/, {
      timeout: 120_000,
    });
    await expect(page.getByTestId("matrix-E1")).toContainText("Pass");
    await expect(page.getByTestId("negotiation-room")).toContainText("Certificate issued");
  } finally {
    agents?.kill();
  }
});
