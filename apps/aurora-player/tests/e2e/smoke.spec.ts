import { expect, test } from "@playwright/test";

test("opens the Aurora library workspace", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await expect(page.getByLabel("Player")).toBeVisible();
});
