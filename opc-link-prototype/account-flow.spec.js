const { test, expect } = require('@playwright/test');

const APP_URL = process.env.OPC_APP_URL || 'http://127.0.0.1:8012/app/';

test('registered user can edit profile, add work, view connections, and logout', async ({ page }) => {
  const username = 'e2e' + Date.now().toString().slice(-9);
  const runtimeErrors = [];
  const mediaRequests = [];
  page.on('pageerror', error => runtimeErrors.push(error.message));
  page.on('request', request => {
    if (['image', 'media'].includes(request.resourceType())) mediaRequests.push(request.url());
  });

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
  await expect(page.locator('img, video')).toHaveCount(0);

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
  expect(mediaRequests).toEqual([]);

  await page.locator('[data-go="onboarding"]:visible').click();
  await page.locator('#logoutButton').click();
  await expect(page.locator('#authGate')).toBeVisible();
});

test('online Agent card creates an A2A introduction and contact history', async ({ page }) => {
  const suffix = Date.now().toString().slice(-8);
  const senderUsername = 'sender' + suffix;
  const turns = [1, 2, 3].map(turn => ({
    round: turn,
    fromAgentId: turn === 2 ? 'shen-zhiye' : 'opc-builder',
    toAgentId: turn === 2 ? 'opc-builder' : 'shen-zhiye',
    taskId: 'task-a2a-' + turn,
    taskState: 'TASK_STATE_COMPLETED',
    response: { shortMessage: '第 ' + turn + ' 轮真实 A2A 回复' }
  }));
  const introduction = {
    id: 'intro-1', screeningId: 'screening-1',
    sourceAgentId: 'opc-builder', targetAgentId: 'shen-zhiye',
    sourceName: '陈默', targetName: '沈知野',
    goal: '请双方介绍自己并判断是否值得建立联系。',
    state: 'WAITING_APPROVAL', relationState: 'NONE',
    report: {
      summary: '双方方向互补，建议建立联系。',
      commonGround: ['都在服务小团队'], complementarity: ['产品与工程互补'],
      risks: [], unconfirmed: ['确认首次合作范围']
    },
    transcript: turns, friendRequestId: null,
    createdAt: new Date().toISOString()
  };

  await page.route('**/api/me/agent-devices', route => route.fulfill({ json: [
    { id: 'device-a', agentId: 'opc-builder', name: 'A computer', platform: 'desktop', online: true, provider: 'ollama', model: 'qwen3:4b', isMine: true, isClaimed: true },
    { id: 'device-b', agentId: 'shen-zhiye', name: 'B computer', platform: 'desktop', online: true, provider: 'ollama', model: 'qwen3:1.7b', isMine: false, isClaimed: true }
  ] }));
  await page.route('**/api/discovery/online-agents', route => route.fulfill({ json: [
    { agentId: 'shen-zhiye', name: '沈知野', role: 'Agent 工程师', city: '杭州', projectSummary: '帮助小团队接入本地模型。', offers: ['Agent 接入'], needs: ['产品反馈'], collaborationStyle: '异步优先', online: true, provider: 'ollama', model: 'qwen3:1.7b', owner: { id: 'user-b', username: 'target-agent', displayName: '目标用户' }, relationState: 'NONE' }
  ] }));
  await page.route('**/api/agent-introductions', async route => {
    if (route.request().method() === 'POST') await route.fulfill({ status: 201, json: introduction });
    else await route.fallback();
  });
  await page.route('**/api/agent-introductions/intro-1/request-contact', route => route.fulfill({
    json: { ...introduction, state: 'CONTACT_REQUESTED', relationState: 'PENDING_OUTGOING', friendRequestId: 'request-1' }
  }));
  await page.route('**/api/connections', route => route.fulfill({ json: [
    { id: 'connection-1', user: { id: 'user-b', username: 'target-agent', displayName: '目标用户' }, devices: [{ id: 'device-b', name: 'B computer', platform: 'desktop', agentId: 'shen-zhiye', provider: 'ollama', model: 'qwen3:1.7b', status: 'ONLINE', lastSeenAt: new Date().toISOString() }], introductions: [{ ...introduction, state: 'CONNECTED' }], createdAt: new Date().toISOString() }
  ] }));
  await page.route('**/api/connection-requests', async route => {
    if (route.request().method() === 'GET') await route.fulfill({ json: [] });
    else await route.fallback();
  });

  await page.goto(APP_URL);
  await page.locator('#registerTab').click();
  await page.locator('#authDisplayName').fill('请求用户');
  await page.locator('#authIdentity').fill(senderUsername);
  await page.locator('#authEmail').fill(senderUsername + '@example.com');
  await page.locator('#authPassword').fill('sender-test-password');
  await page.locator('#authSubmit').click();
  await expect(page.locator('#authGate')).toHaveClass(/hidden/);
  await expect(page.locator('#candidateName')).toHaveText('沈知野');
  await expect(page.locator('#candidateSignal')).toContainText('qwen3:1.7b');

  await page.locator('#agentButton').click();
  await expect(page.locator('#connectView')).toHaveClass(/active/);
  await expect(page.locator('#connectTitle')).toHaveText('双方 Agent 已完成初聊');
  await expect(page.locator('#connectDisclosure')).toContainText('A2A Task ID');
  await expect(page.locator('#connectRound')).toHaveText('3 个 Task');
  await expect(page.locator('#approveButton')).toHaveText('请求建立联系');
  await page.locator('#transcriptButton').click();
  await expect(page.locator('#transcriptList')).toContainText('task-a2a-3');
  await page.locator('#transcriptClose').click();

  await page.locator('#approveButton').click();
  await expect(page.locator('#approveButton')).toHaveText('等待对方接受');

  await page.locator('#connectView [data-go="discover"]').click();
  await page.locator('[data-go="friends"]:visible').click();
  await expect(page.locator('#connectionList')).toContainText('目标用户');
  await expect(page.locator('#connectionList')).toContainText('查看认识记录 · 3 个 A2A Task');
});
