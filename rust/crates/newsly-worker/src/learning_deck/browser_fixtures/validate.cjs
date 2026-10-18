// Run with NODE_PATH pointing to an installed Playwright package. No network or sandbox calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const source = fs.readFileSync(path.join(__dirname, '../browser.rs'), 'utf8');
const script = source.match(/const VALIDATION_SCRIPT: &str = r"([\s\S]*?)";/)[1];
const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'newsly-browser-validation-'));
fs.mkdirSync(path.join(workspace, 'output'));
const html = mode => `<!doctype html><style>
body{margin:0}section{display:none;position:relative;width:720px;height:1280px}section.current{display:block}section>div{width:100px;height:100px}
@media(orientation:landscape){section{width:1280px;height:720px}}
${mode === 'overflow' ? 'section>div{position:absolute;left:1400px}' : ''}
</style><section id="first" class="current"><div>One</div></section><section id="second"><div>Two</div></section><script>
let h=0;const slides=Array.from(document.querySelectorAll('section'));
window.Reveal={isReady:()=>${mode === 'timeout' ? 'false' : 'true'},
getSlides:()=>slides,getCurrentSlide:()=>slides[h],getIndices:slide=>({h:slide?slides.indexOf(slide):h,v:0,f:-1}),
slide:index=>{h=index;slides.forEach((s,i)=>s.classList.toggle('current',i===h))},
next:()=>{${mode === 'navigation' ? '' : 'Reveal.slide(1)'}},prev:()=>Reveal.slide(0),
getConfig:()=>matchMedia('(orientation:portrait)').matches?{width:720,height:1280}:{width:1280,height:720}};
${mode === 'script' ? 'delete Reveal.getSlides;' : ''}
</script>`;
function run(mode, prelude = '') {
  fs.writeFileSync(path.join(workspace, 'output/.newsly-viewer-validation.html'), html(mode));
  fs.writeFileSync(path.join(workspace, 'validate.cjs'), prelude + script);
  const result = spawnSync(process.execPath, ['validate.cjs'], { cwd:workspace, encoding:'utf8', timeout:45000 });
  assert.equal(result.error, undefined, result.error?.message);
  const prefix = result.status === 0 ? 'NEWSLY_BROWSER_VALIDATION=' : 'NEWSLY_BROWSER_VALIDATION_FAILURE=';
  const line = `${result.stdout}\n${result.stderr}`.split('\n').find(line=>line.startsWith(prefix));
  assert.ok(line, `${mode}: ${result.stderr}`);
  return {status:result.status, report:JSON.parse(line.slice(prefix.length))};
}
try {
  assert.equal(run('good').status, 0);
  const overflow=run('overflow').report;
  assert.equal(overflow.repairable,true);
  assert.equal(overflow.phase,'layout_overflow');
  assert.ok(overflow.context.portrait.overflow_details.some(detail=>detail.slide==='first'&&detail.excess_pixels.right>0));
  const navigation=run('navigation').report;
  assert.equal(navigation.repairable,true);
  assert.equal(navigation.phase,'navigation');
  const scriptError=run('script').report;
  assert.equal(scriptError.repairable,false);
  assert.equal(scriptError.phase,'measurement');
  const launch=run('good', "require('playwright').chromium.launch=async()=>{throw new Error('platform unavailable')};\n").report;
  assert.equal(launch.repairable,false);
  assert.equal(launch.phase,'browser_launch');
  const fakeTimeout=run('good', `const playwright=require('playwright');const launch=playwright.chromium.launch.bind(playwright.chromium);playwright.chromium.launch=async options=>{const browser=await launch(options);const newPage=browser.newPage.bind(browser);browser.newPage=async options=>{const page=await newPage(options);page.waitForFunction=async()=>{throw Object.assign(new Error('Timeout 15000ms exceeded'),{name:'TimeoutError'})};return page};return browser};\n`).report;
  assert.equal(fakeTimeout.repairable,false);
  assert.equal(fakeTimeout.phase,'readiness');
  const readiness=run('timeout').report;
  assert.equal(readiness.repairable,true);
  assert.equal(readiness.phase,'readiness');
  console.log('7 local Chromium fixtures passed: valid, overflow diagnostics, navigation, script, platform, fake timeout, readiness timeout');
} finally { fs.rmSync(workspace,{recursive:true,force:true}); }
