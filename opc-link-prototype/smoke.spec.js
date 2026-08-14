const { test, expect } = require('@playwright/test');

const APP_URL = process.env.OPC_APP_URL || 'http://127.0.0.1:8010/app/';

test('core OPC Link prototype flow', async ({ page }) => {
  await page.goto(APP_URL);
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
  await page.goto(APP_URL);

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

test('takeover start button recovers after a successful start and stop', async ({ page }) => {
  const appUrl = process.env.OPC_APP_URL || 'http://127.0.0.1:8010/app/';
  const apiBase = new URL(appUrl).origin;
  const createdResponse = await page.request.post(`${apiBase}/api/human-agent-chats`, {
    data: {
      goal: '验证托管按钮在启动和停止后恢复正确文案',
      mode: 'AGENT_TAKEOVER',
      maxTurns: 1,
    },
  });
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json();

  await page.goto(`${apiBase}${created.participantAUrl}`);
  await expect(page.locator('#startButton')).toBeVisible();
  await expect(page.locator('#startButton')).toHaveText('开始 Agent 托管');

  await page.locator('#startButton').click();
  await expect(page.locator('#roomState')).toHaveText(/AGENT RUNNING|COMPLETED/);
  await expect(page.locator('#startButton')).toHaveText('开始 Agent 托管');
});

test('two Agents mutually confirm today at 15:00', async ({ page }) => {
  await page.goto(APP_URL);
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

test('public internet A2A view sends to computer B and shows local model evidence', async ({ page }) => {
  await page.route('**/api/internet-a2a/targets', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([
      {
        id: 'computer-b',
        name: '电脑 B · 沈知野 Agent',
        baseUrl: 'https://example.com/agent-b/a2a/shen-zhiye',
        protocolVersion: '1.0',
        skillId: 'employee_chat',
        skillName: 'Employee Chat',
        summary: '由 B 电脑本地模型生成回复。',
        defaultPrompt: '你是谁？请只介绍自己的身份。'
      },
      {
        id: 'perkoon',
        name: 'Perkoon Agent',
        baseUrl: 'https://perkoon.com',
        protocolVersion: '0.3.0',
        skillId: 'describe',
        skillName: 'Describe Capabilities',
        summary: 'Public P2P file-transfer Agent',
        defaultPrompt: 'Give three next steps.'
      }
    ])
  }));
  await page.route('**/api/internet-a2a/demo', async route => {
    const payload = route.request().postDataJSON();
    expect(payload.targetId).toBe('computer-b');
    expect(payload.prompt).toContain('你是谁');
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'exchange-ui-1',
        targetId: 'computer-b',
        targetName: '电脑 B · 沈知野 Agent',
        targetUrl: 'https://example.com/agent-b/a2a/shen-zhiye',
        skillId: 'employee_chat',
        skillName: 'Employee Chat',
        prompt: payload.prompt,
        sentMessage: `User request: ${payload.prompt}`,
        taskId: 'task-ui-1',
        taskState: 'TASK_STATE_COMPLETED',
        responseText: '我是沈知野，一个独立开发者。',
        remoteProvider: 'ollama',
        remoteModel: 'qwen3:1.7b',
        createdAt: new Date().toISOString()
      })
    });
  });

  await page.goto(APP_URL);
  await page.locator('[data-go="internet"]').click();
  await expect(page.locator('#internetView')).toHaveClass(/active/);
  await expect(page.locator('#internetTargetSelect')).toHaveValue('computer-b');
  await expect(page.locator('#internetTargetName')).toHaveText('电脑 B · 沈知野 Agent');
  await expect(page.locator('#internetProtocol')).toHaveText('A2A 1.0');

  await page.locator('#internetSendButton').click();
  await expect(page.locator('#internetResponse')).toContainText('我是沈知野');
  await expect(page.locator('#internetModelSource')).toHaveText('电脑 B 本地 · ollama / qwen3:1.7b');
  await expect(page.locator('#internetTaskId')).toHaveText('task-ui-1');
});

