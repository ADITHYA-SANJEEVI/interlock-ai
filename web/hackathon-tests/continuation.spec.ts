import { expect, test } from "@playwright/test";
import {
  Continuation,
  readContinuation,
} from "../app/paypal/continuation-contract";
const id = "11111111-1111-4111-8111-111111111111";
const pagePath = `/capture-continuation/${id}`;
const fixture = "http://127.0.0.1:3111";

test.beforeEach(async ({ request, context }) => {
  await request.post(fixture + "/__reset", { data: {} });
  await context.addCookies([
    {
      name: "interlock_session",
      value: "synthetic-approver",
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
    },
  ]);
});

test("human review has exact source terms and duplicate-click protection", async ({
  page,
  request,
}) => {
  await page.goto(pagePath);
  await page
    .getByRole("button", { name: "Refresh canonical continuation" })
    .click();
  await expect(
    page.getByText("One workspace-seat for alice", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve exact continuation once" }),
  ).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page
    .getByRole("button", { name: "Approve exact continuation once" })
    .dblclick();
  await expect(
    page.getByText("Canonical approval recorded", { exact: true }),
  ).toBeVisible();
  expect((await (await request.get(fixture + "/__state")).json()).writes).toBe(
    1,
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Refresh canonical continuation" })
    .click();
  await expect(
    page.getByText("Canonical approval recorded", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve exact continuation once" }),
  ).toBeDisabled();
});

for (const mode of ["expired", "unavailable", "mismatch"]) {
  test(`${mode} evidence never enables approval`, async ({ page, request }) => {
    await request.post(fixture + "/__reset", { data: { mode } });
    await page.goto(pagePath);
    await page
      .getByRole("button", { name: "Refresh canonical continuation" })
      .click();
    await expect(page.getByRole("alert").first()).toBeVisible();
    expect(
      (await (await request.get(fixture + "/__state")).json()).writes,
    ).toBe(0);
    if (mode === "expired")
      await expect(
        page.getByRole("button", { name: "Approve exact continuation once" }),
      ).toBeDisabled();
    else
      await expect(
        page.getByRole("button", { name: "Approve exact continuation once" }),
      ).toHaveCount(0);
  });
}

test("requester cannot supply independent approval", async ({
  page,
  context,
}) => {
  await context.addCookies([
    {
      name: "interlock_session",
      value: "synthetic-requester",
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
    },
  ]);
  await page.goto(pagePath);
  await page
    .getByRole("button", { name: "Refresh canonical continuation" })
    .click();
  await expect(
    page.getByText("Signed-in principal: synthetic_requester", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve exact continuation once" }),
  ).toBeDisabled();
});

test("lost response retains original command and refresh finds canonical approval", async ({
  page,
  request,
}) => {
  await request.post(fixture + "/__reset", { data: { mode: "lost" } });
  await page.goto(pagePath);
  await page
    .getByRole("button", { name: "Refresh canonical continuation" })
    .click();
  await page.getByRole("checkbox").check();
  await page
    .getByRole("button", { name: "Approve exact continuation once" })
    .click();
  await expect(page.getByRole("alert").first()).toContainText(
    "original command identity is retained",
  );
  expect(
    await page.evaluate(
      () =>
        Object.keys(localStorage).filter((k) =>
          k.startsWith("interlock:continuation:"),
        ).length,
    ),
  ).toBe(1);
  await page
    .getByRole("button", { name: "Refresh canonical continuation" })
    .click();
  await expect(
    page.getByText("Canonical approval recorded", { exact: true }),
  ).toBeVisible();
  expect((await (await request.get(fixture + "/__state")).json()).writes).toBe(
    1,
  );
});

test("same-origin proxy excludes preparation, capture and cross-origin approval", async ({
  request,
}) => {
  const base = `/api/paypal/continuations/${id}`;
  expect(
    (await request.post(base + "/capture-requests", { data: {} })).status(),
  ).toBe(404);
  expect(
    (await request.post("/api/paypal/continuations", { data: {} })).status(),
  ).toBe(404);
  expect(
    (
      await request.post(base + "/approvals", {
        data: {},
        headers: { Origin: "https://untrusted.example" },
      })
    ).status(),
  ).toBe(403);
});

test("client contract rejects identity, denomination and allocation mismatches", async ({
  request,
}) => {
  const state = await (await request.get(fixture + "/__state")).json();
  const original = state.record as Continuation;
  readContinuation(original, id);
  for (const field of [
    "id",
    "currency",
    "amount_minor",
    "merchant_id",
    "provider_order_id",
    "capture_request_id",
    "reservation_id",
    "tenant_id",
  ]) {
    const changed = { ...original, [field]: "INVALID" };
    expect(() => readContinuation(changed, id)).toThrow();
  }
  const att = JSON.parse(original.attestation_raw);
  att.quote.lines[0].beneficiary = "mallory";
  expect(() =>
    readContinuation({ ...original, attestation_raw: JSON.stringify(att) }, id),
  ).toThrow();
});
