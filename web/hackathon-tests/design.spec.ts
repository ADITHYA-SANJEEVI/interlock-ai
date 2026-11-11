import { expect, test } from "@playwright/test";

// Public design checks use only the isolated synthetic fixture service.
// No model, provider, financial database or private credential is used.
test("flagship illustration is interactive and the workspace link is unobstructed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Payment controls/ }),
  ).toBeVisible();
  const orbit = page.getByLabel("Illustrative purchase control flow").first();
  await expect(orbit.getByText("ILLUSTRATION", { exact: true })).toBeVisible();
  await orbit
    .getByRole("button", { name: "Explore Independent approval", exact: true })
    .click();
  await expect(orbit).toHaveAttribute("data-orbit-step", "3");
  const link = page.getByRole("link", { name: "Open workspace", exact: true });
  expect(
    await link.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return (
        r.bottom < innerHeight &&
        el.contains(
          document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
        )
      );
    }),
  ).toBe(true);
  await link.click();
  await expect(
    page.getByRole("heading", { name: "Requests & approvals", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("link", { name: "Legacy lab" }),
  ).toHaveCount(0);
});

test("reduced motion preserves the illustration and keyboard operation", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const orbit = page.getByLabel("Illustrative purchase control flow").first();
  const button = orbit.getByRole("button", {
    name: "Explore Policy",
    exact: true,
  });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(orbit).toHaveAttribute("data-orbit-step", "2");
  expect(
    await orbit
      .locator('[class*="orbitWorld"]')
      .evaluate((el) => getComputedStyle(el).transform),
  ).toBe("none");
  expect(
    await orbit.evaluate((el) =>
      el
        .getAnimations({ subtree: true })
        .some(
          (a) =>
            a.playState === "running" &&
            Number(a.effect?.getTiming().duration) > 1,
        ),
    ),
  ).toBe(false);
  await expect(
    page.getByRole("link", { name: "Open workspace", exact: true }),
  ).toBeVisible();
});

test("mobile public navigation reaches the historical demo while private intelligence stays scoped", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Explore Demo", exact: true })
    .click();
  await expect(page).toHaveURL(/\/demo$/, { timeout: 30000 });
  await expect(
    page
      .getByText("6C4956371B715513Y", { exact: true })
      .filter({ visible: true })
      .first(),
  ).toBeVisible();
  await page.goto("/intelligence");
  await expect(page).toHaveURL(/\/intelligence$/, { timeout: 30000 });
  await expect(
    page.getByRole("heading", { name: "Financial Intelligence", exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByRole("heading", { name: "Select a transaction to investigate." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Captured sandbox purchase" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Evidence consistency widget")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("public navigation and copy remain consistent across the three product routes", async ({
  page,
}) => {
  for (const route of ["/", "/workspace", "/intelligence"]) {
    await page.goto(route);
    const links = page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("link");
    await expect(links).toHaveText(
      route === "/"
        ? ["Product", "Explore Demo", "Documentation", "Sign In"]
        : ["Application", "Explore Demo", "Documentation"],
    );
    const text = await page.locator("body").innerText();
    for (const slogan of [
      "Your control room",
      "Purchase to proof",
      "Every effect.",
      "Independent sources, one investigation",
      "Coverage before conclusions",
      "The trust layer for agentic payments",
    ])
      expect(text.toLowerCase()).not.toContain(slogan.toLowerCase());
    expect(await page.evaluate(() => window.visualViewport?.scale)).toBe(1);
  }
});
