const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../apps/boss-miniapp/native/pages/announcement-detail/index.js');
const source = fs.readFileSync(filename, 'utf8');
const mediaBase = 'https://media.xa.test';

async function fetchContent(content) {
  const item = { id: 23, title: '公告正文测试', created_at: '2026-10-19T12:30:00Z', is_pinned: true, content };
  const requests = [], richTextCalls = [];
  let definition;
  const mocks = {
    '../../core/api': { async request(url) { requests.push(url); return item; } },
    '../../core/auth': {},
    '../../core/ui': {
      date(value) { assert.equal(value, item.created_at); return '2026-10-19 12:30'; },
      richText(value) {
        richTextCalls.push(value);
        return String(value || '').replace(/(["'])\/media\/club-catalog\//g, '$1' + mediaBase + '/media/club-catalog/');
      },
    },
    '../../core/page': { privatePage(value) { return value; } },
  };
  vm.runInNewContext(source, {
    Page(value) { definition = value; },
    require(name) { assert.ok(Object.hasOwn(mocks, name), 'unexpected dependency: ' + name); return mocks[name]; },
  }, { filename });
  assert.equal(typeof definition.fetch, 'function');
  const page = { ...definition };
  page.init({ id: '23' });
  const patch = await page.fetch();
  assert.deepEqual(requests, ['/announcements/23/']);
  assert.deepEqual(richTextCalls, [content], 'the existing rich-text conversion still receives the authored content');
  assert.equal(item.content, content, 'formatting must not mutate the API result');
  assert.equal(patch.item.id, item.id);
  assert.equal(patch.item.title, item.title);
  assert.equal(patch.item.is_pinned, item.is_pinned);
  assert.equal(patch.item.dateText, '2026-10-19 12:30');
  return patch.item.content;
}

function assertResponsiveImages(html, count = 1) {
  const styles = [...html.matchAll(/\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi)]
    .map(match => match[1] ?? match[2] ?? match[3])
    .filter(style => /(?:^|;)\s*max-width\s*:/i.test(style) && /(?:^|;)\s*height\s*:/i.test(style));
  assert.equal(styles.length, count, 'every image must have its own responsive style');
  for (const style of styles) {
    const last = property => [...style.matchAll(new RegExp('(?:^|;)\\s*' + property + '\\s*:\\s*([^;]*)', 'gi'))]
      .at(-1)?.[1].trim().replace(/\s+/g, ' ');
    assert.equal(last('max-width'), '100% !important', 'the last width constraint fits the reading area');
    assert.equal(last('height'), 'auto !important', 'the last height constraint preserves the image aspect ratio');
  }
}

test('announcement text and HTML without images remain unchanged', async () => {
  for (const content of [
    '第一段纯文本\n第二段内容',
    '<h2>服务安排</h2><p>请阅读<strong>重要说明</strong>。</p><ul><li>保持原有格式</li></ul>',
    '<p style="max-width:70%;height:20px">非图片内联格式</p>',
  ]) assert.equal(await fetchContent(content), content);
});

test('an image without a style gains responsive dimensions after media URLs are resolved', async () => {
  const html = await fetchContent('<p>开始</p><img src="/media/club-catalog/poster.webp" alt="活动海报"/><p>结束</p>');
  assertResponsiveImages(html);
  assert.ok(html.startsWith('<p>开始</p><img '));
  assert.ok(html.includes('src="' + mediaBase + '/media/club-catalog/poster.webp"'));
  assert.ok(html.includes('alt="活动海报"/>'));
  assert.ok(html.endsWith('<p>结束</p>'));
});

test('double-quoted image styles retain authored declarations before responsive limits', async () => {
  const authored = "width:1400px;height:600px;border-radius:12px;background-image:url('poster.png')";
  const html = await fetchContent('<img src="poster.webp" style="' + authored + '" alt="双引号样式">');
  assertResponsiveImages(html);
  assert.ok(html.includes('style="' + authored + ';'));
  assert.ok(html.includes('src="poster.webp"'));
  assert.ok(html.includes('alt="双引号样式">'));
});

test('single-quoted image styles retain their quoting and authored declarations', async () => {
  const authored = 'width:1400px;height:600px;font-family:"XA Sans"';
  const html = await fetchContent("<img src='poster.webp' style='" + authored + "' alt='单引号样式'>");
  assertResponsiveImages(html);
  assert.ok(html.includes("style='" + authored + ';'));
  assert.ok(html.includes("src='poster.webp'"));
  assert.ok(html.includes("alt='单引号样式'>"));
});

test('an unquoted image style keeps its declarations when responsive limits are appended', async () => {
  const html = await fetchContent('<img style=width:1400px;height:600px src="poster.webp" alt="无引号样式">');
  assertResponsiveImages(html);
  assert.match(html, /style=["']width:1400px;height:600px;/);
  assert.ok(html.includes('src="poster.webp"'));
  assert.ok(html.includes('alt="无引号样式">'));
});

test('spaces, opposite quotes, greater-than signs and style-like attribute text survive image formatting', async () => {
  const attributes = [
    'src="poster with spaces.webp"',
    'alt=\'角色 "兴安" > 示例\'',
    'title="保留 > 与空格"',
    'data-note="keep style=\'width:1px\' unchanged"',
  ];
  const html = await fetchContent('<p>前文</p><img ' + attributes.join(' ') + ' /><p>后文</p>');
  assertResponsiveImages(html);
  for (const attribute of attributes) assert.ok(html.includes(attribute), 'preserve attribute: ' + attribute);
  assert.ok(html.endsWith(' /><p>后文</p>'));
});

test('multiple images each gain limits while surrounding markup and uppercase attributes survive', async () => {
  const html = await fetchContent('<div><img src="first.webp"><p>图片之间</p><IMG SRC=\'second.webp\' STYLE=\'height:900px\'/><img src="third.webp" style="width:1600px"></div>');
  assertResponsiveImages(html, 3);
  assert.ok(html.includes('<p>图片之间</p>'));
  assert.ok(html.includes("<IMG SRC='second.webp' STYLE='height:900px;"));
  for (const name of ['first.webp', 'second.webp', 'third.webp']) assert.ok(html.includes(name));
  assert.ok(html.startsWith('<div>'));
  assert.ok(html.endsWith('</div>'));
});

test('responsive limits override earlier authored important width and height constraints', async () => {
  const authored = 'max-width:1800px !important;height:900px !important;object-fit:contain;';
  const html = await fetchContent('<img src="poster.webp" style="' + authored + '">');
  assertResponsiveImages(html);
  assert.ok(html.includes('style="' + authored));
});

test('empty announcement content remains empty after existing rich-text conversion', async () => {
  for (const content of ['', null, undefined]) assert.equal(await fetchContent(content), '');
});
