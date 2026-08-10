const { test, expect } = require('@playwright/test');

test('core OPC Link prototype flow', async ({ page }) => {
  await page.goto('http://127.0.0.1:8010/app/');
  await expect(page.locator('#candidateName')).toHaveText('沈知野');
  await expect(page.locator('#candidateVideo')).toHaveJSProperty('readyState', 4);

  await page.locator('#candidateProject').click();
  await expect(page.locator('#reasonSheet')).toHaveClass(/open/);
  await page.waitForTimeout(350);
  await page.screenshot({ path: 'screenshots/discover-reason.png', fullPage: true });

  const screeningResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/screenings') && response.request().method() === 'POST'
  );
  await page.locator('#sheetAgentButton').click();
  expect((await screeningResponse).status()).toBe(201);
  await expect(page.locator('#connectView')).toHaveClass(/active/);
  await expect(page.locator('#connectRecommendation')).toHaveText(/建议认识|继续了解/);
  await expect(page.locator('#connectSummary')).not.toHaveText('');
  expect(await page.locator('#connectMatches li').count()).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#connectRound')).toHaveText('3 轮');
  await page.waitForTimeout(350);
  await page.screenshot({ path: 'screenshots/agent-result.png', fullPage: true });

  await page.locator('#transcriptButton').click();
  await expect(page.locator('#transcriptSheet')).toHaveClass(/open/);
  await expect(page.locator('.transcript-turn')).toHaveCount(3);
  await page.locator('#transcriptClose').click();

  await page.locator('[data-view="connect"] #approveButton').click();
  await expect(page.locator('#toast')).toContainText('已发送认识请求');
  await expect(page.locator('#approveButton')).toHaveText('等待对方确认');

  await page.locator('[data-go="discover"]:visible').click();
  await page.locator('#profileButton').click();
  await expect(page.locator('#profileView')).toHaveClass(/active/);
  await expect(page.locator('#profileName')).toHaveText('沈知野');
  await page.waitForTimeout(350);
  await page.screenshot({ path: 'screenshots/profile.png', fullPage: true });

  await page.locator('[data-go="discover"]:visible').click();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#candidateName')).toHaveText('林予');

  await page.locator('[data-go="onboarding"]:visible').click();
  await expect(page.locator('#onboardingView')).toHaveClass(/active/);
  await page.locator('#opcStatement').fill('正在做一个帮助独立开发者找到长期合作伙伴的 Agent 平台');
  await page.waitForTimeout(350);
  await page.screenshot({ path: 'screenshots/onboarding.png', fullPage: true });
});

test('mobile Agent result fits a 390 x 844 viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('http://127.0.0.1:8010/app/');

  const screeningResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/screenings') && response.request().method() === 'POST'
  );
  await page.locator('#agentButton').click();
  expect((await screeningResponse).status()).toBe(201);
  await expect(page.locator('#connectRecommendation')).toHaveText(/建议认识|继续了解/);
  await expect(page.locator('#connectSummary')).not.toHaveText('');
  await expect(page.locator('#transcriptSheet')).not.toHaveClass(/open/);

  const viewportMetrics = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
    windowScroll: window.scrollY,
    resultScroll: document.querySelector('#connectView .scroll').scrollTop,
    phoneScroll: document.querySelector('#phone').scrollTop,
  }));
  expect(viewportMetrics.width).toBe(viewportMetrics.viewport);
  expect(viewportMetrics.windowScroll).toBe(0);
  expect(viewportMetrics.resultScroll).toBe(0);
  expect(viewportMetrics.phoneScroll).toBe(0);
  await page.screenshot({ path: 'screenshots/mobile-agent-result.png', fullPage: true });
});

test('two Agents mutually confirm today at 15:00', async ({ page }) => {
  await page.goto('http://127.0.0.1:8010/app/');
  await page.locator('#profileButton').click();
  await expect(page.locator('#profileView')).toHaveClass(/active/);

  const scheduleResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/live-conversations/schedule') && response.request().method() === 'POST'
  );
  await page.locator('#profileScheduleButton').click();
  expect((await scheduleResponse).status()).toBe(201);
  await expect(page.locator('#scheduleSheet')).toHaveClass(/open/);
  await expect(page.locator('#scheduleTitle')).toHaveText('双方 Agent 都有空', { timeout: 20_000 });
  await expect(page.locator('.schedule-turn.complete')).toHaveCount(4);
  await page.waitForTimeout(350);
  await page.screenshot({ path: 'screenshots/schedule-confirmed.png', fullPage: true });

  await page.locator('#scheduleConfirmButton').click();
  await expect(page.locator('#toast')).toContainText('你已确认');
  await expect(page.locator('#scheduleConfirmButton')).toHaveText('等待对方本人确认');
});