test('two Agent debugger creates separate participant pages', async ({ page }) => {
  await page.route('**/api/relay/agents', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([
      { agentId: 'opc-builder', online: true, metadata: { provider: 'ollama', model: 'qwen3:4b' } },
      { agentId: 'shen-zhiye', online: true, metadata: { provider: 'ollama', model: 'qwen3:1.7b' } }
    ])
  }));
  await page.route('**/api/human-agent-chats', async route => {
    const payload = route.request().postDataJSON();
    expect(payload.mode).toBe('AGENT_TAKEOVER');
    expect(payload.runPolicy).toBe('CONTINUOUS');
    expect(payload.topology).toBe('RELAY_A_B');
    expect(payload.maxTurns).toBeUndefined();
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'human-room-ui-1',
        mode: 'AGENT_TAKEOVER',
        state: 'AGENT_READY',
        topology: 'RELAY_A_B',
        agentAUrl: 'relay://opc-builder',
        agentBUrl: 'relay://shen-zhiye',
        participantAUrl: '/app/agent-room.html?room=human-room-ui-1&token=token-a',
        participantBUrl: '/app/agent-room.html?room=human-room-ui-1&token=token-b'
      })
    });
  });

  await page.goto(APP_URL);
  await page.locator('[data-go="internet"]').click();
  await page.locator('#internetChatMode').click();
  await expect(page.locator('#employeeChatPanel')).toHaveClass(/active/);
  await page.locator('input[name="chatMode"][value="AGENT_TAKEOVER"]').check();
  await expect(page.locator('#employeeChatButton')).toHaveText('创建 Agent 托管会话');
  await expect(page.locator('#employeeAgentAUrl')).toContainText('relay://opc-builder');
  await expect(page.locator('#employeeAgentBUrl')).toContainText('relay://shen-zhiye');
  await expect(page.locator('#employeeModeHelp')).toContainText('两台电脑的模型才会介入');
  await page.locator('#employeeChatButton').click();
  await expect(page.locator('#humanRoomLinks')).toBeVisible();
  await expect(page.locator('#humanRoomHint')).toContainText('点击开始托管后');
  await expect(page.locator('#employeeAgentAUrl')).toContainText('relay://opc-builder');
  await expect(page.locator('#employeeAgentBUrl')).toContainText('relay://shen-zhiye');
  await expect(page.locator('#humanRoomAOpen')).toHaveAttribute('href', /token=token-a/);
  await expect(page.locator('#humanRoomBOpen')).toHaveAttribute('href', /token=token-b/);
});

test('two browser pages hand off human-approved A2A drafts', async ({ page, context }) => {
  const apiResponse = await page.request.post(`${APP_URL.replace('/app/', '')}/api/human-agent-chats`, {
    data: {
      fromAgentId: 'opc-builder',
      toAgentId: 'shen-zhiye',
      goal: '两个用户分别审核 Agent 草稿，并确认一次真实 A2A 交接',
      maxTurns: 1,
      mode: 'HUMAN_APPROVAL'
    }
  });
  expect(apiResponse.status()).toBe(201);
  const room = await apiResponse.json();
  const pageA = page;
  const pageB = await context.newPage();

  await Promise.all([
    pageA.goto(new URL(room.participantAUrl, APP_URL).href),
    pageB.goto(new URL(room.participantBUrl, APP_URL).href)
  ]);
  await expect(pageA.locator('#viewerName')).toContainText('用户 A');
  await expect(pageB.locator('#viewerName')).toContainText('用户 B');
  await expect(pageA.locator('#composer')).toBeVisible();
  await expect(pageB.locator('#composer')).toBeHidden();

  await pageA.locator('#draftText').fill('请对方 Agent 提出一个两周内可验证的合作方案。');
  await pageA.locator('#approveButton').click();
  await expect(pageB.locator('#composer')).toBeVisible({ timeout: 30_000 });
  await expect(pageA.locator('#composer')).toBeHidden();

  const draft = await pageB.locator('#draftText').inputValue();
  await pageB.locator('#draftText').fill(`${draft} 本人补充：验收必须包含真实用户反馈。`);
  await pageB.locator('#approveButton').click();
  await expect(pageB.locator('#roomState')).toHaveText('COMPLETED');
  await expect(pageA.locator('#roomState')).toHaveText('COMPLETED', { timeout: 10_000 });
  expect(await pageA.locator('.edited').count()).toBeGreaterThanOrEqual(1);
  await expect(pageA.locator('.edited').last()).toContainText('本人修改后批准');
  await expect(pageA.locator('#evidence details')).toHaveCount(1);
});

