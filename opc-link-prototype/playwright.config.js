module.exports = {
  testDir: '.',
  testMatch: 'smoke.spec.js',
  timeout: 120_000,
  use: {
    channel: 'chrome',
    viewport: { width: 1280, height: 900 },
  },
};
