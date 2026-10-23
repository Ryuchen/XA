const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../apps/boss-miniapp/native/pages/club-content/index.js');
const source = fs.readFileSync(filename, 'utf8');
const mediaBase = 'https://media.xa.test';

function presentContent(content) {
  let definition;
  const richTextCalls = [];
  const mocks = {
    '../../core/api': { request() { assert.fail('presentation must not request the server'); } },
    '../../core/ui': {
      image(value) { return value ? mediaBase + value : ''; },
      date(value) { return String(value || '').replace('T', ' ').slice(0, 16); },
      richText(value) {
        richTextCalls.push(value);
        return String(value || '').replace(/(["'])\/media\/club-catalog\//g, '$1' + mediaBase + '/media/club-catalog/');
      },
    },
  };
  vm.runInNewContext(source, {
    Page(value) { definition = value; },
    require(name) { assert.ok(Object.hasOwn(mocks, name), 'unexpected dependency: ' + name); return mocks[name]; },
  }, { filename });
  const row = { id: 990701, title: '俱乐部图文说明', kind: 'ORDER_RULES', created_at: '2026-10-19T12:30:00Z', image_url: '/media/cover.webp', content };
  const result = definition.present(row);
  assert.equal(row.content, content, 'presentation must not mutate the API row');
  assert.deepEqual(richTextCalls, [content], 'retain existing content URL conversion');
  for (const field of ['id', 'title', 'kind', 'created_at', 'image_url']) assert.equal(result[field], row[field]);
  assert.equal(result.image, mediaBase + '/media/cover.webp');
  assert.equal(result.date, '2026-10-19');
  return result.content;
}

function assertResponsiveImages(html, count = 1) {
  const styles = [...html.matchAll(/\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi)]
    .map(match => match[1] ?? match[2] ?? match[3])
    .filter(style => /(?:^|;)\s*max-width\s*:/i.test(style) && /(?:^|;)\s*height\s*:/i.test(style));
  assert.equal(styles.length, count, 'each image has responsive limits');
  for (const style of styles) {
    const last = property => [...style.matchAll(new RegExp('(?:^|;)\\s*' + property + '\\s*:\\s*([^;]*)', 'gi'))]
      .at(-1)?.[1].trim().replace(/\s+/g, ' ');
    assert.equal(last('max-width'), '100% !important');
    assert.equal(last('height'), 'auto !important');
  }
}

test('club text and non-image HTML keep their original content', () => {
  for (const content of [
    '第一段纯文本\n第二段内容',
    '<h2>俱乐部规范</h2><p>保留<strong>重点</strong>与列表。</p><ul><li>规则</li></ul>',
    '<p style="max-width:70%;height:20px">非图片样式</p>',
  ]) assert.equal(presentContent(content), content);
});

test('club image URLs are resolved and surrounding prose stays intact', () => {
  const html = presentContent('<p>开始</p><img src="/media/club-catalog/poster.webp" width="1400" height="2400" alt="活动海报"/><p>结束</p>');
  assertResponsiveImages(html);
  assert.ok(html.startsWith('<p>开始</p><img '));
  assert.ok(html.includes('src="' + mediaBase + '/media/club-catalog/poster.webp"'));
  assert.ok(html.includes('width="1400" height="2400" alt="活动海报"/>'));
  assert.ok(html.endsWith('<p>结束</p>'));
});

test('existing quoted and unquoted image styles keep authored declarations', () => {
  for (const [input, expected] of [
    ['<img src="poster.webp" style="width:1400px;height:900px;border-radius:8px">', 'style="width:1400px;height:900px;border-radius:8px;'],
    ["<img src='poster.webp' style='width:1400px;height:900px'>", "style='width:1400px;height:900px;"],
    ['<img style=width:1400px;height:900px src="poster.webp">', 'style="width:1400px;height:900px;'],
  ]) {
    const html = presentContent(input);
    assertResponsiveImages(html);
    assert.ok(html.includes(expected));
    assert.ok(html.includes('poster.webp'));
  }
});

test('responsive limits override earlier authored important declarations', () => {
  const authored = 'max-width:1800px !important;height:2400px !important;object-fit:contain;';
  const html = presentContent('<img src="poster.webp" style="' + authored + '">');
  assertResponsiveImages(html);
  assert.ok(html.includes('style="' + authored));
});

test('multiple club images retain uppercase tags, attributes and intervening prose', () => {
  const html = presentContent('<div><img src="first.webp"><p>图片之间</p><IMG SRC=\'second.webp\' STYLE=\'height:900px\'/><img src="third.webp" style="width:1600px"></div>');
  assertResponsiveImages(html, 3);
  assert.ok(html.includes('<p>图片之间</p>'));
  assert.ok(html.includes("<IMG SRC='second.webp' STYLE='height:900px;"));
  for (const name of ['first.webp', 'second.webp', 'third.webp']) assert.ok(html.includes(name));
  assert.ok(html.endsWith('</div>'));
});

test('quoted greater-than signs, opposite quotes and style-like attribute text survive', () => {
  const attributes = ['src="poster with spaces.webp"', 'alt=\'角色 "兴安" > 示例\'', 'title="保留 > 与空格"', 'data-note="keep style=\'width:1px\' unchanged"'];
  const html = presentContent('<p>前文</p><img ' + attributes.join(' ') + ' /><p>后文</p>');
  assertResponsiveImages(html);
  for (const attribute of attributes) assert.ok(html.includes(attribute));
  assert.ok(html.endsWith(' /><p>后文</p>'));
});

test('empty club content stays empty', () => {
  for (const content of ['', null, undefined]) assert.equal(presentContent(content), '');
});
