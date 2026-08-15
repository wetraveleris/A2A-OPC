const { test, expect } = require('@playwright/test');

const APP_URL = process.env.OPC_APP_URL || 'http://127.0.0.1:8012/app/';

test('registered user can edit profile, add work, view connections, and logout', async ({ page }) => {
  const username = 'e2e' + Date.now().toString().slice(-9);
  const runtimeErrors = [];
  page.on('pageerror', error => runtimeErrors.push(error.message));

  await page.goto(APP_URL);
  await page.locator('#registerTab').click();
  await page.locator('#authDisplayName').fill('E2E 测试用户');
  await page.locator('#authIdentity').fill(username + '@example.com');
  await page.locator('#authEmail').fill(username + '@example.com');
  await page.locator('#authPassword').fill('a-long-e2e-password');
  await page.locator('#authSubmit').click();
  await expect(page.locator('#authError')).toContainText('这里填写用户名，不是邮箱');
  await page.locator('#authIdentity').fill(username);
  await page.locator('#authSubmit').click();
  await expect(page.locator('#authGate')).toHaveClass(/hidden/, { timeout: 10_000 });

  await page.locator('[data-go="onboarding"]:visible').click();
  await expect(page.locator('#onboardingView')).toHaveClass(/active/);
  await page.locator('#myRole').fill('独立开发者');
  await page.locator('#myCity').fill('杭州');
  await page.locator('#profileProjectSummary').fill('测试一个公开的 Agent 协作项目。');
  await page.locator('#myOffers').fill('工程实现, Agent 接入');
  await page.locator('#myNeeds').fill('真实用户反馈');
  await page.locator('#profileSaveButton').click();
  await expect(page.locator('#toast')).toContainText('资料已保存');

  await page.locator('#workTitle').fill('A2A 协作原型');
  await page.locator('#workSummary').fill('两个本地模型通过公网 Relay 沟通。');
  await page.locator('#workForm button[type="submit"]').click();
  await expect(page.locator('.work-card')).toHaveCount(1);

  await page.locator('[data-go="friends"]:visible').click();
  await expect(page.locator('#friendsView')).toHaveClass(/active/);
  await expect(page.locator('#connectionList')).toContainText('还没有好友连接');
  await expect(page.locator('body')).not.toContainText('预约');
  await expect(page.locator('body')).not.toContainText('问 15:00');
  expect(runtimeErrors).toEqual([]);

  await page.locator('[data-go="onboarding"]:visible').click();
  await page.locator('#logoutButton').click();
  await expect(page.locator('#authGate')).toBeVisible();
});

test('discovery assessment creates a real connection request', async ({ page, playwright }) => {
  const suffix = Date.now().toString().slice(-8);
  const senderUsername = 'sender' + suffix;
  const recipientUsername = 'target' + suffix;
  const apiBase = new URL(APP_URL).origin;
  const recipient = await playwright.request.newContext({ baseURL: apiBase });

  await recipient.post('/api/auth/register', {
    data: {
      username: recipientUsername,
      email: recipientUsername + '@example.com',
      password: 'recipient-test-password',
      displayName: '目标用户'
    }
  });
  await recipient.put('/api/me/profile', {
    data: {
      role: 'Agent 工程师',
      city: '杭州',
      projectSummary: '帮助小团队接入本地模型。',
      offers: ['Agent 接入'],
      needs: ['产品反馈'],
      discoverable: true
    }
  });

  await page.goto(APP_URL);
  await page.locator('#registerTab').click();
  await page.locator('#authDisplayName').fill('请求用户');
  await page.locator('#authIdentity').fill(senderUsername);
  await page.locator('#authEmail').fill(senderUsername + '@example.com');
  await page.locator('#authPassword').fill('sender-test-password');
  await page.locator('#authSubmit').click();
  await expect(page.locator('#authGate')).toHaveClass(/hidden/);
  await expect(page.locator('#candidateName')).toHaveText('目标用户');

  const assessmentResponse = page.waitForResponse(response =>
    response.url().includes('/api/discovery/') && response.url().endsWith('/assessment')
  );
  await page.locator('#agentButton').click();
  expect((await assessmentResponse).status()).toBe(200);
  await expect(page.locator('#connectView')).toHaveClass(/active/);
  await expect(page.locator('#connectTitle')).toHaveText('Agent 已完成初步了解');
  await expect(page.locator('#connectDisclosure')).toContainText('没有联系对方');
  await expect(page.locator('#approveButton')).toHaveText('请求认识');

  await page.locator('#approveButton').click();
  await expect(page.locator('#approveButton')).toHaveText('等待对方接受');
  const requests = await recipient.get('/api/connection-requests');
  const incoming = (await requests.json()).find(item =>
    item.direction === 'INCOMING' && item.user.username === senderUsername
  );
  expect(incoming).toBeTruthy();
  expect((await recipient.post('/api/connection-requests/' + incoming.id + '/accept')).status()).toBe(204);

  await page.locator('#connectView [data-go="discover"]').click();
  await page.locator('[data-go="friends"]:visible').click();
  await expect(page.locator('#connectionList')).toContainText('目标用户');
  await recipient.dispose();
});
