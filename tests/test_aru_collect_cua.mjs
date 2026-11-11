import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {openCollector} from '../scripts/aru_collect_cua.mjs';

async function fixture(t, change = d => d) {
  const dir = await mkdtemp(join(tmpdir(), 'aru-cua-test-'));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const reader = join(dir, 'reader.js');
  await writeFile(reader, '() => ({})');
  const category = {id:'/tools/', name:'Инструмент', url:'https://aru.ooo/tools/'};
  const tabs = Array.from({length:3}, () => {
    let url = category.url;
    return {
      goto: async value => {url = value;},
      playwright: {evaluate: async code => {
        if (typeof code === 'function') return code.toString().includes('data-view') ? 'list' : url;
        return change({authenticated:true, url, title:'Инструмент', categories:[category], children:[], pages:1,
          items:[{id:'42', name:'Дрель', url:'https://aru.ooo/tools/drill/', stock_text:'Нет в наличии', price_text:'100,01 ₽'}]});
      }},
    };
  });
  return {tabs, outputDir:join(dir, 'run'), readerPath:reader};
}

test('captures all observations and resumes without losing completed pages', async t => {
  const options = await fixture(t);
  let collector = await openCollector(options);
  await collector.batch(1);
  const saved = JSON.parse(await readFile(join(options.outputDir, 'pages/tools__001.json')));
  assert.equal(saved.items[0].stock_text, 'Нет в наличии');
  assert.ok(saved.captured_at.endsWith('Z'));
  collector = await openCollector({...options, resume:true});
  assert.equal(collector.status().done, 1);
  assert.equal((await collector.batch(3)).remaining, 0);
  await assert.rejects(openCollector(options), /explicitly resume/);
});

test('loss of authentication never completes a page', async t => {
  const options = await fixture(t);
  const collector = await openCollector(options);
  options.tabs[0].playwright.evaluate = async code => typeof code === 'function'
    ? code.toString().includes('data-view') ? 'list' : 'https://aru.ooo/tools/'
    : {authenticated:false,url:'https://aru.ooo/tools/',title:'Вход',items:[],pages:1};
  assert.equal((await collector.batch(1)).status, 'mismatch');
  assert.equal(collector.status().done, 0);
});

test('empty paginated capture is rejected without advancing the queue', async t => {
  const options = await fixture(t, d => ({...d, pages:2, items:[]}));
  const collector = await openCollector(options);
  await assert.rejects(collector.batch(1), /Empty paginated/);
  assert.equal(collector.status().done, 0);
});

test('canonical category prefixes are enqueued even when absent from menu', async t => {
  const options = await fixture(t, d => ({...d,items:[{...d.items[0],url:'https://aru.ooo/tools/drills/cordless/drill/'}]}));
  const collector = await openCollector(options);
  await collector.batch(1);
  const menu = JSON.parse(await readFile(join(options.outputDir,'categories-menu.json')));
  assert.ok(menu.some(c => c.id === '/tools/drills/'));
  assert.ok(menu.some(c => c.id === '/tools/drills/cordless/'));
});

test('resume rejects stale saved captures', async t => {
  const options = await fixture(t);
  const collector = await openCollector(options);
  await collector.batch(1);
  const path = join(options.outputDir,'pages/tools__001.json');
  const data = JSON.parse(await readFile(path));
  data.captured_at = '2000-01-01T00:00:00Z';
  await writeFile(path,JSON.stringify(data));
  await assert.rejects(openCollector({...options,resume:true}), /stale saved page/);
});