test('Agent takeover waits for consent inside the room', async ({ page }) => {
  const participantA = { side: 'a', agentId: 'opc-builder', agentName: '陈默', role: '独立产品构建者' };
  const participantB = { side: 'b', agentId: 'shen-zhiye', agentName: '沈知野', role: '品牌与增长顾问' };
  const baseView = {
    id: 'ready-room-ui-1',
    goal: '先确认目标，再决定是否托管',
    maxTurns: 2,
    runPolicy: 'LIMITED',
    mode: 'AGENT_TAKEOVER',
    version: 1,
    viewer: participantA,
    other: participantB,
    waitingForAgentId: 'opc-builder',
    canAct: false,
    context: { goal: '先确认目标，再决定是否托管', knownFacts: [], decisions: [], openQuestions: [] },
    pendingDraft: null,
    messages: [],
    a2ATurns: [],
    audit: [{ sequence: 1, action: 'conversation.created', actorAgentId: 'opc-builder', detail: '等待用户授权启动。' }],
    error: null
  };

  await page.route('**/api/human-agent-chats/ready-room-ui-1**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/events')) {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
      return;
    }
    if (url.pathname.endsWith('/start')) {
      expect(route.request().postDataJSON().reason).toContain('授权');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...baseView, state: 'AGENT_RUNNING', version: 2, canStart: false, canStop: true })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...baseView, state: 'AGENT_READY', canStart: true, canStop: false })
    });
  });

  await page.goto(`${APP_URL}agent-room.html?room=ready-room-ui-1&token=token-a`);
  await expect(page.locator('#roomState')).toHaveText('READY');
  await expect(page.locator('#modeTitle')).toHaveText('Agent 托管待授权');
  await expect(page.locator('#timeline')).toContainText('目前没有调用模型');
  await expect(page.locator('.message')).toHaveCount(0);
  await expect(page.locator('#startButton')).toBeVisible();
  await expect(page.locator('#stopButton')).toBeHidden();

  await page.locator('#startButton').click();
  await expect(page.locator('#roomState')).toHaveText('AGENT RUNNING');
  await expect(page.locator('#startButton')).toBeHidden();
  await expect(page.locator('#stopButton')).toBeVisible();
});

