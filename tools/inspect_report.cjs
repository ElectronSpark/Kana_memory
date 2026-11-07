// Local HTML artifact QA only; uses a disposable headless browser profile.
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const packageRoot = process.env.KANA_NODE_MODULES;
if (!packageRoot) throw new Error('Set KANA_NODE_MODULES to the bundled Node packages directory.');
const {chromium} = require(path.join(packageRoot, 'playwright'));
const root = path.resolve(__dirname, '..');
(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1});
    await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
    await page.goto(pathToFileURL(path.join(root, 'docs', '程序设计与准备报告.html')).href);
    await page.addStyleTag({content: 'html{scroll-behavior:auto!important}'});
    const result = await page.evaluate(() => ({
      title: document.title,
      sectionCount: document.querySelectorAll('section').length,
      unresolvedAnchors: [...document.querySelectorAll('a[href^="#"]')].map(a => a.hash.slice(1)).filter(id => !document.getElementById(id)),
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: innerWidth,
      originRows: document.querySelectorAll('#origins tbody tr').length,
      kanaRows: document.querySelectorAll('#inventory tbody tr').length,
    }));
    await page.screenshot({path: path.join(root, 'research', 'report_preview.png')});
    await page.evaluate(() => {
      document.querySelector('#origins details').open = true;
      scrollTo(0, document.querySelector('#origins').offsetTop - 20);
    });
    await page.screenshot({path: path.join(root, 'research', 'report_kana_preview.png')});
    await page.setViewportSize({width: 430, height: 900});
    await page.evaluate(() => scrollTo(0,0));
    result.mobile = await page.evaluate(() => ({documentWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth}));
    await page.screenshot({path: path.join(root, 'research', 'report_mobile_preview.png')});
    if (result.unresolvedAnchors.length || result.originRows !== 46 || result.kanaRows !== 165 || result.documentWidth > result.viewportWidth || result.mobile.documentWidth > result.mobile.viewportWidth) {
      throw new Error(JSON.stringify(result));
    }
    fs.writeFileSync(path.join(root, 'research', 'report_visual_audit.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
