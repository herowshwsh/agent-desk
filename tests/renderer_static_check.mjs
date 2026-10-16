// 渲染层静态一致性检查：id、data 属性、样式类、残留旧代码。
// 运行：node tests/renderer_static_check.mjs

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(ROOT, 'renderer/index.html'), 'utf8');
const js = fs.readFileSync(path.join(ROOT, 'renderer/app.js'), 'utf8');
const css = fs.readFileSync(path.join(ROOT, 'renderer/styles.css'), 'utf8');

let failed = 0;
const report = (name, ok, extra = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${name}${extra}`);
  if (!ok) failed += 1;
};

// 1. app.js 里引用的所有 id 必须在 html 中存在
const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
const jsIds = new Set([...js.matchAll(/\$\('#([a-z0-9-]+)'\)/g)].map(m => m[1]));
const missing = [...jsIds].filter(id => !htmlIds.has(id));
report(`app.js 引用的 ${jsIds.size} 个 id 全部存在`, missing.length === 0, missing.length ? `缺少: ${missing}` : '');

// 2. data-* 属性对齐
const dataAttrs = [...new Set([...js.matchAll(/\[data-([a-z-]+)\]/g)].map(m => m[1]))];
const htmlData = new Set([...html.matchAll(/data-([a-z-]+)/g)].map(m => m[1]));
const missData = dataAttrs.filter(a => !htmlData.has(a));
report(`data 属性对齐（${dataAttrs.length} 个）`, missData.length === 0, missData.length ? `缺少: ${missData}` : '');

// 3. 模块化关键 class 是否都有样式
const needed = ['tabbar', 'module-tabs', 'module-tab', 'tab-icon', 'tab-label', 'ghost-button', 'no-inspector'];
const missCss = needed.filter(c => !css.includes(`.${c}`));
report('模块相关 class 均有样式', missCss.length === 0, missCss.length ? `缺少: ${missCss}` : '');

// 4. 三栏与两栏布局都要保留
report('三栏 grid 保留（inspector 存在时）', /grid-template-columns:\s*258px minmax\(580px, 1fr\) 288px;/.test(css));
report('有 no-inspector 两栏态', /\.no-inspector/.test(css));
report('tabbar 跨三列', /grid-column:\s*1 \/ -1/.test(css));

// 5. welcome-title 不应重复赋值
const assigns = [...js.matchAll(/welcomeTitle\.innerHTML/g)].length;
report('welcomeTitle 只赋值一次', assigns === 1, assigns === 1 ? '' : `实际 ${assigns} 次`);

// 6. 不应残留旧的单模块假设
report('旧 defaultSessions 已移除', !js.includes('defaultSessions'));
report('旧 sessionKey 版本已升级', /agentdesk\.sessions\.v3/.test(js));

// 7. 关键隔离机制
report('会话按模块隔离', /sessionsByModule/.test(js));
report('模型按模块记忆', /moduleModels/.test(js) && /moduleModelKey/.test(js));
report('在途请求按模块归属', /activeModuleOfRequest/.test(js) && /detachedRequests/.test(js));

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