test('two participant pages observe Agent takeover and A2A evidence', async ({ page, context }) => {
  const makeView = token => {
    const viewerA = token === 'token-a';
    const participantA = { side: 'a', agentId: 'opc-builder', agentName: '陈默', role: '独立产品构建者' };
    const participantB = { side: 'b', agentId: 'shen-zhiye', agentName: '沈知野', role: '品牌与增长顾问' };
    return {
      id: 'auto-room-ui-1',
      goal: '让两个 Agent 自动形成一个两周合作实验',
      state: 'COMPLETED',
    maxTurns: 1,
    runPolicy: 'LIMITED',
      mode: 'AGENT_TAKEOVER',
      version: 4,
      viewer: viewerA ? participantA : participantB,
      other: viewerA ? participantB : participantA,
      waitingForAgentId: null,
      canAct: false,
      canStop: false,
      context: {
        goal: '让两个 Agent 自动形成一个两周合作实验',
        knownFacts: ['双方将先验证一个小范围合作'],
        decisions: ['两周内访谈 5 位用户'],
        openQuestions: []
      },
      pendingDraft: null,
      messages: [{
        turn: 0,
        speakerAgentId: 'opc-builder',
        recipientAgentId: 'shen-zhiye',
        text: '请提出一个两周可验证的合作方案。',
        originalText: '请提出一个两周可验证的合作方案。',
        humanEdited: false,
        humanApproved: false,
        approvedByAgentId: 'opc-builder'
      }, {
        turn: 1,
        speakerAgentId: 'shen-zhiye',
        recipientAgentId: 'opc-builder',
        text: '建议共同访谈 5 位独立开发者并复盘转化。',
        originalText: '建议共同访谈 5 位独立开发者并复盘转化。',
        humanEdited: false,
        humanApproved: false,
        approvedByAgentId: 'shen-zhiye',
        sourceTaskId: 'task-auto-ui-1',
        sourceTaskState: 'TASK_STATE_COMPLETED'
      }],
      a2ATurns: [{
        turn: 1,
        fromAgentId: 'opc-builder',
        toAgentId: 'shen-zhiye',
        agentCardUrl: 'https://agent-b.example/.well-known/agent-card.json',
        jsonrpcMethod: 'message/send',
        jsonrpcUrl: 'https://agent-b.example/',
        taskId: 'task-auto-ui-1',
        taskState: 'TASK_STATE_COMPLETED',
        request: { message: '请提出一个两周可验证的合作方案。' },
        response: { reply: '建议共同访谈 5 位独立开发者并复盘转化。' }
      }],
      audit: [{ sequence: 1, action: 'conversation.created', actorAgentId: 'opc-builder', detail: '用户授权 Agent 接管。' }],
      error: null
    };
  };

  await context.route('**/api/human-agent-chats/auto-room-ui-1**', async route => {
    const url = new URL(route.request().url());
    const view = makeView(url.searchParams.get('token'));
    if (url.pathname.endsWith('/events')) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: conversation.updated\ndata: ${JSON.stringify(view)}\n\n`
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(view) });
  });

  const pageA = page;
  const pageB = await context.newPage();
  await Promise.all([
    pageA.goto(`${APP_URL}agent-room.html?room=auto-room-ui-1&token=token-a`),
    pageB.goto(`${APP_URL}agent-room.html?room=auto-room-ui-1&token=token-b`)
  ]);

  for (const participantPage of [pageA, pageB]) {
    await expect(participantPage.locator('#modeTitle')).toHaveText('Agent 自动托管');
    await expect(participantPage.locator('#composer')).toBeHidden();
    await expect(participantPage.locator('#roomState')).toHaveText('COMPLETED');
    await expect(participantPage.locator('.message')).toHaveCount(2);
    await expect(participantPage.locator('.edited')).toHaveCount(2);
    await expect(participantPage.locator('.edited').first()).toHaveText('Agent 自动发送');
    await expect(participantPage.locator('#evidence details')).toHaveCount(1);
    await expect(participantPage.locator('#evidence')).toContainText('task-auto-ui-1');
  }
});

test('room switches between direct chat, takeover, and approval', async ({ page }) => {
  const participantA = { side: 'a', agentId: 'opc-builder', agentName: '陈默', role: '产品顾问' };
  const participantB = { side: 'b', agentId: 'shen-zhiye', agentName: '沈知野', role: '独立开发者' };
  const common = {
    id: 'switch-room-ui-1',
    goal: '持续沟通并随时切换控制权',
    maxTurns: null,
    runPolicy: 'CONTINUOUS',
    viewer: participantA,
    other: participantB,
    waitingForAgentId: null,
    context: { goal: '持续沟通并随时切换控制权', knownFacts: [], decisions: [], openQuestions: [] },
    pendingDraft: null,
    a2ATurns: [],
    audit: [],
    error: null
  };
  let view = {
    ...common,
    mode: 'HUMAN_DIRECT',
    state: 'HUMAN_DIRECT',
    version: 1,
    canAct: false,
    canStart: false,
    canStop: false,
    canSendDirect: true,
    messages: []
  };

  await page.route('**/api/human-agent-chats/switch-room-ui-1**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/events')) {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
      return;
    }
    if (url.pathname.endsWith('/messages')) {
      const payload = route.request().postDataJSON();
      view = {
        ...view,
        version: 2,
        messages: [{
          turn: 0,
          speakerAgentId: 'opc-builder',
          recipientAgentId: 'shen-zhiye',
          text: payload.message,
          originalText: payload.message,
          humanEdited: false,
          humanApproved: true,
          source: 'HUMAN_DIRECT',
          approvedByAgentId: 'opc-builder'
        }]
      };
    } else if (url.pathname.endsWith('/mode')) {
      const mode = route.request().postDataJSON().mode;
      view = mode === 'AGENT_TAKEOVER'
        ? { ...view, mode, state: 'AGENT_READY', version: view.version + 1, canStart: true, canSendDirect: false }
        : { ...view, mode, state: 'WAITING_OWNER_B', version: view.version + 1, canStart: false, canSendDirect: false };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(view) });
  });

  await page.goto(`${APP_URL}agent-room.html?room=switch-room-ui-1&token=token-a`);
  await expect(page.locator('#modeTitle')).toHaveText('人工直接沟通');
  await expect(page.locator('#directComposer')).toBeVisible();
  await expect(page.locator('#roomRelation')).toContainText('持续会话');

  await page.locator('#directText').fill('这条消息由用户本人直接发送。');
  await page.locator('#directSendButton').click();
  await expect(page.locator('.message')).toHaveCount(1);
  await expect(page.locator('.message')).toContainText('人工直接发送');

  await page.locator('[data-mode="AGENT_TAKEOVER"]').click();
  await expect(page.locator('#roomState')).toHaveText('READY');
  await expect(page.locator('#startButton')).toBeVisible();

  await page.locator('[data-mode="HUMAN_APPROVAL"]').click();
  await expect(page.locator('#modeTitle')).toHaveText('逐条人工审核');
  await expect(page.locator('#directComposer')).toBeHidden();
});
